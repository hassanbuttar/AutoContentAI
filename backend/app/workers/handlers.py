"""Job handlers: thin adapters from queue jobs to services."""
from __future__ import annotations

import logging
import sqlite3
from datetime import timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import (Application, PublicationRecord, PublishingRule, RenderedVideo, ScheduledJob, SocialAccount,
                        Storyboard, VideoScript, utcnow)
from app.services.ai.budget import BudgetExceeded
from app.services.ai.client import AIRequestError
from app.services.audit import job_log, notify
from app.services.content.planner import plan_content
from app.services.knowledge.analyzer import analyze_application
from app.services.publishing import service as pubsvc
from app.services.publishing.registry import get_adapter
from app.services.settings_service import get_section
from app.services.video.renderer import Renderer, RenderHooks
from app.workers.queue import FatalError, JobBlocked, RetryableError, enqueue
from app.workers.runner import JobCancelled, JobContext

log = logging.getLogger(__name__)
LIVE_PUB_STATUSES = ("planned", "preparing", "awaiting_approval", "approved", "publishing", "published", "exported", "verify_required")


def _ai_guard(fn):
    """Translate AI budget / request errors into queue semantics."""
    def wrapper(job: ScheduledJob, ctx: JobContext):
        try:
            return fn(job, ctx)
        except BudgetExceeded as exc:
            with SessionLocal() as db:
                notify(db, "AI budget limit reached", exc.reason, "warning", link="/usage", event="budget_exceeded")
                db.commit()
            raise JobBlocked(exc.reason, exc.retry_after) from exc
        except AIRequestError as exc:
            if exc.retryable:
                raise RetryableError(str(exc)) from exc
            raise FatalError(str(exc)) from exc
    wrapper.__name__ = fn.__name__
    return wrapper


def _say(ctx: JobContext, app_id: int | None = None):
    return lambda m, lvl="info": ctx.log(m, lvl, application_id=app_id)


# ------------------------------------------------------------------ analysis
@_ai_guard
def h_analyze(job: ScheduledJob, ctx: JobContext) -> dict:
    with SessionLocal() as db:
        app = db.get(Application, job.payload["application_id"])
        if app is None:
            raise FatalError("Application not found")
        ctx.progress(0.1, f"Analyzing {app.name}")
        rep = analyze_application(db, app, job_id=job.id, force_full=bool(job.payload.get("force_full")), log_fn=_say(ctx, app.id))
        ctx.log(f"Analysis done: {rep.as_dict()}", application_id=app.id)
        return rep.as_dict()


# ------------------------------------------------------------------ approval routing
def effective_mode(app: Application, account: SocialAccount) -> str:
    return account.publishing_mode or app.publishing_mode or "semi"


def attach_video(db, pub: PublicationRecord, video: RenderedVideo, ctx: JobContext | None = None) -> str:
    app = db.get(Application, pub.application_id)
    account = db.get(SocialAccount, pub.social_account_id)
    script = db.get(VideoScript, video.video_script_id) if video.video_script_id else None
    sc = (script.social_content if script else {}) or {}
    key = {"youtube": "youtube", "tiktok": "tiktok"}.get(pub.platform, "instagram")
    copy = sc.get(key) or {}
    pub.rendered_video_id = video.id
    if not pub.caption:
        pub.caption = copy.get("caption") or (sc.get("instagram") or {}).get("caption", "")
    if not pub.hashtags:
        pub.hashtags = copy.get("hashtags") or (sc.get("instagram") or {}).get("hashtags", [])
    if not pub.title:
        pub.title = (sc.get("youtube") or {}).get("title") or (script.hook if script else "")
    if video.status != "ready":
        pub.status = "failed"
        pub.last_error = video.error or "Render failed"
        return pub.status
    mode = effective_mode(app, account)
    qc = video.qc_report or {}
    if mode == "manual":
        pub.status = "awaiting_approval"
    elif mode == "semi":
        if video.review_status == "pending" and qc.get("ok"):
            video.review_status = "approved"
        pub.status = "awaiting_approval"
    else:  # auto
        caps = get_adapter(account.platform if account.status != "export_only" else "export").capabilities(pubsvc.account_settings(db, account))
        connected = account.status in ("connected", "export_only") or account.platform == "export"
        reasons = []
        if not qc.get("auto_publish_ok"):
            reasons.append("QC flags: " + ", ".join(qc.get("flags", [])))
        if not connected:
            reasons.append(f"account status {account.status}")
        if not caps.get("direct_publish") and account.platform != "export" and account.status != "export_only":
            reasons.append("platform mode requires manual completion")
        if video.review_status == "rejected":
            reasons.append("video rejected")
        if reasons:
            pub.status = "awaiting_approval"
            pub.last_error = "Auto-publish held for review: " + "; ".join(reasons)
        else:
            video.review_status = "approved"
            pub.status, pub.approved_at = "approved", utcnow()
    if ctx:
        ctx.log(f"Publication {pub.id} ({pub.platform}) -> {pub.status} [mode {mode}]", application_id=pub.application_id)
    return pub.status


def find_reusable_video(db, pub: PublicationRecord, rule: PublishingRule | None) -> RenderedVideo | None:
    window = int(get_section(db, "scheduler")["reuse_window_hours"])
    videos = list(db.scalars(select(RenderedVideo).where(RenderedVideo.application_id == pub.application_id,
                                                         RenderedVideo.status == "ready",
                                                         RenderedVideo.review_status != "rejected")
                             .order_by(RenderedVideo.created_at.asc())))
    def uses(v):
        return list(db.scalars(select(PublicationRecord).where(PublicationRecord.rendered_video_id == v.id,
                                                               PublicationRecord.status.in_(LIVE_PUB_STATUSES),
                                                               PublicationRecord.id != pub.id)))
    # 1) backlog: videos not assigned anywhere (e.g. generated from the Studio), oldest first
    for v in videos:
        if not uses(v):
            return v
    # 2) cross-platform reuse of a recent video not yet used on this platform/account
    if rule is None or rule.reuse_across_platforms:
        since = utcnow() - timedelta(hours=window)
        for v in reversed(videos):
            if v.created_at < since:
                continue
            u = uses(v)
            if all(p.platform != pub.platform and p.social_account_id != pub.social_account_id for p in u):
                return v
    return None


@_ai_guard
def h_prepare(job: ScheduledJob, ctx: JobContext) -> dict:
    with SessionLocal() as db:
        pub = db.get(PublicationRecord, job.payload["publication_id"])
        if pub is None or pub.status not in ("planned", "preparing"):
            return {"skipped": True, "status": pub.status if pub else None}
        pub.status = "preparing"
        db.commit()
        app = db.get(Application, pub.application_id)
        rule = db.get(PublishingRule, pub.publishing_rule_id) if pub.publishing_rule_id else None
        v = find_reusable_video(db, pub, rule)
        if v:
            st = attach_video(db, pub, v, ctx)
            db.commit()
            ctx.log(f"Reused video #{v.id} for publication {pub.id} (no AI call)", application_id=app.id)
            return {"reused_video": v.id, "status": st}
        # join an in-flight render for the same app (one video, several platforms)
        if rule is None or rule.reuse_across_platforms:
            for rj in db.scalars(select(ScheduledJob).where(ScheduledJob.job_type == "render_video",
                                                            ScheduledJob.application_id == app.id,
                                                            ScheduledJob.status.in_(["queued", "retry_wait", "blocked"]))):
                ids = list(rj.payload.get("publication_ids", []))
                plats = {db.get(PublicationRecord, i).platform for i in ids if db.get(PublicationRecord, i)}
                if rj.payload.get("reusable") and pub.platform not in plats:
                    rj.payload = {**rj.payload, "publication_ids": ids + [pub.id]}
                    db.commit()
                    ctx.log(f"Publication {pub.id} joined render job {rj.id}", application_id=app.id)
                    return {"joined_render_job": rj.id}
        ctx.progress(0.2, "Planning content")
        types = (rule.content_types if rule and rule.content_types else None)
        res = plan_content(db, app, job_id=job.id, content_type=types[0] if types else None, log_fn=_say(ctx, app.id))
        rj, _ = enqueue(db, "render_video", {"storyboard_id": res.storyboard.id, "publication_ids": [pub.id],
                                             "reusable": bool(rule is None or rule.reuse_across_platforms)},
                        idempotency_key=f"render:sb{res.storyboard.id}", application_id=app.id, priority=60, parent_job_id=job.id)
        db.commit()
        return {"storyboard_id": res.storyboard.id, "render_job": rj.id, "used_ai": res.used_ai, "notes": res.notes}


@_ai_guard
def h_plan(job: ScheduledJob, ctx: JobContext) -> dict:
    p = job.payload
    with SessionLocal() as db:
        app = db.get(Application, p["application_id"])
        ctx.progress(0.2, "Planning content")
        res = plan_content(db, app, job_id=job.id, content_type=p.get("content_type"), topic=p.get("topic"),
                           force_local=bool(p.get("force_local")), log_fn=_say(ctx, app.id))
        out = {"storyboard_id": res.storyboard.id, "script_id": res.script.id, "used_ai": res.used_ai, "notes": res.notes}
        if p.get("render", True):
            rj, _ = enqueue(db, "render_video", {"storyboard_id": res.storyboard.id, "publication_ids": p.get("publication_ids", []),
                                                 "reusable": True}, idempotency_key=f"render:sb{res.storyboard.id}",
                            application_id=app.id, priority=40, parent_job_id=job.id)
            db.commit()
            out["render_job"] = rj.id
        return out


def h_render(job: ScheduledJob, ctx: JobContext) -> dict:
    with SessionLocal() as db:
        sb = db.get(Storyboard, job.payload["storyboard_id"])
        if sb is None:
            raise FatalError("Storyboard not found")
        hooks = RenderHooks(progress=lambda f, m: ctx.progress(None if f < 0 else f, m),
                            log=lambda m, lvl="info": ctx.log(m, lvl, application_id=job.application_id),
                            cancelled=ctx.cancelled)
        regen = set(job.payload.get("regenerate_scenes") or [])
        if job.payload.get("regenerate_all"):
            regen = {s["scene_number"] for s in sb.scenes}
        video = Renderer(db, hooks).render(sb.id, regenerate_scenes=regen, job_id=job.id)
        if ctx.cancelled():
            raise JobCancelled()
        # re-read payload: prepare jobs may have attached more publications while rendering
        fresh = db.get(ScheduledJob, job.id)
        db.refresh(fresh)
        statuses = {}
        for pid in fresh.payload.get("publication_ids", []):
            pub = db.get(PublicationRecord, pid)
            if pub and pub.status in ("planned", "preparing"):
                statuses[pid] = attach_video(db, pub, video, ctx)
        if video.status != "ready":
            notify(db, f"Video #{video.id} failed QC", video.error or "", "error", link=f"/library?video={video.id}", event="job_failed")
        db.commit()
        return {"video_id": video.id, "status": video.status, "renderer": video.renderer, "publications": statuses,
                "duration": video.duration, "render_seconds": video.render_seconds}


def h_publish(job: ScheduledJob, ctx: JobContext) -> dict:
    with SessionLocal() as db:
        return pubsvc.publish_publication(db, job.payload["publication_id"], job.id, progress=lambda m: ctx.progress(None, m))


def h_verify(job: ScheduledJob, ctx: JobContext) -> dict:
    with SessionLocal() as db:
        return pubsvc.verify_publication(db, job.payload["publication_id"], job.id)


def h_metrics(job: ScheduledJob, ctx: JobContext) -> dict:
    with SessionLocal() as db:
        return pubsvc.fetch_all_metrics(db)


def h_refresh(job: ScheduledJob, ctx: JobContext) -> dict:
    with SessionLocal() as db:
        return pubsvc.refresh_expiring_tokens(db)


def run_backup(keep: int = 14) -> dict:
    s = get_settings()
    if not s.db_url.startswith("sqlite"):
        return {"skipped": "non-sqlite database; use your database's backup tooling"}
    src_path = s.db_url.replace("sqlite:///", "")
    dest_dir = s.storage("backups")
    dest = dest_dir / f"autocontent_{utcnow():%Y%m%d_%H%M%S}.db"
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    backups = sorted(dest_dir.glob("autocontent_*.db"))
    for old in backups[:-keep]:
        old.unlink(missing_ok=True)
    return {"backup": dest.name, "kept": min(len(backups), keep)}


def h_backup(job: ScheduledJob, ctx: JobContext) -> dict:
    return run_backup()


HANDLERS = {"analyze_application": h_analyze, "prepare_content": h_prepare, "plan_content": h_plan, "render_video": h_render,
            "publish": h_publish, "verify_publication": h_verify, "fetch_metrics": h_metrics, "refresh_tokens": h_refresh,
            "backup": h_backup}

_ = job_log
