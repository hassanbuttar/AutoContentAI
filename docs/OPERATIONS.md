# Operations

## Run modes
| Command | What runs |
|---|---|
| `scripts\start.ps1` | API + dashboard + workers + scheduler in one process (default, Mode A) |
| `scripts\start.ps1 -Dev` | same + Vite dev server with hot reload on :5173 (proxies `/api`) |
| `python -m app.worker` | workers + scheduler only (pair with `AUTOCONTENT_RUN_WORKERS=false` on the API) |
| `scripts\register-task.ps1` | start hidden at Windows logon (Task Scheduler, restarts on failure) |
| `scripts/autocontent.service` | systemd user unit for Linux |

## Restart and recovery guarantees
- Schedules live in the DB. The APScheduler clock is rebuilt at start, so there is nothing to drift.
- On start, jobs leased by dead processes on this host are re-queued (other processes are untouched).
- Today's daily analysis runs at startup if the PC was off at the scheduled time.
- Slots missed while offline follow each rule's missed policy (`publish_late` within grace, `skip`, `next_slot`).
- A post interrupted mid-publish is verified with the platform, never blindly re-posted.

## Backups
The database is backed up daily at 03:15 (SQLite online backup API, 14 kept) to `storage/backups/`.
Settings → System → **Back up now**. Restore: `scripts\stop.ps1`, then
`scripts\restore.ps1 -Backup <file>`. Media files (`storage/videos`, `storage/projects`) are plain files;
include `storage/` in your normal file backup. The master key lives in the OS keychain
(`autocontent-ai/master-key`). Losing it makes stored secrets unreadable (re-enter them), not the data.

## Logs
- Execution logs and job attempts (with tracebacks) are in the DB, shown under Execution Logs.
- Process log: `storage/logs/autocontent.log` (rotating, 5×10 MB). Tokens and keys are redacted.

## Mode B: secure remote dashboard
The worker stays on your PC. Remote access goes through an **outbound** tunnel that terminates TLS:
```
cloudflared tunnel --url http://127.0.0.1:8000        # or: tailscale serve 8000
```
Then in `.env`:
```
AUTOCONTENT_PUBLIC_BASE_URL=https://<your-tunnel-host>
AUTOCONTENT_COOKIE_SECURE=true
```
- ComfyUI, the DB and the secrets never leave the PC. Only the authenticated dashboard/API is reachable.
- Use a strong admin password. Failed logins are rate-limited (8 per 5 minutes per IP).
- The same HTTPS base satisfies Meta/TikTok redirect requirements and Instagram Login's public `video_url`.
- Scheduling and publishing continue when nobody is connected.
- *Not implemented:* a relay protocol for dashboards hosted somewhere that can't reach the PC
  through a tunnel. The design is in `docs/ARCHITECTURE.md` §2.

### Private phone access with Tailscale (recommended)
Reachable only from devices signed in to your tailnet; nothing is exposed to the internet and the app
stays bound to `127.0.0.1`.
1. Install Tailscale on the PC and the phone; sign in to both with the same account (enable 2FA on it).
2. On the PC, run `tailscale serve --bg --https=443 http://127.0.0.1:8000`. The first time, it prints a
   `login.tailscale.com/f/serve?...` link: open it to allow Serve and HTTPS certificates on the tailnet,
   then run the command again.
3. Set `AUTOCONTENT_COOKIE_SECURE=true` in `.env` and restart (`scripts/stop.ps1`, then the
   "AutoContent AI" scheduled task or `scripts/start.ps1 -Hidden`). Browsers still accept the secure
   cookie on `http://localhost`.
4. On the phone, open `https://<pc-name>.<tailnet>.ts.net` with Tailscale switched on.

Serve config survives reboots (the Tailscale service starts automatically). Only port 443 → 8000 is
served; ComfyUI (8188) is not. Check with `tailscale serve status`; remove with
`tailscale serve --https=443 off`. Do **not** set `AUTOCONTENT_PUBLIC_BASE_URL` to the `ts.net` address:
Meta/TikTok/Google cannot reach it, so OAuth redirects and Instagram Login's `video_url` must use a
public tunnel instead. A lost phone: remove it in the Tailscale admin console.

## Security checklist
- Dashboard login is required for every API except health and the OAuth callback (which is
  validated by a single-use, 15-minute `state`).
- State-changing requests need the `X-Requested-With: autocontent` header (CSRF defence). CSP and
  frame-deny headers are set.
- Project sources must sit inside allowed folders, set in Settings → Security (drive roots are rejected).
- Repos are read-only. Git runs with hooks, fsmonitor, external diff and textconv disabled. No project code runs.
- Claude output is validated JSON. It references assets only by ID and is never executed.
- Uploads are type-checked (and images decoded), size-capped, renamed, and stored inside `storage/`.
