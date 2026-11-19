"""Execution logs / jobs, ComfyUI workflows, settings & secrets, AI usage, analytics."""
from __future__ import annotations

import json
from datetime import timedelta

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select

from app.api.deps import AuthUser, Depends, Session, User, get_db, get_or_404, iso, ser_job, ser_workflow
from app.core import secrets
from app.models import (AIUsageRecord, Application, ComfyWorkflow, ContentPerformance, JobAttempt, LogEntry,
                        PublicationRecord, RenderedVideo, ScheduledJob, utcnow)
from app.schemas.api import SecretIn, WorkflowIn, WorkflowPatch
from app.services.ai.budget import budget_status
from app.services.ai.client import connectivity_test
from app.services.audit import audit
from app.services.settings_service import SECTIONS, all_settings, update_section
from app.services.video import tts
from app.services.video.comfyui import ComfyClient, ComfyError, is_ui_format, summarize_stats
from app.workers import queue as q

router = APIRouter(prefix="/api", tags=["ops"])


# ------------------------------------------------------------------ jobs & logs
@router.get("/jobs")
def list_jobs(status: str | None = None, job_type: str | None = None, application_id: int | None = None, limit: int = 100,
              user: User = AuthUser, db: Session = Depends(get_db)):
    qy = select(ScheduledJob).order_by(ScheduledJob.id.desc()).limit(min(limit, 500))
    if status:
        qy = qy.where(ScheduledJob.status.in_(status.split(",")))
    if job_type:
        qy = qy.where(ScheduledJob.job_type == job_type)
    if application_id:
        qy = qy.where(ScheduledJob.application_id == application_id)
    return [ser_job(j) for j in db.scalars(qy)]


@router.get("/jobs/{job_id}")
def get_job(job_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    j = get_or_404(db, ScheduledJob, job_id)
    d = ser_job(j)
    d["attempts_detail"] = [{"attempt_no": a.attempt_no, "status": a.status, "error": a.error, "traceback": a.traceback,
                             "worker_id": a.worker_id, "started_at": iso(a.started_at), "finished_at": iso(a.finished_at),
                             "duration_s": a.duration_s}
                            for a in db.scalars(select(JobAttempt).where(JobAttempt.job_id == j.id).order_by(JobAttempt.id))]
    d["logs"] = [{"level": e.level, "message": e.message, "created_at": iso(e.created_at)}
                 for e in db.scalars(select(LogEntry).where(LogEntry.job_id == j.id).order_by(LogEntry.id))]
    d["children"] = [ser_job(c) for c in db.scalars(select(ScheduledJob).where(ScheduledJob.parent_job_id == j.id))]
    return d


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    j = get_or_404(db, ScheduledJob, job_id)
    if not q.cancel(db, j):
        raise HTTPException(409, f"Job already {j.status}")
    return ser_job(j)


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    j = get_or_404(db, ScheduledJob, job_id)
    q.retry_now(db, j)
    return ser_job(j)


@router.get("/logs")
def logs(level: str | None = None, application_id: int | None = None, limit: int = 200, user: User = AuthUser,
         db: Session = Depends(get_db)):
    qy = select(LogEntry).order_by(LogEntry.id.desc()).limit(min(limit, 1000))
    if level:
        qy = qy.where(LogEntry.level.in_(level.split(",")))
    if application_id:
        qy = qy.where(LogEntry.application_id == application_id)
    return [{"id": e.id, "level": e.level, "message": e.message, "job_id": e.job_id, "application_id": e.application_id,
             "created_at": iso(e.created_at)} for e in db.scalars(qy)]


# ------------------------------------------------------------------ ComfyUI
def _comfy(db: Session) -> ComfyClient:
    from app.services.settings_service import get_section

    return ComfyClient(get_section(db, "comfyui")["url"], timeout=10)


@router.get("/comfyui/status")
def comfy_status(user: User = AuthUser, db: Session = Depends(get_db)):
    try:
        c = _comfy(db)
        return {"ok": True, **summarize_stats(c.system_stats()), **c.queue_state()}
    except ComfyError as exc:
        return {"ok": False, "error": str(exc), "action": exc.actionable}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@router.get("/workflows")
def list_workflows(user: User = AuthUser, db: Session = Depends(get_db)):
    return [ser_workflow(w) for w in db.scalars(select(ComfyWorkflow).order_by(ComfyWorkflow.kind, ComfyWorkflow.name))]


@router.get("/workflows/{wf_id}")
def get_workflow(wf_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    return ser_workflow(get_or_404(db, ComfyWorkflow, wf_id), include_workflow=True)


@router.post("/workflows", status_code=201)
def create_workflow(body: WorkflowIn, user: User = AuthUser, db: Session = Depends(get_db)):
    if is_ui_format(body.workflow):
        raise HTTPException(400, "UI-format workflow detected. In ComfyUI choose Workflow → Export (API) and import that file.")
    w = ComfyWorkflow(**body.model_dump())
    db.add(w)
    db.flush()
    audit(db, "workflow.create", user.id, "workflow", w.id)
    db.commit()
    return ser_workflow(w)


@router.post("/workflows/import", status_code=201)
async def import_workflow(file: UploadFile = File(...), name: str = Form(""), kind: str = Form("txt2img"),
                          user: User = AuthUser, db: Session = Depends(get_db)):
    raw = await file.read()
    if len(raw) > 5_000_000:
        raise HTTPException(413, "Workflow file too large")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Not valid JSON") from exc
    if "workflow" in data and isinstance(data["workflow"], dict) and "mappings" in data:  # AutoContent bundle format
        body = WorkflowIn(**{k: v for k, v in data.items() if k in WorkflowIn.model_fields})
        if name:
            body.name = name
    else:
        if is_ui_format(data):
            raise HTTPException(400, "UI-format workflow detected. In ComfyUI choose Workflow → Export (API) and import that file.")
        body = WorkflowIn(name=name or (file.filename or "workflow").rsplit(".", 1)[0], kind=kind, workflow=data,
                          output_type="video" if kind in ("img2vid", "txt2vid") else "image", mappings=guess_mappings(data))
    return create_workflow(body, user, db)


def guess_mappings(wf: dict) -> dict:
    """Best-effort suggestions; the user confirms them in the mapping editor."""
    m: dict = {}
    sampler = next((k for k, v in wf.items() if isinstance(v, dict) and v.get("class_type", "").startswith("KSampler")), None)
    if sampler:
        ins = wf[sampler].get("inputs", {})
        for key, field in (("seed", "seed"), ("seed", "noise_seed"), ("steps", "steps")):
            if field in ins and key not in m:
                m[key] = {"node": sampler, "input": field}
        for key, field in (("positive_prompt", "positive"), ("negative_prompt", "negative")):
            link = ins.get(field)
            if isinstance(link, list) and str(link[0]) in wf and "text" in wf[str(link[0])].get("inputs", {}):
                m[key] = {"node": str(link[0]), "input": "text"}
    for k, v in wf.items():
        if not isinstance(v, dict):
            continue
        ct, ins = v.get("class_type", ""), v.get("inputs", {})
        if "width" in ins and "height" in ins and "width" not in m and not isinstance(ins["width"], list):
            m["width"], m["height"] = {"node": k, "input": "width"}, {"node": k, "input": "height"}
            if "length" in ins and not isinstance(ins["length"], list):
                m["length"] = {"node": k, "input": "length"}
        if ct == "LoadImage" and "input_image" not in m:
            m["input_image"] = {"node": k, "input": "image"}
        if "filename_prefix" in ins and "filename_prefix" not in m:
            m["filename_prefix"] = {"node": k, "input": "filename_prefix"}
    return m


@router.patch("/workflows/{wf_id}")
def patch_workflow(wf_id: int, body: WorkflowPatch, user: User = AuthUser, db: Session = Depends(get_db)):
    w = get_or_404(db, ComfyWorkflow, wf_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(w, k, v)
    if body.mappings is not None:
        w.validated = False
    if body.is_default:
        for o in db.scalars(select(ComfyWorkflow).where(ComfyWorkflow.kind == w.kind, ComfyWorkflow.id != w.id)):
            o.is_default = False
    db.commit()
    return ser_workflow(w)


@router.delete("/workflows/{wf_id}")
def delete_workflow(wf_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    w = get_or_404(db, ComfyWorkflow, wf_id)
    db.delete(w)
    db.commit()
    return {"ok": True}


@router.post("/workflows/{wf_id}/validate")
def validate_workflow(wf_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    w = get_or_404(db, ComfyWorkflow, wf_id)
    try:
        report = _comfy(db).validate(w.workflow, w.mappings)
    except ComfyError as exc:
        raise HTTPException(503, f"{exc} {exc.actionable}") from exc
    w.validated, w.validation_report = report["ok"], {**report, "checked_at": iso(utcnow())}
    db.commit()
    return ser_workflow(w)


# ------------------------------------------------------------------ settings
@router.get("/settings")
def get_settings_all(user: User = AuthUser, db: Session = Depends(get_db)):
    from typing import get_args

    names = list(get_args(SecretIn.model_fields["name"].annotation))
    return {"settings": all_settings(db), "secrets": {n: secrets.secret_status(db, n) for n in names},
            "key_storage": secrets.key_source()}


@router.patch("/settings/{section}")
def patch_settings(section: str, patch: dict, user: User = AuthUser, db: Session = Depends(get_db)):
    if section not in SECTIONS:
        raise HTTPException(404, "Unknown settings section")
    if section == "security":
        roots = patch.get("allowed_project_roots")
        if roots is not None:
            from pathlib import Path

            clean = []
            for r in roots:
                p = Path(str(r)).expanduser()
                if not p.is_absolute() or not p.exists() or not p.is_dir():
                    raise HTTPException(400, f"Allowed root must be an existing absolute folder: {r}")
                if len(p.resolve().parts) <= 1:
                    raise HTTPException(400, "Refusing to allow an entire drive root")
                clean.append(str(p.resolve()))
            patch["allowed_project_roots"] = clean
    if section == "general" and "timezone" in patch:
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(patch["timezone"])
        except Exception as exc:
            raise HTTPException(400, "Unknown timezone") from exc
    merged = update_section(db, section, patch)
    audit(db, "settings.update", user.id, "settings", section, {"keys": list(patch)})
    db.commit()
    return merged


@router.put("/secrets")
def put_secret(body: SecretIn, user: User = AuthUser, db: Session = Depends(get_db)):
    secrets.set_secret(db, body.name, body.value.strip())
    audit(db, "secret.set", user.id, "secret", body.name)  # value never logged
    db.commit()
    return secrets.secret_status(db, body.name)


@router.delete("/secrets/{name}")
def delete_secret(name: str, user: User = AuthUser, db: Session = Depends(get_db)):
    if not name.startswith(("anthropic.", "oauth.youtube.", "oauth.instagram.", "oauth.tiktok.", "github.", "gitlab.")):
        raise HTTPException(400, "Unknown secret")
    secrets.delete_secret(db, name)
    audit(db, "secret.delete", user.id, "secret", name)
    db.commit()
    return {"ok": True}


@router.post("/settings/test/claude")
def test_claude(user: User = AuthUser, db: Session = Depends(get_db)):
    return connectivity_test(db)


@router.get("/settings/voices")
def voices(user: User = AuthUser):
    return tts.list_voices()


# ------------------------------------------------------------------ AI usage
@router.get("/usage")
def usage(days: int = 30, user: User = AuthUser, db: Session = Depends(get_db)):
    since = utcnow() - timedelta(days=min(days, 365))
    rows = list(db.scalars(select(AIUsageRecord).where(AIUsageRecord.created_at >= since).order_by(AIUsageRecord.id.desc())))
    by_day: dict[str, dict] = {}
    by_app: dict[str, dict] = {}
    by_purpose: dict[str, dict] = {}
    names = {a.id: a.name for a in db.scalars(select(Application))}
    for r in rows:
        tok = r.input_tokens + r.output_tokens + r.cache_read_tokens + r.cache_creation_tokens
        for bucket, key in ((by_day, r.created_at.date().isoformat()), (by_app, names.get(r.application_id, "System")),
                            (by_purpose, r.purpose)):
            b = bucket.setdefault(key, {"calls": 0, "tokens": 0, "cost": 0.0, "cache_read": 0, "input": 0, "output": 0})
            b["calls"] += 1
            b["tokens"] += tok
            b["cost"] = round(b["cost"] + r.estimated_cost, 6)
            b["cache_read"] += r.cache_read_tokens
            b["input"] += r.input_tokens
            b["output"] += r.output_tokens
    return {"budget": budget_status(db),
            "by_day": [{"day": k, **v} for k, v in sorted(by_day.items())],
            "by_application": [{"application": k, **v} for k, v in by_app.items()],
            "by_purpose": [{"purpose": k, **v} for k, v in by_purpose.items()],
            "records": [{"id": r.id, "created_at": iso(r.created_at), "application": names.get(r.application_id),
                         "job_id": r.job_id, "purpose": r.purpose, "model": r.model, "input_tokens": r.input_tokens,
                         "output_tokens": r.output_tokens, "cache_read_tokens": r.cache_read_tokens,
                         "cache_creation_tokens": r.cache_creation_tokens, "estimated_cost": r.estimated_cost,
                         "duration_ms": r.duration_ms, "success": r.success, "error": r.error, "stop_reason": r.stop_reason}
                        for r in rows[:300]]}


# ------------------------------------------------------------------ analytics
@router.get("/analytics")
def analytics(days: int = 30, user: User = AuthUser, db: Session = Depends(get_db)):
    since = utcnow() - timedelta(days=min(days, 365))
    names = {a.id: a.name for a in db.scalars(select(Application))}
    videos = list(db.scalars(select(RenderedVideo).where(RenderedVideo.created_at >= since)))
    pubs = list(db.scalars(select(PublicationRecord).where(PublicationRecord.scheduled_for >= since,
                                                           PublicationRecord.scheduled_for <= utcnow())))
    gen_by_app: dict[str, int] = {}
    for v in videos:
        if v.status == "ready":
            gen_by_app[names.get(v.application_id, "?")] = gen_by_app.get(names.get(v.application_id, "?"), 0) + 1
    by_platform: dict[str, dict] = {}
    on_time = late = 0
    for p in pubs:
        b = by_platform.setdefault(p.platform, {"published": 0, "failed": 0, "missed": 0, "exported": 0, "total": 0})
        b["total"] += 1
        if p.status in b:
            b[p.status] += 1
        if p.status == "published" and p.published_at:
            if p.published_at - p.scheduled_for <= timedelta(minutes=15):
                on_time += 1
            else:
                late += 1
    attempted = sum(b["published"] + b["failed"] for b in by_platform.values())
    jobs_failed = db.scalar(select(func.count(ScheduledJob.id)).where(ScheduledJob.status == "failed", ScheduledJob.created_at >= since)) or 0
    jobs_total = db.scalar(select(func.count(ScheduledJob.id)).where(ScheduledJob.created_at >= since)) or 0
    render_times = [v.render_seconds for v in videos if v.render_seconds]
    by_day: dict[str, dict] = {}
    for v in videos:
        if v.status == "ready":
            d = by_day.setdefault(v.created_at.date().isoformat(), {"generated": 0, "published": 0})
            d["generated"] += 1
    for p in pubs:
        if p.status == "published" and p.published_at:
            d = by_day.setdefault(p.published_at.date().isoformat(), {"generated": 0, "published": 0})
            d["published"] += 1
    backlog = db.scalar(select(func.count(RenderedVideo.id)).where(
        RenderedVideo.status == "ready", RenderedVideo.review_status != "rejected",
        ~RenderedVideo.id.in_(select(PublicationRecord.rendered_video_id).where(PublicationRecord.rendered_video_id.is_not(None))))) or 0
    # engagement: latest snapshot per publication; None stays None (never invented)
    latest = {}
    for perf in db.scalars(select(ContentPerformance).order_by(ContentPerformance.fetched_at)):
        latest[perf.publication_record_id] = perf
    engagement = []
    for pid, perf in latest.items():
        p = db.get(PublicationRecord, pid)
        if p is None:
            continue
        engagement.append({"publication_id": pid, "platform": p.platform, "application": names.get(p.application_id),
                           "url": p.external_url, "views": perf.views, "likes": perf.likes, "comments": perf.comments,
                           "shares": perf.shares, "unavailable": perf.unavailable, "fetched_at": iso(perf.fetched_at)})
    usage = db.execute(select(func.coalesce(func.sum(AIUsageRecord.input_tokens + AIUsageRecord.output_tokens), 0),
                              func.coalesce(func.sum(AIUsageRecord.estimated_cost), 0.0)).where(AIUsageRecord.created_at >= since)).one()
    return {
        "generated_by_application": [{"application": k, "videos": v} for k, v in sorted(gen_by_app.items())],
        "by_platform": [{"platform": k, **v} for k, v in by_platform.items()],
        "success_rate": round(sum(b["published"] for b in by_platform.values()) / attempted, 3) if attempted else None,
        "failed_jobs": jobs_failed, "total_jobs": jobs_total,
        "avg_render_seconds": round(sum(render_times) / len(render_times), 1) if render_times else None,
        "videos_per_day": round(len([v for v in videos if v.status == "ready"]) / max(1, days), 2),
        "tokens": int(usage[0]), "cost": round(float(usage[1]), 4), "backlog": backlog,
        "schedule_adherence": {"on_time": on_time, "late": late,
                               "rate": round(on_time / (on_time + late), 3) if (on_time + late) else None},
        "by_day": [{"day": k, **v} for k, v in sorted(by_day.items())],
        "engagement": engagement,
        "engagement_note": "Metrics come from official platform APIs only; empty values mean the platform did not provide them.",
    }
