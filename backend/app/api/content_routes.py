from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.deps import (AuthUser, Depends, Session, User, get_db, get_or_404, iso, ser_job, ser_pub, ser_video,
                          to_utc_naive)
from app.core.paths import storage_path
from app.models import (Application, ContentIdea, GeneratedAsset, PublicationRecord, RenderedVideo, ScheduledJob,
                        SocialAccount, Storyboard, VideoScript, utcnow)
from app.schemas.api import (GenerateIn, MarkPublishedIn, PublicationCreate, PublicationPatch, RerenderIn, ScriptPatch)
from app.services.ai.schemas import ContentPlan, SceneIn
from app.services.audit import audit
from app.services.content import claims as claim_check
from app.services.publishing.service import check_public_media_token
from app.services.publishing import limits
from app.services.storage_cleanup import remove_files
from app.services.video import library
from app.workers import queue as q
from app.workers.queue import enqueue

router = APIRouter(prefix="/api", tags=["content"])
media_router = APIRouter(tags=["media"])


# ------------------------------------------------------------------ studio
@router.post("/studio/generate")
def generate(body: GenerateIn, user: User = AuthUser, db: Session = Depends(get_db)):
    app = get_or_404(db, Application, body.application_id)
    job, _ = enqueue(db, "plan_content", {"application_id": app.id, "content_type": body.content_type, "topic": body.topic,
                                          "force_local": body.force_local, "render": body.render},
                     idempotency_key=f"plan:{app.id}:{uuid.uuid4().hex[:10]}", application_id=app.id, priority=40)
    audit(db, "studio.generate", user.id, "application", app.id, {"topic": body.topic, "type": body.content_type})
    db.commit()
    return {"job_id": job.id}


def _script_detail(db: Session, s: VideoScript) -> dict:
    idea = db.get(ContentIdea, s.content_idea_id)
    sb = db.scalar(select(Storyboard).where(Storyboard.video_script_id == s.id).order_by(Storyboard.version.desc()).limit(1))
    videos = list(db.scalars(select(RenderedVideo).where(RenderedVideo.video_script_id == s.id).order_by(RenderedVideo.id.desc())))
    return {"id": s.id, "application_id": s.application_id, "hook": s.hook, "script_text": s.script_text, "cta": s.cta,
            "duration_seconds": s.duration_seconds, "social_content": s.social_content, "claims": s.claims,
            "claim_check": s.claim_check, "edited": s.edited, "created_at": iso(s.created_at), "version": s.version,
            "idea": {"id": idea.id, "topic": idea.topic, "content_type": idea.content_type, "source": idea.source,
                     "angle": idea.angle, "status": idea.status} if idea else None,
            "storyboard": {"id": sb.id, "version": sb.version, "scenes": sb.scenes} if sb else None,
            "videos": [ser_video(v) for v in videos], "ai_usage_id": s.ai_usage_id}


@router.get("/scripts")
def list_scripts(application_id: int | None = None, limit: int = 50, user: User = AuthUser, db: Session = Depends(get_db)):
    qy = select(VideoScript).order_by(VideoScript.id.desc()).limit(min(limit, 200))
    if application_id:
        qy = qy.where(VideoScript.application_id == application_id)
    return [_script_detail(db, s) for s in db.scalars(qy)]


@router.get("/scripts/{script_id}")
def get_script(script_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    return _script_detail(db, get_or_404(db, VideoScript, script_id))


@router.put("/scripts/{script_id}")
def edit_script(script_id: int, body: ScriptPatch, user: User = AuthUser, db: Session = Depends(get_db)):
    s = get_or_404(db, VideoScript, script_id)
    if body.hook is not None:
        s.hook = body.hook
    if body.cta is not None:
        s.cta = body.cta
    if body.social_content is not None:
        merged = {**(s.social_content or {})}
        for k in ("instagram", "tiktok", "youtube"):
            if isinstance(body.social_content.get(k), dict):
                merged[k] = {**merged.get(k, {}), **body.social_content[k]}
        s.social_content = merged
    if body.scenes is not None:
        scenes = [SceneIn.model_validate(x).model_dump() for x in body.scenes]  # validate edits like AI output
        for i, sc in enumerate(scenes, start=1):
            sc["scene_number"] = i
        last = db.scalar(select(Storyboard).where(Storyboard.video_script_id == s.id).order_by(Storyboard.version.desc()).limit(1))
        db.add(Storyboard(video_script_id=s.id, version=(last.version + 1) if last else 1, scenes=scenes))
        s.script_text = " ".join(x["narration"] for x in scenes if x["narration"])
        s.duration_seconds = sum(x["duration_seconds"] for x in scenes)
        plan = dict(s.plan_json or {})
        plan.update({"scenes": scenes, "hook": s.hook, "cta": s.cta, "social": s.social_content})
        try:
            app = db.get(Application, s.application_id)
            s.claim_check = claim_check.check_plan(ContentPlan.model_validate(plan), app.knowledge.facts if app.knowledge else [])
        except Exception:
            pass
    s.edited = True
    if body.social_content is not None:
        library.refresh_caption_qc(db, s)  # e.g. an import that only failed for an empty caption becomes ready
    db.commit()
    return _script_detail(db, s)


@router.post("/storyboards/{sb_id}/render")
def render_storyboard(sb_id: int, body: RerenderIn, user: User = AuthUser, db: Session = Depends(get_db)):
    sb = get_or_404(db, Storyboard, sb_id)
    script = db.get(VideoScript, sb.video_script_id)
    job, _ = enqueue(db, "render_video", {"storyboard_id": sb.id, "publication_ids": [], "regenerate_scenes": body.scenes,
                                          "regenerate_all": body.all, "reusable": True},
                     idempotency_key=f"render:sb{sb.id}:{uuid.uuid4().hex[:8]}", application_id=script.application_id, priority=40)
    db.commit()
    return {"job_id": job.id}


@router.post("/scripts/{script_id}/new-version")
def new_version(script_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    """Regenerate the whole video: a fresh plan for the same topic and content type."""
    s = get_or_404(db, VideoScript, script_id)
    idea = db.get(ContentIdea, s.content_idea_id)
    job, _ = enqueue(db, "plan_content", {"application_id": s.application_id, "topic": idea.topic if idea else None,
                                          "content_type": idea.content_type if idea else None, "render": True},
                     idempotency_key=f"plan:{s.application_id}:{uuid.uuid4().hex[:10]}", application_id=s.application_id, priority=40)
    db.commit()
    return {"job_id": job.id}


# ------------------------------------------------------------------ videos (content library)
@router.get("/videos")
def list_videos(application_id: int | None = None, status: str | None = None, review_status: str | None = None,
                limit: int = 60, user: User = AuthUser, db: Session = Depends(get_db)):
    qy = select(RenderedVideo).order_by(RenderedVideo.id.desc()).limit(min(limit, 300))
    if application_id:
        qy = qy.where(RenderedVideo.application_id == application_id)
    if status:
        qy = qy.where(RenderedVideo.status == status)
    if review_status:
        qy = qy.where(RenderedVideo.review_status == review_status)
    out = []
    for v in db.scalars(qy):
        d = ser_video(v)
        s = db.get(VideoScript, v.video_script_id) if v.video_script_id else None
        app = db.get(Application, v.application_id)
        d["application_name"] = app.name if app else None
        d["hook"] = s.hook if s else ""
        d["topic"] = (db.get(ContentIdea, v.content_idea_id).topic if v.content_idea_id else "")
        d["publications"] = [{"id": p.id, "platform": p.platform, "status": p.status, "scheduled_for": iso(p.scheduled_for)}
                             for p in db.scalars(select(PublicationRecord).where(PublicationRecord.rendered_video_id == v.id))]
        out.append(d)
    return out


IMPORT_MAX_BYTES = 1024 * 1024 * 1024


@router.post("/applications/{app_id}/videos/import", status_code=201)
async def import_video(app_id: int, file: UploadFile = File(...), title: str = Form(""), caption: str = Form(""),
                       hashtags: str = Form(""), youtube_title: str = Form(""), fit: str = Form("auto"),
                       ai_generated: bool = Form(False),
                       user: User = AuthUser, db: Session = Depends(get_db)):
    """Bring a finished video made elsewhere into the library (conformed to 9:16 if needed, QC'd, deduplicated)."""
    import re
    import tempfile
    from pathlib import Path

    from app.core.config import get_settings
    from app.services.video import importer

    app = get_or_404(db, Application, app_id)
    if len(title) > 300 or len(caption) > 2200 or len(youtube_title) > 100 or len(hashtags) > 1000:
        raise HTTPException(400, "Title max 300, caption max 2200, YouTube title max 100 characters")
    tmp_dir = get_settings().storage("tmp")
    fd, tmp_name = tempfile.mkstemp(suffix=Path(file.filename or "upload.mp4").suffix[:10] or ".mp4", dir=tmp_dir)
    tmp = Path(tmp_name)
    try:
        size = 0
        with open(fd, "wb") as fh:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > IMPORT_MAX_BYTES:
                    raise HTTPException(413, "File too large (1 GB max)")
                fh.write(chunk)
        tags = [t for t in re.split(r"[\s,]+", hashtags) if t.strip("#")]
        try:
            res = await run_in_threadpool(importer.import_video, db, app, tmp, original_name=file.filename or "upload",
                                          title=title, caption=caption, hashtags=tags, youtube_title=youtube_title, fit=fit,
                                          ai_generated=ai_generated)
        except importer.DuplicateVideo as exc:
            # Structured so the dashboard can open the existing copy instead of leaving the user stuck.
            raise HTTPException(409, {"message": f"{exc}. Opening it.", "video_id": exc.video_id}) from exc
        except importer.ImportProblem as exc:
            raise HTTPException(400, str(exc)) from exc
        video = res.video
        audit(db, "video.import", user.id, "video", video.id, {"name": (file.filename or "")[:200], "renderer": video.renderer,
                                                              "replaced_video_id": res.replaced_video_id})
        db.commit()
        remove_files(db, res.stale_files)
        return {**ser_video(video), "replaced_video_id": res.replaced_video_id}
    finally:
        tmp.unlink(missing_ok=True)


@router.get("/videos/{video_id}")
def get_video(video_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    v = get_or_404(db, RenderedVideo, video_id)
    d = ser_video(v)
    d["script"] = _script_detail(db, db.get(VideoScript, v.video_script_id)) if v.video_script_id else None
    d["assets"] = [{"id": a.id, "scene_number": a.scene_number, "kind": a.kind, "source": a.source, "status": a.status,
                    "error": a.error, "prompt": a.prompt, "gpu_seconds": a.gpu_seconds}
                   for a in db.scalars(select(GeneratedAsset).where(GeneratedAsset.storyboard_id == v.storyboard_id)
                                       .order_by(GeneratedAsset.scene_number, GeneratedAsset.id))] if v.storyboard_id else []
    d["publications"] = [ser_pub(p, db.get(SocialAccount, p.social_account_id)) for p in
                         db.scalars(select(PublicationRecord).where(PublicationRecord.rendered_video_id == v.id))]
    return d


def _file(rel: str | None, media: str, download_name: str | None = None) -> FileResponse:
    if not rel:
        raise HTTPException(404, "No file")
    p = storage_path(rel)
    if not p.exists():
        raise HTTPException(404, "File missing on disk")
    return FileResponse(p, media_type=media, filename=download_name)


@router.get("/videos/{video_id}/file")
def video_file(video_id: int, download: bool = False, user: User = AuthUser, db: Session = Depends(get_db)):
    v = get_or_404(db, RenderedVideo, video_id)
    return _file(v.path, "video/mp4", f"autocontent_{video_id}.mp4" if download else None)


@router.get("/videos/{video_id}/thumbnail")
def video_thumb(video_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    v = get_or_404(db, RenderedVideo, video_id)
    return _file(v.thumbnail_path, "image/jpeg")


@router.post("/videos/{video_id}/review")
def review_video(video_id: int, decision: str, user: User = AuthUser, db: Session = Depends(get_db)):
    v = get_or_404(db, RenderedVideo, video_id)
    if decision not in ("approved", "rejected", "pending"):
        raise HTTPException(400, "decision must be approved|rejected|pending")
    if decision == "approved" and v.status != "ready":
        raise HTTPException(400, "Only ready videos can be approved")
    v.review_status = decision
    if decision == "rejected":
        for p in db.scalars(select(PublicationRecord).where(PublicationRecord.rendered_video_id == v.id,
                                                            PublicationRecord.status.in_(["awaiting_approval", "approved"]))):
            p.status, p.rendered_video_id, p.last_error = "planned", None, "Video rejected; new content will be prepared"
            j = db.scalar(select(ScheduledJob).where(ScheduledJob.idempotency_key == f"prepare:{p.id}"))
            if j:
                j.idempotency_key = f"prepare:{p.id}:old:{j.id}"  # allow a fresh prepare job
        if v.content_idea_id:
            idea = db.get(ContentIdea, v.content_idea_id)
            if idea:
                idea.status = "rejected"
    audit(db, f"video.{decision}", user.id, "video", v.id)
    db.commit()
    return ser_video(v)


@router.delete("/videos/{video_id}")
def delete_video(video_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    v = get_or_404(db, RenderedVideo, video_id)
    if library.is_published(db, v):
        raise HTTPException(409, "Video has been published; it is kept for history")
    # Database first, files second: on Windows the file may still be open (the dashboard's player streaming it),
    # which used to raise WinError 32 -> HTTP 500 with nothing deleted. Locked files are retried by a cleanup job.
    files = library.delete_video_record(db, v)
    audit(db, "video.delete", user.id, "video", video_id)
    db.commit()
    pending = remove_files(db, files)
    return {"ok": True, "files_pending": len(pending)}


# ------------------------------------------------------------------ publications (queue)
@router.get("/publications")
def list_publications(status: str | None = None, application_id: int | None = None, platform: str | None = None,
                      limit: int = 100, user: User = AuthUser, db: Session = Depends(get_db)):
    qy = (select(PublicationRecord, SocialAccount, Application)
          .join(SocialAccount, PublicationRecord.social_account_id == SocialAccount.id)
          .join(Application, PublicationRecord.application_id == Application.id))
    if status:
        qy = qy.where(PublicationRecord.status.in_(status.split(",")))
    if application_id:
        qy = qy.where(PublicationRecord.application_id == application_id)
    if platform:
        qy = qy.where(PublicationRecord.platform == platform)
    qy = qy.order_by(PublicationRecord.scheduled_for.desc()).limit(min(limit, 500))
    return [ser_pub(p, a, ap) for p, a, ap in db.execute(qy).all()]


@router.post("/publications", status_code=201)
def create_publication(body: PublicationCreate, user: User = AuthUser, db: Session = Depends(get_db)):
    """Manual override: schedule a specific video on a specific account (or publish now)."""
    v = get_or_404(db, RenderedVideo, body.video_id)
    acc = get_or_404(db, SocialAccount, body.social_account_id)
    if acc.application_id != v.application_id:
        raise HTTPException(400, "Video and account belong to different applications")
    if v.status != "ready":
        raise HTTPException(400, "Video is not ready")
    _ensure_fits(v, acc)
    dup = db.scalar(select(PublicationRecord).where(PublicationRecord.rendered_video_id == v.id,
                                                    PublicationRecord.social_account_id == acc.id,
                                                    PublicationRecord.status.in_(["approved", "publishing", "published", "awaiting_approval"])))
    if dup:
        raise HTTPException(409, f"This video is already queued or published on this account (publication #{dup.id})")
    when = to_utc_naive(body.scheduled_for) or utcnow()
    p = PublicationRecord(application_id=v.application_id, social_account_id=acc.id, platform=acc.platform, scheduled_for=when,
                          status="awaiting_approval", idempotency_key=f"manual:{acc.id}:{v.id}:{uuid.uuid4().hex[:8]}", manual=True)
    db.add(p)
    db.flush()
    from app.workers.handlers import attach_video

    attach_video(db, p, v)
    if body.approve:
        p.status, p.approved_at, p.approved_by = "approved", utcnow(), user.id
        v.review_status = "approved"
    audit(db, "publication.create_manual", user.id, "publication", p.id)
    db.commit()
    return ser_pub(p, acc)


@router.get("/publications/{pub_id}")
def get_publication(pub_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    p = get_or_404(db, PublicationRecord, pub_id)
    d = ser_pub(p, db.get(SocialAccount, p.social_account_id), db.get(Application, p.application_id))
    d["video"] = ser_video(db.get(RenderedVideo, p.rendered_video_id)) if p.rendered_video_id else None
    d["platform_state"] = {k: v for k, v in (p.platform_state or {}).items() if k not in ("upload_session", "upload_uri")}
    d["jobs"] = [ser_job(j) for j in db.scalars(select(ScheduledJob).where(ScheduledJob.idempotency_key.in_(
        [f"prepare:{p.id}", f"publish:{p.id}"])))]
    return d


@router.patch("/publications/{pub_id}")
def patch_publication(pub_id: int, body: PublicationPatch, user: User = AuthUser, db: Session = Depends(get_db)):
    p = get_or_404(db, PublicationRecord, pub_id)
    if p.status in ("publishing", "published"):
        raise HTTPException(409, f"Cannot edit a {p.status} publication")
    d = body.model_dump(exclude_unset=True)
    for k in ("title", "caption", "hashtags"):
        if d.get(k) is not None:
            setattr(p, k, d[k])
    if d.get("scheduled_for"):
        p.scheduled_for = to_utc_naive(d["scheduled_for"])
        p.manual = True
    db.commit()
    return ser_pub(p)


def _ensure_fits(v: RenderedVideo, acc: SocialAccount | None) -> None:
    """Refuse a platform whose size/length limits the video breaks (manual-export accounts are not limited)."""
    if acc is None or acc.platform == "export" or acc.status == "export_only":
        return
    fit = limits.check_platform(acc.platform, v.duration, v.size_bytes)
    if not fit["ok"]:
        raise HTTPException(400, f"{limits.LIMITS[acc.platform].label} won't accept this video: {'; '.join(fit['problems'])}")


def _transition(db: Session, p: PublicationRecord, allowed: tuple, new: str) -> None:
    if p.status not in allowed:
        raise HTTPException(409, f"Publication is {p.status}; expected one of {', '.join(allowed)}")
    p.status = new


@router.post("/publications/{pub_id}/approve")
def approve(pub_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    p = get_or_404(db, PublicationRecord, pub_id)
    if not p.rendered_video_id:
        raise HTTPException(400, "No video attached yet")
    v = db.get(RenderedVideo, p.rendered_video_id)
    if v.status != "ready":
        raise HTTPException(400, "Video failed QC; regenerate it first")
    _ensure_fits(v, db.get(SocialAccount, p.social_account_id))
    _transition(db, p, ("awaiting_approval", "failed", "missed"), "approved")
    p.approved_at, p.approved_by, p.last_error = utcnow(), user.id, None
    if p.scheduled_for < utcnow() - timedelta(minutes=5):
        p.scheduled_for, p.manual = utcnow(), True  # approving a past slot publishes now
    v.review_status = "approved"
    audit(db, "publication.approve", user.id, "publication", p.id)
    db.commit()
    return ser_pub(p)


@router.post("/publications/{pub_id}/reject")
def reject(pub_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    p = get_or_404(db, PublicationRecord, pub_id)
    _transition(db, p, ("awaiting_approval", "approved", "planned", "preparing", "failed", "verify_required"), "rejected")
    audit(db, "publication.reject", user.id, "publication", p.id)
    db.commit()
    return ser_pub(p)


@router.post("/publications/{pub_id}/publish-now")
def publish_now(pub_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    p = get_or_404(db, PublicationRecord, pub_id)
    if not p.rendered_video_id:
        raise HTTPException(400, "No video attached yet")
    if p.status not in ("approved", "awaiting_approval", "failed"):
        raise HTTPException(409, f"Publication is {p.status}")
    _ensure_fits(db.get(RenderedVideo, p.rendered_video_id), db.get(SocialAccount, p.social_account_id))
    p.status, p.scheduled_for, p.manual, p.approved_at, p.approved_by = "approved", utcnow(), True, utcnow(), user.id
    db.get(RenderedVideo, p.rendered_video_id).review_status = "approved"
    job, created = enqueue(db, "publish", {"publication_id": p.id}, idempotency_key=f"publish:{p.id}",
                           application_id=p.application_id, priority=5)
    if not created and job.status in ("failed", "cancelled", "succeeded"):
        q.retry_now(db, job, include_succeeded=True)
    audit(db, "publication.publish_now", user.id, "publication", p.id)
    db.commit()
    return {"publication": ser_pub(p), "job_id": job.id}


@router.post("/publications/{pub_id}/retry")
def retry(pub_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    p = get_or_404(db, PublicationRecord, pub_id)
    if p.status == "verify_required":
        raise HTTPException(409, "Check the platform first, then use 'Mark as published' or 'Confirm not published'")
    if p.status not in ("failed", "missed"):
        raise HTTPException(409, f"Publication is {p.status}")
    if p.rendered_video_id:
        return publish_now(pub_id, user, db)
    p.status, p.last_error = "planned", None
    j = db.scalar(select(ScheduledJob).where(ScheduledJob.idempotency_key == f"prepare:{p.id}"))
    if j:
        j.idempotency_key = f"prepare:{p.id}:old:{j.id}"
    db.commit()
    return ser_pub(p)


@router.post("/publications/{pub_id}/confirm-not-published")
def confirm_not_published(pub_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    """Human checked the platform after an interrupted publish: nothing went live, safe to retry."""
    p = get_or_404(db, PublicationRecord, pub_id)
    _transition(db, p, ("verify_required",), "failed")
    p.platform_state = {}
    audit(db, "publication.confirm_not_published", user.id, "publication", p.id)
    db.commit()
    return ser_pub(p)


@router.post("/publications/{pub_id}/mark-published")
def mark_published(pub_id: int, body: MarkPublishedIn, user: User = AuthUser, db: Session = Depends(get_db)):
    p = get_or_404(db, PublicationRecord, pub_id)
    _transition(db, p, ("exported", "verify_required", "failed", "approved", "awaiting_approval"), "published")
    p.published_at, p.external_url, p.external_post_id = utcnow(), body.url, body.external_id
    p.last_error = None
    audit(db, "publication.mark_published", user.id, "publication", p.id, {"manual": True})
    db.commit()
    return ser_pub(p)


@router.post("/publications/{pub_id}/cancel")
def cancel_pub(pub_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    p = get_or_404(db, PublicationRecord, pub_id)
    _transition(db, p, ("planned", "preparing", "awaiting_approval", "approved", "failed", "missed"), "cancelled")
    db.commit()
    return ser_pub(p)


# ------------------------------------------------------------------ public media (Instagram Login variant only)
@media_router.get("/media/public/{token}.mp4")
def public_media(token: str, db: Session = Depends(get_db)):
    vid = check_public_media_token(token)
    if vid is None:
        raise HTTPException(404)
    v = db.get(RenderedVideo, vid)
    if v is None or v.review_status != "approved":
        raise HTTPException(404)
    return _file(v.path, "video/mp4")
