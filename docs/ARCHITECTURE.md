# AutoContent AI — Architecture

AutoContent AI is a local-first automation platform. It learns about your software
projects, plans short promotional videos (using Claude only where it adds value), renders
them on your PC with ComfyUI + FFmpeg, and publishes them on per-account schedules.

This document covers the major decisions. For setup see `README.md`; for platform
publishing specifics see `docs/PUBLISHING.md`; for ComfyUI see `docs/COMFYUI.md`.

---

## 1. Major decisions

| Decision | Choice | Why |
|---|---|---|
| Process model | **One Python process** (FastAPI + embedded worker threads + APScheduler). The worker can also run separately (`python -m app.worker`). | No Redis/Celery for a single PC. Separation stays possible because the queue lives in the DB. |
| Database | SQLite (WAL mode) through SQLAlchemy 2 + Alembic. PostgreSQL works by changing `DATABASE_URL`. | Zero-ops local default. |
| Job queue | DB table `scheduled_jobs` with leases, an idempotency key, retries and exponential backoff. | Survives restarts. Duplicate enqueues are rejected by a unique constraint. |
| Scheduler | APScheduler is only a **clock** (a tick every 60 s, daily analysis, metrics, token refresh, backups). The DB holds the actual schedule: `publishing_rules` become `publication_records` with a unique `(rule, slot)` key. | A reboot cannot duplicate a slot or a post. Missed-run handling is a pure function of DB state. |
| AI | Official `anthropic` SDK, structured JSON output (`output_config.format`), Pydantic validation, prompt caching, budget gate before every call. | Bounded, auditable token use. Claude output is untrusted data. |
| Local fallbacks | Deterministic template planner, heuristic change classifier, local card renderer. | The system makes real videos with no API key, an exhausted budget, or ComfyUI offline. |
| Video | ComfyUI (HTTP + WebSocket) for scene visuals, FFmpeg for motion, assembly, captions and audio. Pillow draws text overlays and captions as PNGs. | No dependence on libass or font configuration. Works with any modern FFmpeg. |
| FFmpeg binary | Configured path → bundled `imageio-ffmpeg` (modern static build) → `PATH`. | The PATH ffmpeg on many machines is ancient (this one is from 2013). |
| TTS | Windows SAPI via `pyttsx3` in a subprocess (isolates COM). Optional Piper. Silent track as the last resort. | Offline and free. |
| Captions | Timed from the script and the measured narration length, not from Whisper. | We already know the words, so exact text at zero cost. |
| Secrets | A random master key in the OS keychain (`keyring`: Windows Credential Manager / Secret Service). Secret values are encrypted with Fernet in the `secret_values` table. | Keychain entries are size-limited, but OAuth token sets are large. Secrets never reach the frontend. |
| Auth | Local admin account (scrypt hash) and a signed session cookie (httpOnly, SameSite). Mutating requests require an `X-Requested-With` header (CSRF defence). | Simple and safe for local and tunnelled use. |
| Frontend | Vite + React + TypeScript + Tailwind v4, shadcn-style components (hand-copied, as shadcn intends), TanStack Query, Recharts. | Fast to build and to maintain. |

The project layout is simplified relative to the original brief. Workers live inside the
backend package (`backend/app/workers`) so that they share models and services without
import gymnastics.

```
backend/app/
  api/            FastAPI routers (one per dashboard section)
  core/           config, db, security (auth, secrets, safe paths), logging
  models.py       SQLAlchemy models (all tables)
  schemas/        Pydantic API + AI contract schemas
  services/
    knowledge/    scanner, git/GitHub/GitLab readers, change classifier, profile
    ai/           Claude client, prompts, budget, local planner
    content/      topic selection, dedup/fingerprints, claim checks, planner
    video/        comfyui client, renderer (ffmpeg), tts, captions, QC
    publishing/   adapter base, youtube, instagram, tiktok, export, oauth
    scheduling/   slot computation, materializer, missed-run policy
  workers/        queue, runner (lanes + leases), handlers, scheduler clock
frontend/         React dashboard
workflows/comfyui ComfyUI API-format workflows + node mappings
storage/          projects, generated_assets, videos, thumbnails, exports, logs, backups
scripts/          install/start/stop (Windows + Linux), task scheduler, systemd
```

---

## 2. Deployment model

### Mode A: fully local (default)
```
Browser ──http://127.0.0.1:8000──▶ FastAPI (serves built dashboard + /api)
                                     ├─ Worker threads (lanes: default ×2, gpu ×1)
                                     ├─ APScheduler clock
                                     ├─ SQLite (storage/autocontent.db)
                                     ├─ ComfyUI  http://127.0.0.1:8188 (never exposed)
                                     └─ FFmpeg / TTS subprocesses
Outbound only: api.anthropic.com, YouTube/Meta/TikTok APIs, GitHub/GitLab APIs
```

### Mode B: secure remote dashboard (extension)
The automation core stays on the PC. Remote access works through an **outbound** tunnel
(Cloudflare Tunnel, Tailscale Funnel/Serve, or similar) that terminates TLS and forwards
only to the FastAPI port. ComfyUI, the DB and the secrets are never reachable from outside.
- Set `AUTOCONTENT_PUBLIC_BASE_URL=https://your-host`, `AUTOCONTENT_COOKIE_SECURE=true` and
  `AUTOCONTENT_CORS_ORIGINS` (only needed if the static dashboard is hosted on a separate
  origin).
- Scheduling, rendering and publishing keep running when nobody is connected.
- Planned for a later release: a relay protocol for dashboards hosted on infrastructure
  without a tunnel to the PC. The local agent would long-poll an authenticated
  `/agent/commands` endpoint and push status snapshots. The DB queue already holds every
  command, so this is a transport addition and not a redesign. *Status: designed, not
  implemented.*

---

## 3. Database schema (summary)

All timestamps are UTC. Key constraints are listed. See `backend/app/models.py`.

| Table | Purpose | Key constraints / indexes |
|---|---|---|
| users | local accounts | unique username |
| applications | registered software products, strategy, video and budget overrides | unique slug |
| application_sources | folders, git repos, GitHub/GitLab, docs, changelog, manual | idx application_id |
| application_assets | logos, screenshots, demo clips, licensed music, intro/outro | idx application_id, kind |
| application_knowledge | Project Knowledge Profile (facts with stable IDs), versioned | unique application_id |
| project_snapshots | per-scan manifest (commit, file hashes, doc hashes) | idx (source_id, created_at) |
| project_change_events | classified commits, doc or release changes with significance | idx (application_id, processed) |
| social_accounts | platform account per application | idx application_id |
| oauth_connections | token reference (secret name), scopes, expiry | unique social_account_id |
| oauth_states | short-lived OAuth state + PKCE verifier reference | PK state |
| publishing_rules | frequency, times, windows, tz, blackout, missed policy | idx social_account_id |
| publication_records | one row per planned post slot | **unique (publishing_rule_id, scheduled_for)**, **unique idempotency_key** |
| scheduled_jobs | DB job queue | **unique idempotency_key**, idx (status, run_at) |
| job_attempts | each execution attempt (error, traceback, duration) | idx job_id |
| log_entries | execution logs per job/app | idx (job_id), (created_at) |
| content_ideas | topic + fingerprint + referenced fact IDs | idx (application_id, fingerprint) |
| video_scripts | hook, narration, CTA, per-platform social copy, claims | idx content_idea_id |
| storyboards | validated scene list (versioned) | idx video_script_id |
| generated_assets | per-scene images, clips, audio, provenance (workflow, seed) | idx storyboard_id |
| rendered_videos | final MP4, QC report, sha256, review status | idx application_id, unique sha256 per app |
| content_performance | metrics snapshots (NULL = unavailable, never invented) | idx publication_record_id |
| comfyui_workflows | API-format workflow, node mappings, validation report | |
| ai_usage_records | tokens (in/out/cache read/write), cost, model, job | idx (created_at), (application_id) |
| system_settings | key/value JSON settings | PK key |
| secret_values | Fernet-encrypted secrets (API keys, OAuth tokens) | PK name |
| audit_logs | who did what (secrets never logged) | idx created_at |
| notifications | failure alerts surfaced in the dashboard | idx read |

---

## 4. Job lifecycle and scheduling

### Job states
```
queued ──claim──▶ running ──ok──▶ succeeded
   ▲                 │
   │                 ├─retryable error (attempts < max)──▶ retry_wait ──(run_at reached)──▶ queued
   │                 ├─budget exceeded / dependency down──▶ blocked  (resumes when cleared)
   │                 └─fatal / attempts exhausted──▶ failed  (+ notification; never discarded)
   └── cancelled (manual)
```
- **Claiming** uses a conditional `UPDATE ... WHERE status='queued'` (compare-and-set) plus a
  lease (`lease_expires_at`), extended by a heartbeat.
- **Backoff:** `base * 2^(attempt-1)` with jitter, capped (default 30 s → 1 h).
- **Recovery on startup:** a `running` job whose lease has expired is requeued (or failed if
  out of attempts). A **publish** job interrupted mid-flight is **not** retried blindly. Its
  publication moves to `verify_required`, and the adapter's `verify()` checks the platform
  using the IDs saved before the final publish call. If verification is impossible, the
  item waits for a human. **This is what prevents duplicate posts after a crash or reboot.**
- **Lanes:** `default` (analysis, planning, publishing, metrics) runs 2 concurrent jobs.
  `gpu` (rendering) runs 1. Both are configurable.

### Scheduling pipeline (every tick, 60 s)
1. **Materialize:** for every active rule (account not paused, application active),
   `compute_slots(rule, now, horizon)` produces UTC datetimes from daily or weekly counts,
   explicit times or evenly distributed windows, the per-account timezone, days of week,
   campaign start/end dates, blackout periods and the max per day. Each slot is inserted as a
   `publication_record` with `ON CONFLICT DO NOTHING` semantics.
2. **Prepare content:** a record within `generation_lead_hours` of its slot with no video
   gets a `prepare_content` job (key `prepare:{pub_id}`). If the rule allows reuse and the
   same application already has an approved/ready video from the last 48 h not yet used on
   that platform, the job attaches it with **no AI call**. Otherwise it plans (AI or local)
   and enqueues `render_video` (key `render:{idea_id}`).
3. **Approval:** a rendered video passes QC. Then, by publishing mode:
   manual (video and post need approval) / semi (post needs approval) / automatic (approved
   when QC is clean and the account supports direct publishing).
4. **Publish:** an approved record whose `scheduled_for <= now` gets a `publish` job (key
   `publish:{pub_id}`). The handler moves the record `approved → publishing` with a
   compare-and-set first, so two workers can never publish the same record.
5. **Missed slots** (PC off): policy per rule. `skip` marks the slot `missed`;
   `publish_late` publishes if within `grace_minutes`, otherwise marks it missed;
   `next_slot` marks it missed and releases the video back to the pool for the next slot.

Generation time is separate from publishing time. Content is prepared `generation_lead_hours`
(default 12) before the slot.

---

## 5. Claude context-reduction strategy

1. **Local preprocessing first.** File walking, manifest hashing, git log/diff stats,
   commit classification (conventional-commit prefixes + keyword heuristics) and doc
   diffing (only added or changed sections) all run in Python.
2. **Knowledge Profile caching.** One profile per application. It contains a list of
   **facts with stable IDs** (`F001`, …) with kind, status (released/beta/planned),
   source, and `times_featured` / `last_featured_at`.
3. **Incremental updates.** A Claude call happens only when new change events reach
   the significance threshold. Claude receives the compact profile and a bounded change
   digest (default 6,000 characters), and returns a *delta* that is merged locally.
4. **Single structured call per video.** Concept, hook, script, storyboard, scene prompts,
   and per-platform captions, titles and hashtags all come from one request. Claude receives
   only the facts relevant to the chosen topic plus a short avoid-list of recent topics.
5. **Prompt caching.** The frozen system prompt and the per-app profile block come first,
   with `cache_control`. Volatile task details come after the breakpoint.
6. **Call skipping.** No changes means no profile call. A reusable video or an unused plan
   in the backlog means no planning call. Cost-saving mode or an exhausted budget routes
   to the local template planner.
7. **Budgets** (global and per app): daily calls, monthly tokens, monthly USD, max tokens
   per job, max retries. These are checked **before** every call. When a limit is hit, the
   AI-dependent jobs go to `blocked` with the reason and resume automatically when the
   window rolls over.
8. **No agent loops.** The code calls Claude in fixed places, at most once per purpose
   per job, plus at most `max_retries` validation/dedup retries.
9. **Usage tracking** records input/output/cache-read/cache-write tokens, estimated cost,
   model, purpose, job and application, plus the request ID.

Claude output is validated with Pydantic, claims are checked against fact IDs, and the
output is never executed.

---

## 6. ComfyUI integration contract

A workflow is stored in **API format** (`File → Export (API)`). Each workflow has a
**mapping** that tells the backend where to inject values:

```json
{
  "positive_prompt": {"node": "6", "input": "text"},
  "negative_prompt": {"node": "7", "input": "text"},
  "seed":            {"node": "3", "input": "seed"},
  "width":           {"node": "5", "input": "width"},
  "height":          {"node": "5", "input": "height"},
  "input_image":     {"node": "12", "input": "image"},
  "length":          {"node": "20", "input": "length"},
  "filename_prefix": {"node": "9", "input": "filename_prefix"}
}
```
- **Validation** against the live `/object_info`: every `class_type` must exist, every
  mapped input must exist on its node, and every model-file choice (checkpoint, UNet, VAE,
  CLIP, LoRA) must be in the server's allowed list. The report names the missing pieces.
- **Execution:** upload input images (`/upload/image`), `POST /prompt` with a `client_id`,
  track progress over WebSocket (`/ws?clientId=`) with `/history/{id}` polling as fallback,
  enforce a timeout (cancel through `/queue` delete or `/interrupt`), then download outputs
  through `/view`.
- **Kinds:** `txt2img`, `img2vid`, `txt2vid`. Image outputs get Ken-Burns motion in FFmpeg.
  Video outputs are scaled/cropped to 9:16 and looped or trimmed.
- **Fallback:** if ComfyUI is unreachable or a workflow is invalid, the scene uses the
  application screenshot (blurred-background composition) or a branded title card, and the
  job log records the actionable error.

---

## 7. Social publishing adapter boundaries

```python
class PlatformAdapter:
    platform: str
    def capabilities(self, account) -> Capabilities      # direct publish? needs public URL? limits
    def authorization_url(self, state, code_challenge, redirect_uri) -> str
    def exchange_code(self, code, code_verifier, redirect_uri) -> TokenSet
    def refresh(self, tokens) -> TokenSet
    def account_info(self, tokens) -> dict
    def preflight(self, video, post) -> list[str]        # platform-specific validation
    def publish(self, ctx: PublishContext) -> PublishResult   # ctx.save_state() persists ids early
    def verify(self, ctx) -> PublishResult | None        # used after crashes
    def fetch_metrics(self, tokens, external_id) -> Metrics  # None fields = unavailable
```
Adapters: `youtube` (resumable upload, YouTube Data API v3), `instagram` (Reels container →
optional resumable upload → poll → `media_publish`), `tiktok` (Content Posting API: creator
info → init FILE_UPLOAD → chunked PUT → status fetch), and `export` (writes MP4 + caption
files for manual upload). The scheduler, planner and renderer know only this interface.
Adapters receive an injected `httpx.Client`, so tests use `httpx.MockTransport`.

---

## 8. Security model (summary)

- Secrets: master key in the OS keychain, values Fernet-encrypted in the DB. API responses
  show only `configured: true` plus the last 4 characters. A log filter redacts tokens,
  keys and `Authorization` headers.
- Project access: a source path must be inside a configured **allowed root**. Paths are
  resolved (symlinks included) and checked with `is_relative_to`. Upload and download
  endpoints resolve storage paths the same way, which blocks path traversal.
- Repositories are read, never executed. Git runs read-only commands with
  `core.fsmonitor=false`, `core.hooksPath` pointed at an empty dir, `--no-ext-diff` and
  `--no-textconv`. Repo text is wrapped as untrusted data in prompts.
- Claude output is parsed as JSON, schema-validated, and length-limited. File references
  in it are resolved only against the application's own asset IDs, never as paths.
- OAuth uses a random `state` (single use, 15 min TTL) and PKCE where supported. Tokens are
  refreshed proactively and can be disconnected or rotated from the dashboard.
