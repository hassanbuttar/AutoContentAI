"""System status (setup wizard), dashboard overview, notifications, backups."""
from __future__ import annotations

import shutil
from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy import func, select, text

from app.api.deps import AuthUser, Depends, Session, User, get_db, iso, ser_job, ser_pub
from app.core import secrets
from app.core.config import get_settings
from app.models import (AIUsageRecord, Application, Notification, PublicationRecord, RenderedVideo, ScheduledJob,
                        SocialAccount, utcnow)
from app.services.ai.budget import budget_status
from app.services.ai.client import API_KEY_SECRET, connectivity_test
from app.services.settings_service import get_section, update_section
from app.services.video import ffmpeg
from app.services.video.comfyui import ComfyClient, ComfyError, summarize_stats

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
def health():
    return {"ok": True}


def _check(name: str, ok: bool, detail: str, action: str = "", level: str | None = None) -> dict:
    return {"name": name, "ok": ok, "level": level or ("ok" if ok else "error"), "detail": detail, "action": action}


@router.get("/status")
def system_status(deep: bool = False, user: User = AuthUser, db: Session = Depends(get_db)):
    s = get_settings()
    checks = [_check("backend", True, f"FastAPI running ({s.env})")]
    try:
        db.execute(text("SELECT 1"))
        checks.append(_check("database", True, s.db_url.split("///")[-1] if s.db_url.startswith("sqlite") else "connected"))
    except Exception as exc:  # pragma: no cover
        checks.append(_check("database", False, str(exc), "Check AUTOCONTENT_DATABASE_URL"))
    ai = get_section(db, "ai")
    has_key = secrets.secret_status(db, API_KEY_SECRET)["configured"]
    if not has_key:
        checks.append(_check("claude", False, "No API key configured; the local template planner will be used",
                             "Settings → AI → add your Anthropic API key", "warning"))
    elif deep:
        t = connectivity_test(db)
        checks.append(_check("claude", t["ok"], f"Model {t.get('model')} reachable" if t["ok"] else t.get("error", ""),
                             "" if t["ok"] else "Verify the API key and model name in Settings → AI"))
    else:
        checks.append(_check("claude", True, f"API key configured; model {ai['model']}"))
    comfy = get_section(db, "comfyui")
    gpu = None
    if comfy.get("enabled"):
        try:
            c = ComfyClient(comfy["url"], timeout=5)
            st = summarize_stats(c.system_stats())
            q = c.queue_state()
            gpu = st
            checks.append(_check("comfyui", True, f"ComfyUI {st['version']} at {comfy['url']} — queue {q['running']} running / {q['pending']} pending"))
        except ComfyError as exc:
            checks.append(_check("comfyui", False, str(exc), exc.actionable + " The local fallback renderer will be used.", "warning"))
        except Exception as exc:
            checks.append(_check("comfyui", False, f"{type(exc).__name__}: {exc}", "Check the ComfyUI URL", "warning"))
    else:
        checks.append(_check("comfyui", True, "Disabled — using local fallback renderer", level="warning"))
    cap = ffmpeg.capabilities()
    checks.append(_check("ffmpeg", cap["ok"], f"{cap.get('version', '')} ({cap.get('source', '')})" if cap["ok"] else cap.get("error", ""),
                         "Run scripts/install to install backend requirements (bundled FFmpeg)"))
    if gpu:
        checks.append(_check("gpu", True, f"{gpu['gpu']} — {gpu['vram_free_gb']} / {gpu['vram_total_gb']} GB VRAM free"))
    else:
        nvsmi = shutil.which("nvidia-smi")
        checks.append(_check("gpu", bool(nvsmi), "NVIDIA driver detected (ComfyUI not reachable)" if nvsmi else "No GPU info (ComfyUI offline)",
                             "GPU is only needed for ComfyUI", "warning"))
    try:
        free = shutil.disk_usage(s.storage_dir).free / 1e9
        writable = (s.storage_dir / "videos").exists()
        checks.append(_check("storage", writable and free > 2, f"{s.storage_dir} — {free:.1f} GB free",
                             "Free disk space or set AUTOCONTENT_STORAGE_DIR"))
    except OSError as exc:
        checks.append(_check("storage", False, str(exc)))
    roots = get_section(db, "security")["allowed_project_roots"]
    checks.append(_check("project_folders", bool(roots), f"{len(roots)} allowed project folder(s)" if roots else "No allowed project folders",
                         "Settings → Security → add the folders that contain your projects", "ok" if roots else "warning"))
    oauth = get_section(db, "oauth")
    missing = [p for p in ("youtube", "instagram", "tiktok")
               if not ((oauth.get(p) or {}).get("client_id") or (oauth.get(p) or {}).get("client_key"))
               or not secrets.secret_status(db, f"oauth.{p}.client_secret")["configured"]]
    checks.append(_check("oauth", not missing, "All platform OAuth clients configured" if not missing else
                         f"Not configured: {', '.join(missing)} (manual export still works)",
                         "Settings → Social platforms (see docs/PUBLISHING.md)", "ok" if not missing else "warning"))
    from app.workers import clock

    return {"checks": checks, "setup_completed": get_section(db, "general")["setup_completed"],
            "key_storage": secrets.key_source(), "scheduler": clock.last_tick(),
            "ready": all(c["ok"] or c["level"] == "warning" for c in checks)}


@router.post("/setup-complete")
def setup_complete(user: User = AuthUser, db: Session = Depends(get_db)):
    update_section(db, "general", {"setup_completed": True})
    db.commit()
    return {"ok": True}


@router.get("/overview")
def overview(user: User = AuthUser, db: Session = Depends(get_db)):
    now = utcnow()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    def count(model, *where):
        return db.scalar(select(func.count()).select_from(model).where(*where)) or 0
    stats = {
        "applications": count(Application),
        "active_applications": count(Application, Application.active.is_(True)),
        "connected_accounts": count(SocialAccount, SocialAccount.status.in_(["connected", "export_only"])),
        "accounts_needing_attention": count(SocialAccount, SocialAccount.status.in_(["needs_reauth", "error"])),
        "scheduled_today": count(PublicationRecord, PublicationRecord.scheduled_for >= day,
                                 PublicationRecord.scheduled_for < day + timedelta(days=1),
                                 PublicationRecord.status.notin_(["cancelled"])),
        "videos_generated": count(RenderedVideo, RenderedVideo.status == "ready"),
        "videos_generated_today": count(RenderedVideo, RenderedVideo.status == "ready", RenderedVideo.created_at >= day),
        "published": count(PublicationRecord, PublicationRecord.status == "published"),
        "published_today": count(PublicationRecord, PublicationRecord.status == "published", PublicationRecord.published_at >= day),
        "awaiting_approval": count(PublicationRecord, PublicationRecord.status == "awaiting_approval"),
        "failed_jobs_7d": count(ScheduledJob, ScheduledJob.status == "failed", ScheduledJob.finished_at >= now - timedelta(days=7)),
        "failed_publications": count(PublicationRecord, PublicationRecord.status.in_(["failed", "verify_required"])),
    }
    running = list(db.scalars(select(ScheduledJob).where(ScheduledJob.status.in_(["running", "queued", "retry_wait", "blocked"]))
                              .order_by(ScheduledJob.status.desc(), ScheduledJob.run_at).limit(12)))
    upcoming = db.execute(select(PublicationRecord, SocialAccount, Application)
                          .join(SocialAccount, PublicationRecord.social_account_id == SocialAccount.id)
                          .join(Application, PublicationRecord.application_id == Application.id)
                          .where(PublicationRecord.scheduled_for >= now - timedelta(hours=1),
                                 PublicationRecord.status.in_(["planned", "preparing", "awaiting_approval", "approved"]))
                          .order_by(PublicationRecord.scheduled_for).limit(10)).all()
    per_app = db.execute(select(Application.id, Application.name,
                                func.count(func.distinct(RenderedVideo.id)))
                         .join(RenderedVideo, RenderedVideo.application_id == Application.id, isouter=True)
                         .group_by(Application.id)).all()
    pub_counts = dict(db.execute(select(PublicationRecord.application_id, func.count())
                                 .where(PublicationRecord.status == "published").group_by(PublicationRecord.application_id)).all())
    comfy = get_section(db, "comfyui")
    worker = {"comfyui": None}
    if comfy.get("enabled"):
        try:
            c = ComfyClient(comfy["url"], timeout=3)
            worker["comfyui"] = {**summarize_stats(c.system_stats()), **c.queue_state(), "ok": True}
        except Exception as exc:
            worker["comfyui"] = {"ok": False, "error": str(exc)[:200]}
    tok = db.scalar(select(func.coalesce(func.sum(AIUsageRecord.input_tokens + AIUsageRecord.output_tokens), 0))
                    .where(AIUsageRecord.created_at >= day))
    return {"stats": stats, "jobs": [ser_job(j) for j in running],
            "upcoming": [ser_pub(p, a, ap) for p, a, ap in upcoming],
            "per_application": [{"id": i, "name": n, "videos": v, "published": pub_counts.get(i, 0)} for i, n, v in per_app],
            "worker": worker, "ai": {**budget_status(db), "tokens_today": int(tok or 0)}}


@router.get("/notifications")
def notifications(unread_only: bool = False, user: User = AuthUser, db: Session = Depends(get_db)):
    q = select(Notification).order_by(Notification.created_at.desc()).limit(50)
    if unread_only:
        q = q.where(Notification.read.is_(False))
    rows = db.scalars(q)
    unread = db.scalar(select(func.count(Notification.id)).where(Notification.read.is_(False)))
    return {"unread": unread, "items": [{"id": n.id, "level": n.level, "title": n.title, "message": n.message, "link": n.link,
                                         "read": n.read, "created_at": iso(n.created_at)} for n in rows]}


@router.post("/notifications/read-all")
def notifications_read(user: User = AuthUser, db: Session = Depends(get_db)):
    for n in db.scalars(select(Notification).where(Notification.read.is_(False))):
        n.read = True
    db.commit()
    return {"ok": True}


@router.post("/backup")
def backup_now(user: User = AuthUser):
    from app.workers.handlers import run_backup

    return run_backup()


@router.get("/backups")
def backups(user: User = AuthUser):
    d = get_settings().storage("backups")
    return [{"name": p.name, "size": p.stat().st_size} for p in sorted(d.glob("autocontent_*.db"), reverse=True)]


@router.post("/tick")
def manual_tick(user: User = AuthUser, db: Session = Depends(get_db)):
    """Run the scheduler tick now (normally every minute)."""
    from app.services.scheduling.engine import tick

    return tick(db)


_ = ser_pub
