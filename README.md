# AutoContent AI

A local-first studio that keeps your software products on social media. It tracks what each
application does, notices what changed, plans short vertical videos (spending Claude tokens only
where they add value), renders them on your PC with **ComfyUI + FFmpeg**, and publishes them to
**Instagram Reels, TikTok and YouTube Shorts** on per-account schedules. Everything is run from one
web dashboard.

```
Add application → connect source → analyze → strategy → connect accounts → schedule
      → videos generated ahead of time → review or auto-publish → results & analytics
```

## Quick start (Windows 11)

Prerequisites: Python 3.11+, Node.js 20+, Git. ComfyUI is optional but recommended (default `http://127.0.0.1:8188`).
FFmpeg is **bundled** through `imageio-ffmpeg`. An outdated `ffmpeg` on PATH is ignored.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```
```powershell
powershell -File scripts\start.ps1
```
Then open http://127.0.0.1:8000:
1. Create the administrator account.
2. The **first-run wizard** checks the backend, database, Claude, ComfyUI, FFmpeg, GPU, storage and
   OAuth, and asks for an allowed project folder (e.g. `D:\Code`), an optional Anthropic API key, and your timezone.
3. Applications → **Add application** → Sources → add the repo → **Analyze** → **Generate video**.

Start automatically at logon: `scripts\register-task.ps1`. Stop: `scripts\stop.ps1`.
Linux: `bash scripts/install.sh && bash scripts/start.sh` (systemd unit in `scripts/`).

Without an API key everything still works: the deterministic local planner builds profiles and
scripts from your verified facts. Without ComfyUI, scenes use screenshot compositions and branded
title cards.

## Documentation
- `docs/ARCHITECTURE.md`: decisions, deployment modes, schema, job lifecycle, Claude token strategy,
  ComfyUI contract, adapter boundaries
- `docs/PUBLISHING.md`: per-platform setup, scopes, limits and what is verified
- `docs/COMFYUI.md`: bundled workflows, importing yours, node mappings, failure behaviour
- `docs/OPERATIONS.md`: run modes, recovery guarantees, backups, Mode B tunnel, security checklist

## Development
```powershell
cd backend; .venv\Scripts\python -m pytest -q        # 31 tests: acceptance scenarios 1-10 + units
```
```powershell
powershell -File scripts\start.ps1 -Dev               # API on :8000 + Vite on :5173 with hot reload
```
Backend: FastAPI, SQLAlchemy 2, Alembic, APScheduler, Pydantic, `anthropic` SDK (Python 3.11+).
Frontend: React 18, Vite, TypeScript, Tailwind 4, shadcn-style components, TanStack Query, Recharts.

## Implementation checklist

Legend: ✅ done and tested · ☑️ done, verified manually · 🟡 implemented, not verified against the real service · ⛔ not implemented

| Area | Status | Notes |
|---|---|---|
| FastAPI backend, SQLite + Alembic migration (27 tables), settings | ✅ | |
| Local auth (scrypt, signed httpOnly cookie, CSRF header, login rate limit) | ✅ | |
| Encrypted secrets (OS keychain master key + Fernet), log redaction | ✅ | never returned to the frontend |
| DB job queue: leases, idempotency keys, backoff, blocked state, crash recovery | ✅ | Scenario 5 |
| Scheduler: daily/weekly, times or windows, tz/DST, blackout, campaigns, max/day, missed policy | ✅ | Scenario 4 + unit tests |
| Project scanning (local folder/git, GitHub, GitLab, docs, changelog, manual notes) | ✅ local · 🟡 GitHub/GitLab | hosted APIs implemented, not run against a live repo |
| Incremental change detection + local classifier; AI call skipping | ✅ | Scenario 2 |
| Knowledge profile (facts with stable IDs), editable in UI | ✅ ☑️ | |
| Claude adapter: structured outputs, prompt caching, budgets, usage/cost, refusal fallback | ✅ (mocked) · 🟡 live | not called live (no API key in this environment) |
| Local template planner, topic rotation, repetition checks, claim verification | ✅ | Scenarios 3, 8 |
| ComfyUI: connect, import, validate vs `/object_info`, submit, WS progress, cancel, download | ✅ ☑️ | Qwen-Image workflow run on the live server |
| Renderer: Ken Burns, screenshots, title cards, SAPI narration, timed captions, logo, music, intro/outro, QC | ✅ ☑️ | 1080×1920 H.264/AAC MP4 verified |
| ComfyUI-down fallback with actionable error | ✅ | Scenario 9 |
| Publishing adapters: YouTube, Instagram (2 variants), TikTok (direct/inbox), manual export | ✅ (mocked) · 🟡 live | needs your developer apps + OAuth |
| OAuth (state + PKCE, single-use), token refresh/rotation, disconnect | ✅ (mocked) | Scenario 6 |
| Duplicate-post prevention (CAS, persisted platform IDs, verify, `verify_required`) | ✅ | Scenarios 5, 8 |
| Approval modes manual / semi / auto + review UI | ✅ ☑️ | Scenario 10 |
| Engagement metrics from official APIs (null = unavailable, never invented) | 🟡 | |
| Dashboard: all 13 sections, setup wizard, dark/light, notifications | ✅ ☑️ | |
| Backups (daily + on demand) and restore script | ✅ backup · ☑️ restore script written | |
| Install/start/stop scripts, Task Scheduler, systemd | ☑️ Windows scripts written · 🟡 Linux/Docker | Linux and Docker not run here |
| Mode B remote dashboard via HTTPS tunnel | 🟡 | config + docs; tunnel not exercised |
| Mode B relay protocol (dashboard host without a tunnel) | ⛔ | designed in ARCHITECTURE.md |
| Optional local LLM for cheap tasks | ⛔ | replaced by the deterministic classifier/planner |
| Whisper captions | ⛔ (by design) | captions come from the known script text instead |

## What needs you
1. **Anthropic API key** (Settings → AI). Then press *Test connection*.
2. **Developer apps** for YouTube (Google Cloud OAuth client), Instagram (Meta app + professional account)
   and TikTok (Content Posting API; posts stay private until TikTok audits the app). See `docs/PUBLISHING.md`.
3. **HTTPS tunnel** if you connect Meta/TikTok (their redirect URIs must be HTTPS) or use the remote dashboard.
4. Do a supervised **Publish now** per platform before you enable automatic mode.
