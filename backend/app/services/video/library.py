"""Content Library operations shared by the API and the importer."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ContentIdea, PublicationRecord, RenderedVideo, VideoScript
from app.services.video import qc

LIVE_STATUSES = ("published", "publishing")
QC_ERROR_PREFIXES = ("Failed QC", "QC failed")


def is_imported(v: RenderedVideo) -> bool:
    return (v.renderer or "").startswith("imported")


def is_published(db: Session, v: RenderedVideo) -> bool:
    return db.scalar(select(PublicationRecord.id).where(PublicationRecord.rendered_video_id == v.id,
                                                        PublicationRecord.status.in_(LIVE_STATUSES))) is not None


def is_replaceable(db: Session, v: RenderedVideo) -> bool:
    """A copy the user has no use for: failed QC or rejected in review, and never published."""
    return (v.status == "failed" or v.review_status == "rejected") and not is_published(db, v)


def delete_video_record(db: Session, v: RenderedVideo) -> list[str]:
    """Remove the video row (and an import's own idea/script) without committing. Returns the storage-relative
    files to delete *after* the caller commits (see storage_cleanup.remove_files)."""
    files = [f for f in (v.path, v.thumbnail_path) if f]
    for p in db.scalars(select(PublicationRecord).where(PublicationRecord.rendered_video_id == v.id)):
        p.rendered_video_id = None
        if p.status in ("awaiting_approval", "approved"):
            p.status = "planned"
    script_id, idea_id, imported = v.video_script_id, v.content_idea_id, is_imported(v)
    db.delete(v)
    db.flush()
    if imported:  # an import's idea/script exist only to carry its captions
        others = db.scalar(select(RenderedVideo.id).where(RenderedVideo.video_script_id == script_id)) if script_id else None
        if script_id and not others:
            s = db.get(VideoScript, script_id)
            if s:
                db.delete(s)
                db.flush()  # sessions don't autoflush: make the idea check below see the deletion
        if idea_id and not db.scalar(select(VideoScript.id).where(VideoScript.content_idea_id == idea_id)):
            idea = db.get(ContentIdea, idea_id)
            if idea:
                db.delete(idea)
        db.flush()
    return files


def refresh_caption_qc(db: Session, script: VideoScript) -> list[RenderedVideo]:
    """Re-run the caption check for every video of `script` after its captions change, so a video that only
    failed because its caption was empty becomes publishable once one is added (and vice versa)."""
    changed = []
    for v in db.scalars(select(RenderedVideo).where(RenderedVideo.video_script_id == script.id)):
        if not v.qc_report or not v.path or v.status not in ("ready", "failed"):
            continue
        if v.status == "failed" and v.error and not v.error.startswith(QC_ERROR_PREFIXES):
            continue  # a render error, not a QC verdict
        v.qc_report = qc.refresh_caption_check(v.qc_report, script, caption_required=not is_imported(v))
        ok = v.qc_report["ok"]
        v.status = "ready" if ok else "failed"
        v.error = None if ok else "Failed QC: " + ", ".join(c["name"] for c in v.qc_report["checks"]
                                                              if not c["ok"] and c["severity"] == "error")
        changed.append(v)
    return changed
