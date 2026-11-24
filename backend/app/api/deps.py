"""Shared API helpers: auth dependency, serializers, small utilities."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import current_user
from app.core.db import get_db
from app.services.publishing import limits as _limits
from app.models import (Application, ApplicationAsset, ApplicationSource, ComfyWorkflow, PublicationRecord,
                        PublishingRule, RenderedVideo, ScheduledJob, SocialAccount, User)

__all__ = ["get_db", "current_user", "Depends", "Session", "User", "get_or_404", "iso", "ser_app", "ser_source",
           "ser_asset", "ser_account", "ser_rule", "ser_pub", "ser_job", "ser_video", "ser_workflow", "to_utc_naive"]


def get_or_404(db: Session, model, id_: Any, what: str | None = None):
    obj = db.get(model, id_)
    if obj is None:
        raise HTTPException(404, f"{what or model.__name__} not found")
    return obj


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def to_utc_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=None)


def ser_app(a: Application, extra: dict | None = None) -> dict:
    d = {c: getattr(a, c) for c in ("id", "name", "slug", "description", "category", "website_url", "target_audience",
                                    "main_features", "selling_points", "tone", "brand_colors", "language", "target_countries",
                                    "active", "paused", "publishing_mode", "content_strategy", "video_settings", "ai_budget", "cta_text")}
    d["created_at"], d["updated_at"] = iso(a.created_at), iso(a.updated_at)
    if extra:
        d.update(extra)
    return d


def ser_source(s: ApplicationSource) -> dict:
    cfg = dict(s.config or {})
    return {"id": s.id, "application_id": s.application_id, "source_type": s.source_type, "location": s.location,
            "branch": s.branch, "config": cfg, "enabled": s.enabled, "last_scanned_at": iso(s.last_scanned_at),
            "last_commit": s.last_commit, "last_error": s.last_error}


def ser_asset(a: ApplicationAsset) -> dict:
    return {"id": a.id, "application_id": a.application_id, "kind": a.kind, "original_name": a.original_name, "mime": a.mime,
            "width": a.width, "height": a.height, "duration": a.duration, "description": a.description,
            "license_note": a.license_note, "url": f"/api/assets/{a.id}/file", "created_at": iso(a.created_at)}


def ser_account(a: SocialAccount, caps: dict | None = None) -> dict:
    o = a.oauth
    return {"id": a.id, "application_id": a.application_id, "platform": a.platform, "display_name": a.display_name,
            "external_account_id": a.external_account_id, "status": a.status, "paused": a.paused,
            "publishing_mode": a.publishing_mode, "settings": a.settings or {}, "capabilities": caps or a.capabilities or {},
            "last_published_at": iso(a.last_published_at), "last_error": a.last_error,
            "oauth": {"status": o.status, "scopes": o.scopes, "expires_at": iso(o.expires_at),
                      "refresh_expires_at": iso(o.refresh_expires_at), "last_refresh_at": iso(o.last_refresh_at)} if o else None}


def ser_rule(r: PublishingRule) -> dict:
    d = {c: getattr(r, c) for c in ("id", "application_id", "social_account_id", "name", "enabled", "frequency",
                                    "posts_per_period", "days_of_week", "posting_times", "window_start", "window_end",
                                    "timezone", "start_date", "end_date", "blackout_periods", "max_per_day",
                                    "generation_lead_hours", "missed_policy", "grace_minutes", "reuse_across_platforms",
                                    "content_types")}
    return d


def ser_video(v: RenderedVideo | None) -> dict | None:
    if v is None:
        return None
    return {"id": v.id, "application_id": v.application_id, "status": v.status, "review_status": v.review_status,
            "width": v.width, "height": v.height, "fps": v.fps, "duration": v.duration, "size_bytes": v.size_bytes,
            "renderer": v.renderer, "qc_report": v.qc_report, "render_seconds": v.render_seconds, "gpu_seconds": v.gpu_seconds,
            "error": v.error, "video_script_id": v.video_script_id, "storyboard_id": v.storyboard_id,
            "content_idea_id": v.content_idea_id, "created_at": iso(v.created_at),
            "url": f"/api/videos/{v.id}/file" if v.path else None,
            "thumbnail_url": f"/api/videos/{v.id}/thumbnail" if v.thumbnail_path else None,
            # live per-platform fit (size/length limits), so older videos and changed limits are covered too
            "platforms": _limits.eligibility(v.duration, v.size_bytes) if v.duration else None}


def ser_pub(p: PublicationRecord, account: SocialAccount | None = None, app: Application | None = None) -> dict:
    return {"id": p.id, "application_id": p.application_id, "social_account_id": p.social_account_id,
            "publishing_rule_id": p.publishing_rule_id, "platform": p.platform, "scheduled_for": iso(p.scheduled_for),
            "status": p.status, "rendered_video_id": p.rendered_video_id, "title": p.title, "caption": p.caption,
            "hashtags": p.hashtags, "published_at": iso(p.published_at), "external_post_id": p.external_post_id,
            "external_url": p.external_url, "attempts": p.attempts, "last_error": p.last_error, "export_path": p.export_path,
            "manual": p.manual, "approved_at": iso(p.approved_at),
            "account_name": account.display_name if account else None, "application_name": app.name if app else None,
            "thumbnail_url": f"/api/videos/{p.rendered_video_id}/thumbnail" if p.rendered_video_id else None}


def ser_job(j: ScheduledJob) -> dict:
    return {"id": j.id, "job_type": j.job_type, "lane": j.lane, "status": j.status, "priority": j.priority, "payload": j.payload,
            "application_id": j.application_id, "run_at": iso(j.run_at), "attempts": j.attempts, "max_attempts": j.max_attempts,
            "last_error": j.last_error, "blocked_reason": j.blocked_reason, "progress": j.progress,
            "progress_message": j.progress_message, "result": j.result, "created_at": iso(j.created_at),
            "started_at": iso(j.started_at), "finished_at": iso(j.finished_at), "parent_job_id": j.parent_job_id}


def ser_workflow(w: ComfyWorkflow, include_workflow: bool = False) -> dict:
    d = {"id": w.id, "name": w.name, "description": w.description, "kind": w.kind, "output_type": w.output_type,
         "mappings": w.mappings, "defaults": w.defaults, "application_id": w.application_id, "content_type": w.content_type,
         "enabled": w.enabled, "is_default": w.is_default, "timeout_seconds": w.timeout_seconds, "validated": w.validated,
         "validation_report": w.validation_report, "node_count": len(w.workflow or {}), "updated_at": iso(w.updated_at),
         "nodes": [{"id": k, "class_type": v.get("class_type"), "inputs": [n for n, val in (v.get("inputs") or {}).items()
                                                                            if not isinstance(val, list)]}
                   for k, v in (w.workflow or {}).items() if isinstance(v, dict)]}
    if include_workflow:
        d["workflow"] = w.workflow
    return d


AuthUser = Depends(current_user)
_ = User
