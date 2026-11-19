"""AI contract: JSON schemas sent to Claude (structured outputs) and Pydantic models that
validate whatever comes back. Claude output is untrusted until it passes these models."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

FACT_KINDS = ["feature", "benefit", "integration", "audience", "usp", "limitation"]
FACT_STATUS = ["released", "beta", "planned"]
CONTENT_TYPES = ["feature_announcement", "walkthrough", "product_demo", "problem_solution", "tips_tutorial",
                 "benefits_use_cases", "release_highlights", "educational", "productivity_tip", "comparison", "evergreen"]
VISUAL_SOURCES = ["comfyui", "application_screenshot", "title_card"]
MOTIONS = ["zoom_in", "zoom_out", "pan_left", "pan_right", "static"]


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or list(props.keys()), "additionalProperties": False}


_S = {"type": "string"}
_SL = {"type": "array", "items": {"type": "string"}}
_FACT_IN = _obj({"text": _S, "kind": {"type": "string", "enum": FACT_KINDS}, "status": {"type": "string", "enum": FACT_STATUS},
                 "source": _S, "evidence": _S})

PROFILE_SCHEMA = _obj({
    "summary": _S,
    "target_users": _SL,
    "unique_selling_points": _SL,
    "architecture_summary": _S,
    "marketing_opportunities": _SL,
    "known_limitations": _SL,
    "recently_shipped": _SL,
    "facts": {"type": "array", "items": _FACT_IN},
})

DELTA_SCHEMA = _obj({
    "meaningful": {"type": "boolean"},
    "summary_update": _S,
    "new_facts": {"type": "array", "items": _FACT_IN},
    "updated_facts": {"type": "array", "items": _obj({"id": _S, "text": _S, "status": {"type": "string", "enum": FACT_STATUS}})},
    "retired_fact_ids": _SL,
    "recently_shipped": _SL,
    "marketing_opportunities": _SL,
})

_SCENE = _obj({
    "scene_number": {"type": "integer"},
    "duration_seconds": {"type": "number"},
    "narration": _S,
    "overlay_text": _S,
    "visual_source": {"type": "string", "enum": VISUAL_SOURCES},
    "visual_prompt": _S,
    "screenshot_asset_id": {"type": "integer"},
    "motion": {"type": "string", "enum": MOTIONS},
    "fact_ids": _SL,
})

PLAN_SCHEMA = _obj({
    "content_type": {"type": "string", "enum": CONTENT_TYPES},
    "topic": _S,
    "angle": _S,
    "target_audience": _S,
    "hook": _S,
    "duration_seconds": {"type": "number"},
    "fact_ids": _SL,
    "scenes": {"type": "array", "items": _SCENE},
    "cta": _S,
    "social": _obj({
        "instagram": _obj({"caption": _S, "hashtags": _SL}),
        "tiktok": _obj({"caption": _S, "hashtags": _SL}),
        "youtube": _obj({"title": _S, "description": _S, "tags": _SL}),
    }),
    "claims": {"type": "array", "items": _obj({"text": _S, "fact_ids": _SL})},
})


# ------------------------------------------------------------------ validation models
class FactIn(BaseModel):
    text: str = Field(min_length=3, max_length=400)
    kind: Literal["feature", "benefit", "integration", "audience", "usp", "limitation"] = "feature"
    status: Literal["released", "beta", "planned"] = "released"
    source: str = Field(default="", max_length=300)
    evidence: str = Field(default="", max_length=500)


class ProfileOut(BaseModel):
    summary: str = Field(min_length=10, max_length=2000)
    target_users: list[str] = Field(default_factory=list, max_length=12)
    unique_selling_points: list[str] = Field(default_factory=list, max_length=12)
    architecture_summary: str = Field(default="", max_length=1500)
    marketing_opportunities: list[str] = Field(default_factory=list, max_length=15)
    known_limitations: list[str] = Field(default_factory=list, max_length=12)
    recently_shipped: list[str] = Field(default_factory=list, max_length=15)
    facts: list[FactIn] = Field(default_factory=list, max_length=60)


class FactUpdate(BaseModel):
    id: str = Field(pattern=r"^F\d{3,4}$")
    text: str = Field(min_length=3, max_length=400)
    status: Literal["released", "beta", "planned"] = "released"


class DeltaOut(BaseModel):
    meaningful: bool
    summary_update: str = Field(default="", max_length=2000)
    new_facts: list[FactIn] = Field(default_factory=list, max_length=20)
    updated_facts: list[FactUpdate] = Field(default_factory=list, max_length=20)
    retired_fact_ids: list[str] = Field(default_factory=list, max_length=20)
    recently_shipped: list[str] = Field(default_factory=list, max_length=15)
    marketing_opportunities: list[str] = Field(default_factory=list, max_length=15)


class SceneIn(BaseModel):
    scene_number: int = Field(default=1, ge=1, le=20)
    duration_seconds: float = Field(ge=1.5, le=15)
    narration: str = Field(default="", max_length=400)
    overlay_text: str = Field(default="", max_length=90)
    visual_source: Literal["comfyui", "application_screenshot", "title_card"] = "title_card"
    visual_prompt: str = Field(default="", max_length=900)
    screenshot_asset_id: int = 0
    motion: Literal["zoom_in", "zoom_out", "pan_left", "pan_right", "static"] = "zoom_in"
    fact_ids: list[str] = Field(default_factory=list, max_length=10)


class PlatformCaption(BaseModel):
    caption: str = Field(default="", max_length=2200)
    hashtags: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("hashtags")
    @classmethod
    def _norm(cls, v: list[str]) -> list[str]:
        out = []
        for h in v:
            h = "#" + "".join(ch for ch in h.lstrip("#") if ch.isalnum() or ch == "_")
            if len(h) > 1 and h.lower() not in [x.lower() for x in out]:
                out.append(h[:60])
        return out


class YouTubeCopy(BaseModel):
    title: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=30)


class SocialCopy(BaseModel):
    instagram: PlatformCaption = Field(default_factory=PlatformCaption)
    tiktok: PlatformCaption = Field(default_factory=PlatformCaption)
    youtube: YouTubeCopy = Field(default_factory=YouTubeCopy)


class Claim(BaseModel):
    text: str = Field(max_length=400)
    fact_ids: list[str] = Field(default_factory=list)


class ContentPlan(BaseModel):
    content_type: str = "evergreen"
    topic: str = Field(min_length=3, max_length=300)
    angle: str = Field(default="", max_length=600)
    target_audience: str = Field(default="", max_length=300)
    hook: str = Field(default="", max_length=200)
    duration_seconds: float = Field(default=30, ge=8, le=120)
    fact_ids: list[str] = Field(default_factory=list)
    scenes: list[SceneIn] = Field(min_length=2, max_length=12)
    cta: str = Field(default="", max_length=200)
    social: SocialCopy = Field(default_factory=SocialCopy)
    claims: list[Claim] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _renumber(self) -> "ContentPlan":
        for i, s in enumerate(self.scenes, start=1):
            s.scene_number = i
        self.duration_seconds = round(sum(s.duration_seconds for s in self.scenes), 2)
        return self

    def narration_text(self) -> str:
        return " ".join(s.narration.strip() for s in self.scenes if s.narration.strip())
