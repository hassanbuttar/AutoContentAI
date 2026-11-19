"""Request validation models (all external input is validated here)."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator

HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
SOURCE_TYPES = Literal["local_folder", "local_git", "github", "gitlab", "docs_folder", "changelog", "release_notes",
                       "spec_doc", "manual", "read_api"]
PLATFORMS = Literal["youtube", "instagram", "tiktok", "export"]
MODES = Literal["manual", "semi", "auto"]


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class SetupIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=10, max_length=200)


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=200)


class ApplicationIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    category: str = Field(default="", max_length=100)
    website_url: HttpUrl | None = None
    target_audience: str = Field(default="", max_length=1000)
    main_features: list[str] = Field(default_factory=list, max_length=50)
    selling_points: list[str] = Field(default_factory=list, max_length=30)
    tone: str = Field(default="friendly, confident", max_length=100)
    brand_colors: list[str] = Field(default_factory=list, max_length=4)
    language: str = Field(default="en", max_length=16)
    target_countries: list[str] = Field(default_factory=list, max_length=50)
    active: bool = True
    paused: bool = False
    publishing_mode: MODES = "semi"
    content_strategy: dict = Field(default_factory=dict)
    video_settings: dict = Field(default_factory=dict)
    ai_budget: dict = Field(default_factory=dict)
    cta_text: str = Field(default="", max_length=200)

    @field_validator("brand_colors")
    @classmethod
    def _colors(cls, v):
        for c in v:
            if not COLOR.match(c):
                raise ValueError(f"Invalid color {c}; use #RRGGBB")
        return v

    @field_validator("main_features", "selling_points")
    @classmethod
    def _strip(cls, v):
        return [x.strip()[:300] for x in v if x and x.strip()]


class ApplicationPatch(ApplicationIn):
    name: str | None = Field(default=None, max_length=200)  # type: ignore[assignment]
    publishing_mode: MODES | None = None  # type: ignore[assignment]


class SourceIn(BaseModel):
    source_type: SOURCE_TYPES
    location: str = Field(default="", max_length=1000)
    branch: str | None = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9._/-]*$")
    config: dict = Field(default_factory=dict)
    enabled: bool = True
    token: str | None = Field(default=None, max_length=500)  # GitHub/GitLab token, stored encrypted


class AssetPatch(BaseModel):
    description: str | None = Field(default=None, max_length=1000)
    license_note: str | None = Field(default=None, max_length=500)
    kind: Literal["logo", "screenshot", "demo_video", "music", "intro", "outro"] | None = None


class FactIn(BaseModel):
    id: str | None = None
    text: str = Field(min_length=3, max_length=400)
    kind: Literal["feature", "benefit", "integration", "audience", "usp", "limitation"] = "feature"
    status: Literal["released", "beta", "planned"] = "released"
    active: bool = True


class KnowledgePatch(BaseModel):
    summary: str | None = Field(default=None, max_length=3000)
    facts: list[FactIn] | None = None
    profile: dict | None = None


class AccountIn(BaseModel):
    application_id: int
    platform: PLATFORMS
    display_name: str = Field(min_length=1, max_length=200)
    publishing_mode: MODES | None = None
    settings: dict = Field(default_factory=dict)
    export_only: bool = False


class AccountPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    publishing_mode: MODES | None = None
    settings: dict | None = None
    paused: bool | None = None
    export_only: bool | None = None


class RuleIn(BaseModel):
    social_account_id: int
    name: str = Field(default="", max_length=200)
    enabled: bool = True
    frequency: Literal["daily", "weekly"] = "daily"
    posts_per_period: int = Field(default=1, ge=1, le=20)
    days_of_week: list[int] = Field(default_factory=lambda: list(range(7)))
    posting_times: list[str] = Field(default_factory=list, max_length=20)
    window_start: str = "09:00"
    window_end: str = "21:00"
    timezone: str = "UTC"
    start_date: str | None = None
    end_date: str | None = None
    blackout_periods: list[dict] = Field(default_factory=list, max_length=50)
    max_per_day: int | None = Field(default=None, ge=1, le=50)
    generation_lead_hours: int = Field(default=12, ge=1, le=168)
    missed_policy: Literal["skip", "publish_late", "next_slot"] = "publish_late"
    grace_minutes: int = Field(default=120, ge=0, le=7 * 24 * 60)
    reuse_across_platforms: bool = True
    content_types: list[str] = Field(default_factory=list)

    @field_validator("days_of_week")
    @classmethod
    def _days(cls, v):
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("days_of_week must be 0 (Mon) .. 6 (Sun)")
        return sorted(set(v))

    @field_validator("posting_times")
    @classmethod
    def _times(cls, v):
        for t in v:
            if not HHMM.match(t):
                raise ValueError(f"Invalid time {t}; use HH:MM")
        return v

    @field_validator("window_start", "window_end")
    @classmethod
    def _hm(cls, v):
        if not HHMM.match(v):
            raise ValueError("Use HH:MM")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(v)
        except Exception as exc:
            raise ValueError(f"Unknown timezone {v}") from exc
        return v

    @field_validator("start_date", "end_date")
    @classmethod
    def _date(cls, v):
        if v:
            datetime.strptime(v, "%Y-%m-%d")
        return v or None


class GenerateIn(BaseModel):
    application_id: int
    content_type: str | None = Field(default=None, max_length=64)
    topic: str | None = Field(default=None, max_length=300)
    force_local: bool = False
    render: bool = True


class ScriptPatch(BaseModel):
    hook: str | None = Field(default=None, max_length=300)
    cta: str | None = Field(default=None, max_length=300)
    social_content: dict | None = None
    scenes: list[dict] | None = Field(default=None, max_length=12)


class RerenderIn(BaseModel):
    scenes: list[int] = Field(default_factory=list)
    all: bool = False


class PublicationPatch(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    caption: str | None = Field(default=None, max_length=2200)
    hashtags: list[str] | None = Field(default=None, max_length=30)
    scheduled_for: datetime | None = None  # UTC (naive or aware)


class PublicationCreate(BaseModel):
    video_id: int
    social_account_id: int
    scheduled_for: datetime | None = None  # None = now
    approve: bool = False


class MarkPublishedIn(BaseModel):
    url: str | None = Field(default=None, max_length=500)
    external_id: str | None = Field(default=None, max_length=200)


class WorkflowIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    kind: Literal["txt2img", "img2vid", "txt2vid"] = "txt2img"
    output_type: Literal["image", "video"] = "image"
    workflow: dict
    mappings: dict = Field(default_factory=dict)
    defaults: dict = Field(default_factory=dict)
    application_id: int | None = None
    content_type: str | None = None
    enabled: bool = True
    is_default: bool = False
    timeout_seconds: int = Field(default=600, ge=30, le=7200)


class WorkflowPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    mappings: dict | None = None
    defaults: dict | None = None
    enabled: bool | None = None
    is_default: bool | None = None
    timeout_seconds: int | None = Field(default=None, ge=30, le=7200)
    application_id: int | None = None
    content_type: str | None = None


class SecretIn(BaseModel):
    name: Literal["anthropic.api_key", "oauth.youtube.client_secret", "oauth.instagram.client_secret",
                  "oauth.tiktok.client_secret", "github.token", "gitlab.token"]
    value: str = Field(min_length=1, max_length=4000)
