from __future__ import annotations

import mimetypes
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from sqlalchemy import select

from app.api.deps import (AuthUser, Depends, Session, User, get_db, get_or_404, iso, ser_app, ser_asset, ser_source)
from app.core import secrets
from app.core.config import get_settings
from app.core.paths import PathNotAllowed, check_allowed_project_path, safe_filename, slugify, storage_path, to_storage_rel
from app.models import (Application, ApplicationAsset, ApplicationKnowledge, ApplicationSource, ProjectChangeEvent,
                        PublicationRecord, RenderedVideo, SocialAccount)
from app.schemas.api import ApplicationIn, ApplicationPatch, AssetPatch, KnowledgePatch, SourceIn
from app.services.audit import audit
from app.services.knowledge import scanner
from app.services.settings_service import get_section
from app.workers.queue import enqueue

router = APIRouter(prefix="/api", tags=["applications"])
ALLOWED_UPLOADS = {"image/png", "image/jpeg", "image/webp", "video/mp4", "video/quicktime", "video/webm", "audio/mpeg",
                   "audio/wav", "audio/x-wav", "audio/mp4", "audio/aac", "audio/ogg"}
MAX_UPLOAD = 300 * 1024 * 1024


def _app_extra(db: Session, a: Application) -> dict:
    k = a.knowledge
    return {"knowledge": {"version": k.version, "facts": len([f for f in k.facts if f.get("active", True)]),
                          "generated_by": k.generated_by, "updated_at": iso(k.updated_at)} if k else None,
            "sources": len(a.sources), "accounts": len(a.social_accounts),
            "videos": len(list(db.scalars(select(RenderedVideo.id).where(RenderedVideo.application_id == a.id)))),
            "published": len(list(db.scalars(select(PublicationRecord.id).where(PublicationRecord.application_id == a.id,
                                                                                PublicationRecord.status == "published"))))}


@router.get("/applications")
def list_apps(user: User = AuthUser, db: Session = Depends(get_db)):
    return [ser_app(a, _app_extra(db, a)) for a in db.scalars(select(Application).order_by(Application.name))]


@router.post("/applications", status_code=201)
def create_app(body: ApplicationIn, user: User = AuthUser, db: Session = Depends(get_db)):
    base = slugify(body.name)
    slug, i = base, 2
    while db.scalar(select(Application.id).where(Application.slug == slug)):
        slug, i = f"{base}-{i}", i + 1
    data = body.model_dump()
    data["website_url"] = str(body.website_url) if body.website_url else None
    a = Application(slug=slug, **data)
    db.add(a)
    db.flush()
    audit(db, "application.create", user.id, "application", a.id)
    db.commit()
    return ser_app(a, _app_extra(db, a))


@router.get("/applications/{app_id}")
def get_app(app_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    a = get_or_404(db, Application, app_id)
    return ser_app(a, _app_extra(db, a))


@router.patch("/applications/{app_id}")
def patch_app(app_id: int, body: ApplicationPatch, user: User = AuthUser, db: Session = Depends(get_db)):
    a = get_or_404(db, Application, app_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        if k == "website_url":
            v = str(v) if v else None
        if v is not None or k in ("website_url",):
            setattr(a, k, v)
    audit(db, "application.update", user.id, "application", a.id, {"fields": list(body.model_dump(exclude_unset=True))})
    db.commit()
    return ser_app(a, _app_extra(db, a))


@router.delete("/applications/{app_id}")
def delete_app(app_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    a = get_or_404(db, Application, app_id)
    for acc in a.social_accounts:
        secrets.delete_secret(db, f"oauth.account.{acc.id}")
    for s in a.sources:
        secrets.delete_secret(db, f"source.{s.id}.token")
    audit(db, "application.delete", user.id, "application", a.id, {"name": a.name})
    db.delete(a)
    db.commit()
    return {"ok": True}


@router.post("/applications/{app_id}/analyze")
def analyze(app_id: int, full: bool = False, user: User = AuthUser, db: Session = Depends(get_db)):
    a = get_or_404(db, Application, app_id)
    job, created = enqueue(db, "analyze_application", {"application_id": a.id, "force_full": full, "trigger": "manual"},
                           idempotency_key=f"analyze:{a.id}:manual:{uuid.uuid4().hex[:8]}", application_id=a.id, priority=30)
    db.commit()
    return {"job_id": job.id}


# ------------------------------------------------------------------ sources
@router.get("/applications/{app_id}/sources")
def list_sources(app_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    a = get_or_404(db, Application, app_id)
    return [ser_source(s) for s in a.sources]


def _validate_source(db: Session, body: SourceIn) -> dict:
    roots = get_section(db, "security")["allowed_project_roots"]
    info: dict = {}
    if body.source_type in ("local_folder", "local_git", "docs_folder", "changelog", "release_notes", "spec_doc"):
        try:
            p = check_allowed_project_path(body.location, roots)
        except PathNotAllowed as exc:
            raise HTTPException(400, str(exc)) from exc
        info["is_git"] = p.is_dir() and scanner.is_git_repo(p)
        if body.source_type == "local_git" and not info["is_git"]:
            raise HTTPException(400, "This folder is not a git repository (use 'local_folder' instead)")
    elif body.source_type == "github":
        import re

        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", body.location):
            raise HTTPException(400, "GitHub repository must be owner/name")
    elif body.source_type == "gitlab":
        if not body.location or "/" not in body.location:
            raise HTTPException(400, "GitLab project must be group/name (or numeric id)")
    elif body.source_type == "manual":
        if not (body.config or {}).get("text", "").strip():
            raise HTTPException(400, "Manual source needs feature text")
        if len(body.config["text"]) > 50_000:
            raise HTTPException(400, "Manual text too long (50k chars max)")
    return info


@router.post("/applications/{app_id}/sources", status_code=201)
def add_source(app_id: int, body: SourceIn, user: User = AuthUser, db: Session = Depends(get_db)):
    a = get_or_404(db, Application, app_id)
    info = _validate_source(db, body)
    s = ApplicationSource(application_id=a.id, source_type=body.source_type, location=body.location.strip(),
                          branch=body.branch or None, config=body.config, enabled=body.enabled)
    db.add(s)
    db.flush()
    if body.token:
        secrets.set_secret(db, f"source.{s.id}.token", body.token)
    audit(db, "source.create", user.id, "source", s.id, {"type": s.source_type})
    db.commit()
    return {**ser_source(s), **info}


@router.patch("/sources/{source_id}")
def patch_source(source_id: int, body: SourceIn, user: User = AuthUser, db: Session = Depends(get_db)):
    s = get_or_404(db, ApplicationSource, source_id)
    _validate_source(db, body)
    if body.location != s.location or body.branch != s.branch:
        s.last_commit = None  # new location: start history fresh
    s.source_type, s.location, s.branch, s.config, s.enabled = body.source_type, body.location.strip(), body.branch, body.config, body.enabled
    if body.token:
        secrets.set_secret(db, f"source.{s.id}.token", body.token)
    db.commit()
    return ser_source(s)


@router.delete("/sources/{source_id}")
def delete_source(source_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    s = get_or_404(db, ApplicationSource, source_id)
    secrets.delete_secret(db, f"source.{s.id}.token")
    db.delete(s)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ assets
@router.get("/applications/{app_id}/assets")
def list_assets(app_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    get_or_404(db, Application, app_id)
    return [ser_asset(x) for x in db.scalars(select(ApplicationAsset).where(ApplicationAsset.application_id == app_id)
                                             .order_by(ApplicationAsset.kind, ApplicationAsset.id))]


@router.post("/applications/{app_id}/assets", status_code=201)
async def upload_asset(app_id: int, kind: str = Form(...), description: str = Form(""), license_note: str = Form(""),
                       file: UploadFile = File(...), user: User = AuthUser, db: Session = Depends(get_db)):
    a = get_or_404(db, Application, app_id)
    if kind not in ("logo", "screenshot", "demo_video", "music", "intro", "outro"):
        raise HTTPException(400, "Invalid asset kind")
    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    if mime not in ALLOWED_UPLOADS:
        raise HTTPException(400, f"Unsupported file type {mime}")
    if kind == "music" and not license_note.strip():
        raise HTTPException(400, "Music requires a license note (source / license) so only licensed audio is used")
    folder = get_settings().storage("projects", a.slug, "assets")
    dest = folder / f"{uuid.uuid4().hex[:10]}_{safe_filename(file.filename or 'upload')}"
    size = 0
    with open(dest, "wb") as fh:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD:
                fh.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "File too large (300 MB max)")
            fh.write(chunk)
    w = h = None
    dur = None
    if mime.startswith("image/"):
        try:
            with Image.open(dest) as im:
                im.verify()
            with Image.open(dest) as im:
                w, h = im.size
        except Exception as exc:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, "File is not a valid image") from exc
    elif mime.startswith(("video/", "audio/")):
        try:
            from app.services.video.ffmpeg import probe

            pr = probe(dest)
            w, h, dur = pr.width or None, pr.height or None, round(pr.duration, 2)
        except Exception as exc:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, "Could not read media file") from exc
    asset = ApplicationAsset(application_id=a.id, kind=kind, path=to_storage_rel(dest), original_name=(file.filename or "")[:300],
                             mime=mime, width=w, height=h, duration=dur, description=description[:1000], license_note=license_note[:500])
    db.add(asset)
    audit(db, "asset.upload", user.id, "application", a.id, {"kind": kind, "name": asset.original_name})
    db.commit()
    return ser_asset(asset)


@router.patch("/assets/{asset_id}")
def patch_asset(asset_id: int, body: AssetPatch, user: User = AuthUser, db: Session = Depends(get_db)):
    x = get_or_404(db, ApplicationAsset, asset_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(x, k, v)
    db.commit()
    return ser_asset(x)


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    x = get_or_404(db, ApplicationAsset, asset_id)
    path = x.path
    db.delete(x)
    db.commit()
    from app.services.storage_cleanup import remove_files

    pending = remove_files(db, [path])  # locked files are retried by a cleanup job instead of being orphaned
    return {"ok": True, "files_pending": len(pending)}


@router.get("/assets/{asset_id}/file")
def asset_file(asset_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    x = get_or_404(db, ApplicationAsset, asset_id)
    p = storage_path(x.path)
    if not p.exists():
        raise HTTPException(404, "File missing")
    return FileResponse(p, media_type=x.mime or None)


# ------------------------------------------------------------------ knowledge
@router.get("/applications/{app_id}/knowledge")
def get_knowledge(app_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    a = get_or_404(db, Application, app_id)
    k = a.knowledge
    events = list(db.scalars(select(ProjectChangeEvent).where(ProjectChangeEvent.application_id == a.id)
                             .order_by(ProjectChangeEvent.detected_at.desc(), ProjectChangeEvent.id.desc()).limit(100)))
    return {"knowledge": None if k is None else {"version": k.version, "summary": k.summary, "profile": k.profile, "facts": k.facts,
                                                  "generated_by": k.generated_by, "updated_at": iso(k.updated_at),
                                                  "last_full_analysis_at": iso(k.last_full_analysis_at),
                                                  "last_incremental_at": iso(k.last_incremental_at)},
            "changes": [{"id": e.id, "type": e.change_type, "title": e.title, "category": e.category, "significance": e.significance,
                         "user_facing": e.user_facing, "processed": e.processed, "promoted": e.promoted, "ref": e.ref,
                         "detected_at": iso(e.detected_at), "initial": (e.details or {}).get("initial", False)} for e in events]}


@router.patch("/applications/{app_id}/knowledge")
def patch_knowledge(app_id: int, body: KnowledgePatch, user: User = AuthUser, db: Session = Depends(get_db)):
    a = get_or_404(db, Application, app_id)
    k = a.knowledge
    if k is None:
        k = ApplicationKnowledge(application_id=a.id, version=0, facts=[], profile={}, generated_by="manual")
        a.knowledge = k
    if body.summary is not None:
        k.summary = body.summary
    if body.profile is not None:
        k.profile = {**(k.profile or {}), **body.profile}
    if body.facts is not None:
        from app.services.knowledge.analyzer import add_facts

        old = {f["id"]: f for f in k.facts or []}
        new_facts: list[dict] = []
        pending_new = []
        for f in body.facts:
            if f.id and f.id in old:
                new_facts.append({**old[f.id], "text": f.text, "kind": f.kind, "status": f.status, "active": f.active})
            else:
                pending_new.append(f.model_dump(exclude={"id", "active"}))
        # keep removed facts as inactive (history / fingerprints stay valid)
        kept = {f["id"] for f in new_facts}
        new_facts += [{**f, "active": False} for fid, f in old.items() if fid not in kept]
        add_facts(new_facts, pending_new, "owner")
        k.facts = new_facts
    k.version += 1
    audit(db, "knowledge.edit", user.id, "application", a.id)
    db.commit()
    return get_knowledge(app_id, user, db)


_ = SocialAccount
