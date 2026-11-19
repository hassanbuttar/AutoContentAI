"""SQLAlchemy models. All datetimes are stored as naive UTC."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# ---------------------------------------------------------------- users / settings / audit
class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    session_version: Mapped[int] = mapped_column(Integer, default=1)  # bump to revoke sessions


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SecretValue(Base):
    __tablename__ = "secret_values"
    name: Mapped[str] = mapped_column(String(200), primary_key=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    hint: Mapped[str | None] = mapped_column(String(16))  # last 4 chars for UI display
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JSON)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    level: Mapped[str] = mapped_column(String(16), default="info")  # info|warning|error
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str | None] = mapped_column(String(300))
    read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


# ---------------------------------------------------------------- applications
class Application(Base, TimestampMixin):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(100), default="")
    website_url: Mapped[str | None] = mapped_column(String(500))
    target_audience: Mapped[str] = mapped_column(Text, default="")
    main_features: Mapped[list] = mapped_column(JSON, default=list)
    selling_points: Mapped[list] = mapped_column(JSON, default=list)
    tone: Mapped[str] = mapped_column(String(100), default="friendly, confident")
    brand_colors: Mapped[list] = mapped_column(JSON, default=list)  # ["#4F46E5", "#22D3EE"]
    language: Mapped[str] = mapped_column(String(16), default="en")
    target_countries: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    publishing_mode: Mapped[str] = mapped_column(String(16), default="semi")  # manual|semi|auto
    content_strategy: Mapped[dict] = mapped_column(JSON, default=dict)
    video_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    ai_budget: Mapped[dict] = mapped_column(JSON, default=dict)
    cta_text: Mapped[str] = mapped_column(String(200), default="")

    sources: Mapped[list["ApplicationSource"]] = relationship(back_populates="application", cascade="all, delete-orphan")
    assets: Mapped[list["ApplicationAsset"]] = relationship(back_populates="application", cascade="all, delete-orphan")
    knowledge: Mapped["ApplicationKnowledge | None"] = relationship(back_populates="application", cascade="all, delete-orphan", uselist=False)
    social_accounts: Mapped[list["SocialAccount"]] = relationship(back_populates="application", cascade="all, delete-orphan")


class ApplicationSource(Base, TimestampMixin):
    __tablename__ = "application_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[str] = mapped_column(String(32))  # local_folder|local_git|github|gitlab|docs_folder|changelog|release_notes|spec_doc|manual|read_api
    location: Mapped[str] = mapped_column(String(1000), default="")  # path, owner/repo, URL
    branch: Mapped[str | None] = mapped_column(String(200))
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # include/exclude globs, manual text, api headers secret name
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_commit: Mapped[str | None] = mapped_column(String(100))
    last_fingerprint: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(Text)

    application: Mapped[Application] = relationship(back_populates="sources")


class ApplicationAsset(Base):
    __tablename__ = "application_assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # logo|screenshot|demo_video|music|intro|outro
    path: Mapped[str] = mapped_column(String(1000))  # relative to storage dir
    original_name: Mapped[str] = mapped_column(String(300), default="")
    mime: Mapped[str] = mapped_column(String(100), default="")
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str] = mapped_column(Text, default="")  # what the screenshot shows (helps AI pick)
    license_note: Mapped[str] = mapped_column(String(500), default="")  # required for music
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    application: Mapped[Application] = relationship(back_populates="assets")


class ApplicationKnowledge(Base, TimestampMixin):
    __tablename__ = "application_knowledge"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    summary: Mapped[str] = mapped_column(Text, default="")
    profile: Mapped[dict] = mapped_column(JSON, default=dict)  # target_users, usps, architecture, opportunities, limitations, themes
    facts: Mapped[list] = mapped_column(JSON, default=list)  # [{id,text,kind,status,source,first_seen,times_featured,last_featured_at}]
    generated_by: Mapped[str] = mapped_column(String(32), default="local")  # local|claude
    last_full_analysis_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_incremental_at: Mapped[datetime | None] = mapped_column(DateTime)

    application: Mapped[Application] = relationship(back_populates="knowledge")


class ProjectSnapshot(Base):
    __tablename__ = "project_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    source_id: Mapped[int] = mapped_column(ForeignKey("application_sources.id", ondelete="CASCADE"))
    commit_sha: Mapped[str | None] = mapped_column(String(100))
    fingerprint: Mapped[str] = mapped_column(String(100))
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)  # {"files": {path: hash}, "docs": {path: hash}}
    doc_texts: Mapped[dict] = mapped_column(JSON, default=dict)  # small doc snapshots for incremental diffing
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (Index("ix_snapshots_source_created", "source_id", "created_at"),)


class ProjectChangeEvent(Base):
    __tablename__ = "project_change_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("application_sources.id", ondelete="SET NULL"))
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("project_snapshots.id", ondelete="SET NULL"))
    change_type: Mapped[str] = mapped_column(String(32))  # commit|docs|release|changelog|files|manual
    ref: Mapped[str | None] = mapped_column(String(200))  # commit sha / tag / path
    title: Mapped[str] = mapped_column(String(500))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    category: Mapped[str] = mapped_column(String(32), default="other")  # feature|improvement|fix|docs|release|chore|other
    significance: Mapped[float] = mapped_column(Float, default=0.0)
    user_facing: Mapped[bool] = mapped_column(Boolean, default=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)  # merged into knowledge profile
    promoted: Mapped[bool] = mapped_column(Boolean, default=False)  # used in content
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (
        Index("ix_changes_app_processed", "application_id", "processed"),
        UniqueConstraint("application_id", "change_type", "ref", name="uq_change_ref"),
    )


# ---------------------------------------------------------------- social
class SocialAccount(Base, TimestampMixin):
    __tablename__ = "social_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(32))  # youtube|instagram|tiktok|export
    display_name: Mapped[str] = mapped_column(String(200))
    external_account_id: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default="disconnected")  # disconnected|connected|needs_reauth|error|export_only
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    publishing_mode: Mapped[str | None] = mapped_column(String(16))  # overrides application
    settings: Mapped[dict] = mapped_column(JSON, default=dict)  # privacy, auth_variant, public_media_base_url, ...
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    last_published_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)

    application: Mapped[Application] = relationship(back_populates="social_accounts")
    oauth: Mapped["OAuthConnection | None"] = relationship(back_populates="account", cascade="all, delete-orphan", uselist=False)
    rules: Mapped[list["PublishingRule"]] = relationship(back_populates="account", cascade="all, delete-orphan")


class OAuthConnection(Base, TimestampMixin):
    __tablename__ = "oauth_connections"
    id: Mapped[int] = mapped_column(primary_key=True)
    social_account_id: Mapped[int] = mapped_column(ForeignKey("social_accounts.id", ondelete="CASCADE"), unique=True)
    provider: Mapped[str] = mapped_column(String(32))
    token_secret_name: Mapped[str] = mapped_column(String(200))  # key into secret_values
    scopes: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(32), default="active")  # active|expired|revoked|error
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)

    account: Mapped[SocialAccount] = relationship(back_populates="oauth")


class OAuthState(Base):
    __tablename__ = "oauth_states"
    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    social_account_id: Mapped[int] = mapped_column(ForeignKey("social_accounts.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String(32))
    code_verifier: Mapped[str] = mapped_column(String(200))  # PKCE verifier (single use, short TTL)
    redirect_uri: Mapped[str] = mapped_column(String(500))
    user_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class PublishingRule(Base, TimestampMixin):
    __tablename__ = "publishing_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    social_account_id: Mapped[int] = mapped_column(ForeignKey("social_accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    frequency: Mapped[str] = mapped_column(String(16), default="daily")  # daily|weekly
    posts_per_period: Mapped[int] = mapped_column(Integer, default=1)
    days_of_week: Mapped[list] = mapped_column(JSON, default=lambda: [0, 1, 2, 3, 4, 5, 6])  # Mon=0
    posting_times: Mapped[list] = mapped_column(JSON, default=list)  # ["09:00","18:30"]; empty => distribute
    window_start: Mapped[str] = mapped_column(String(5), default="09:00")
    window_end: Mapped[str] = mapped_column(String(5), default="21:00")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    start_date: Mapped[str | None] = mapped_column(String(10))  # YYYY-MM-DD campaign start
    end_date: Mapped[str | None] = mapped_column(String(10))
    blackout_periods: Mapped[list] = mapped_column(JSON, default=list)  # [{"start": iso, "end": iso}] local time
    max_per_day: Mapped[int | None] = mapped_column(Integer)
    generation_lead_hours: Mapped[int] = mapped_column(Integer, default=12)
    missed_policy: Mapped[str] = mapped_column(String(16), default="publish_late")  # skip|publish_late|next_slot
    grace_minutes: Mapped[int] = mapped_column(Integer, default=120)
    reuse_across_platforms: Mapped[bool] = mapped_column(Boolean, default=True)
    content_types: Mapped[list] = mapped_column(JSON, default=list)  # empty => app strategy

    account: Mapped[SocialAccount] = relationship(back_populates="rules")


class PublicationRecord(Base, TimestampMixin):
    __tablename__ = "publication_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    social_account_id: Mapped[int] = mapped_column(ForeignKey("social_accounts.id", ondelete="CASCADE"), index=True)
    publishing_rule_id: Mapped[int | None] = mapped_column(ForeignKey("publishing_rules.id", ondelete="SET NULL"))
    platform: Mapped[str] = mapped_column(String(32))
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, index=True)
    # planned -> preparing -> awaiting_approval -> approved -> publishing -> published
    #   also: failed, missed, cancelled, exported, verify_required, rejected
    status: Mapped[str] = mapped_column(String(32), default="planned", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    rendered_video_id: Mapped[int | None] = mapped_column(ForeignKey("rendered_videos.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(300), default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    approved_by: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    external_post_id: Mapped[str | None] = mapped_column(String(200))
    external_url: Mapped[str | None] = mapped_column(String(500))
    platform_state: Mapped[dict] = mapped_column(JSON, default=dict)  # container/publish ids saved BEFORE final publish
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    export_path: Mapped[str | None] = mapped_column(String(1000))
    manual: Mapped[bool] = mapped_column(Boolean, default=False)  # created by manual override, not a rule
    __table_args__ = (UniqueConstraint("publishing_rule_id", "scheduled_for", name="uq_rule_slot"),)

    account: Mapped[SocialAccount] = relationship()
    video: Mapped["RenderedVideo | None"] = relationship(foreign_keys=[rendered_video_id])


# ---------------------------------------------------------------- jobs
class ScheduledJob(Base, TimestampMixin):
    __tablename__ = "scheduled_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    lane: Mapped[str] = mapped_column(String(16), default="default")
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|retry_wait|blocked|succeeded|failed|cancelled
    priority: Mapped[int] = mapped_column(Integer, default=100)  # lower runs first
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    run_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    last_error: Mapped[str | None] = mapped_column(Text)
    blocked_reason: Mapped[str | None] = mapped_column(String(300))
    locked_by: Mapped[str | None] = mapped_column(String(100))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    progress_message: Mapped[str] = mapped_column(String(500), default="")
    result: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    parent_job_id: Mapped[int | None] = mapped_column(ForeignKey("scheduled_jobs.id", ondelete="SET NULL"))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (Index("ix_jobs_status_run_at", "status", "lane", "run_at"),)


class JobAttempt(Base):
    __tablename__ = "job_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("scheduled_jobs.id", ondelete="CASCADE"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16))  # running|succeeded|failed|interrupted|blocked
    error: Mapped[str | None] = mapped_column(Text)
    traceback: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_s: Mapped[float | None] = mapped_column(Float)


class LogEntry(Base):
    __tablename__ = "log_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("scheduled_jobs.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


# ---------------------------------------------------------------- content
class ContentIdea(Base):
    __tablename__ = "content_ideas"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    content_type: Mapped[str] = mapped_column(String(64))
    topic: Mapped[str] = mapped_column(String(500))
    angle: Mapped[str] = mapped_column(Text, default="")
    fingerprint: Mapped[str] = mapped_column(String(64))
    fact_ids: Mapped[list] = mapped_column(JSON, default=list)
    change_event_ids: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(32), default="local")  # claude|local|manual
    status: Mapped[str] = mapped_column(String(32), default="planned")  # planned|rendered|used|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (Index("ix_ideas_app_fp", "application_id", "fingerprint"),)


class VideoScript(Base):
    __tablename__ = "video_scripts"
    id: Mapped[int] = mapped_column(primary_key=True)
    content_idea_id: Mapped[int] = mapped_column(ForeignKey("content_ideas.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    hook: Mapped[str] = mapped_column(Text, default="")
    script_text: Mapped[str] = mapped_column(Text, default="")
    cta: Mapped[str] = mapped_column(String(300), default="")
    language: Mapped[str] = mapped_column(String(16), default="en")
    duration_seconds: Mapped[float] = mapped_column(Float, default=30)
    social_content: Mapped[dict] = mapped_column(JSON, default=dict)
    claims: Mapped[list] = mapped_column(JSON, default=list)
    claim_check: Mapped[dict] = mapped_column(JSON, default=dict)
    plan_json: Mapped[dict] = mapped_column(JSON, default=dict)  # full validated AI/local plan
    ai_usage_id: Mapped[int | None] = mapped_column(ForeignKey("ai_usage_records.id", ondelete="SET NULL"))
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Storyboard(Base):
    __tablename__ = "storyboards"
    id: Mapped[int] = mapped_column(primary_key=True)
    video_script_id: Mapped[int] = mapped_column(ForeignKey("video_scripts.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    scenes: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class GeneratedAsset(Base):
    __tablename__ = "generated_assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    storyboard_id: Mapped[int | None] = mapped_column(ForeignKey("storyboards.id", ondelete="CASCADE"), index=True)
    scene_number: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))  # image|video|audio|card|frame
    source: Mapped[str] = mapped_column(String(32))  # comfyui|screenshot|local_card|tts|demo_video
    path: Mapped[str] = mapped_column(String(1000))
    workflow_id: Mapped[int | None] = mapped_column(ForeignKey("comfyui_workflows.id", ondelete="SET NULL"))
    prompt: Mapped[str] = mapped_column(Text, default="")
    seed: Mapped[int | None] = mapped_column(Integer)
    comfy_prompt_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default="ready")
    error: Mapped[str | None] = mapped_column(Text)
    gpu_seconds: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RenderedVideo(Base, TimestampMixin):
    __tablename__ = "rendered_videos"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    content_idea_id: Mapped[int | None] = mapped_column(ForeignKey("content_ideas.id", ondelete="SET NULL"))
    video_script_id: Mapped[int | None] = mapped_column(ForeignKey("video_scripts.id", ondelete="SET NULL"))
    storyboard_id: Mapped[int | None] = mapped_column(ForeignKey("storyboards.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), default="rendering")  # rendering|ready|failed
    review_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|rejected
    path: Mapped[str | None] = mapped_column(String(1000))
    thumbnail_path: Mapped[str | None] = mapped_column(String(1000))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    duration: Mapped[float | None] = mapped_column(Float)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    renderer: Mapped[str] = mapped_column(String(64), default="")
    qc_report: Mapped[dict] = mapped_column(JSON, default=dict)
    render_seconds: Mapped[float | None] = mapped_column(Float)
    gpu_seconds: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    render_job_id: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("application_id", "sha256", name="uq_video_hash"),)


class ContentPerformance(Base):
    __tablename__ = "content_performance"
    id: Mapped[int] = mapped_column(primary_key=True)
    publication_record_id: Mapped[int] = mapped_column(ForeignKey("publication_records.id", ondelete="CASCADE"), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    views: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    shares: Mapped[int | None] = mapped_column(Integer)
    unavailable: Mapped[dict] = mapped_column(JSON, default=dict)  # metric -> reason
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class ComfyWorkflow(Base, TimestampMixin):
    __tablename__ = "comfyui_workflows"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(32), default="txt2img")  # txt2img|img2vid|txt2vid
    workflow: Mapped[dict] = mapped_column(JSON)  # API format
    mappings: Mapped[dict] = mapped_column(JSON, default=dict)
    defaults: Mapped[dict] = mapped_column(JSON, default=dict)  # width/height/length/fps/steps/negative_prompt
    output_type: Mapped[str] = mapped_column(String(16), default="image")  # image|video
    application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    content_type: Mapped[str | None] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=600)
    validated: Mapped[bool] = mapped_column(Boolean, default=False)
    validation_report: Mapped[dict] = mapped_column(JSON, default=dict)


class AIUsageRecord(Base):
    __tablename__ = "ai_usage_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id", ondelete="SET NULL"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("scheduled_jobs.id", ondelete="SET NULL"))
    purpose: Mapped[str] = mapped_column(String(64))  # profile_initial|profile_update|content_plan|connectivity_test
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(100))
    stop_reason: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
