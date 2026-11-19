"""Runtime settings stored in `system_settings` (one JSON document per section)."""
from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session

from app.models import SystemSetting

DEFAULTS: dict[str, dict[str, Any]] = {
    "general": {
        "timezone": "UTC",
        "daily_analysis_time": "05:30",  # local time of `timezone`
        "setup_completed": False,
        "instance_name": "AutoContent AI",
    },
    "ai": {
        "enabled": True,
        "model": "claude-opus-5-5",
        "effort": "medium",  # low|medium|high
        "cost_saving_mode": False,  # prefer local planner; Claude only for profile updates on significant changes
        "use_refusal_fallbacks": True,
        "on_budget_exceeded": "pause",  # pause (block AI jobs until the window resets) | local_fallback
        "max_output_tokens": 8000,
        "significance_threshold": 0.45,
        "profile_char_budget": 12000,
        "digest_char_budget": 6000,
        "budgets": {
            "daily_calls": 25,
            "monthly_tokens": 3_000_000,
            "monthly_cost_usd": 25.0,
            "max_tokens_per_job": 60_000,
            "max_retries": 1,
        },
        # USD per 1M tokens: input, output, cache_read; cache write = 1.25 × input
        "pricing": {
            "claude-opus-5-5": [4.0, 20.0, 0.20],
            "claude-sonnet-5-5": [2.0, 10.0, 0.10],
            "claude-haiku-5-5": [0.10, 0.50, 0.01],
            "claude-fable-5-2": [8.0, 40.0, 0.20],
        },
    },
    "comfyui": {
        "enabled": True,
        "url": "http://127.0.0.1:8188",
        "timeout_seconds": 900,
        "use_for_scenes": True,
        "use_img2vid": False,  # animate stills with an img2vid workflow (slow; GPU heavy)
        "max_comfy_scenes_per_video": 2,  # Qwen-Image takes minutes per image on a laptop GPU
    },
    "video": {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "target_duration": 30,
        "min_duration": 15,
        "max_duration": 60,
        "scenes": 5,
        "caption_style": {"enabled": True, "font_size": 66, "color": "#FFFFFF", "stroke": "#000000", "highlight": "#FACC15", "position": "lower"},
        "voice": {"enabled": True, "engine": "sapi", "voice_name": "", "rate": 178, "piper_path": "", "piper_model": ""},
        "logo_position": "top-right",  # top-left|top-right|bottom-left|bottom-right|none
        "transition": "fade",  # none|fade
        "music_volume": 0.12,
        "end_card": True,
        "ken_burns": True,
        "crf": 20,
    },
    "security": {
        "allowed_project_roots": [],
    },
    "scheduler": {
        "horizon_days": 3,
        "max_jobs_per_day": 40,
        "daily_generation_limit": 12,
        "retry_base_seconds": 30,
        "retry_max_seconds": 3600,
        "auto_analyze": True,
        "reuse_window_hours": 48,
        "verify_after_crash": True,
    },
    "dedup": {"similarity_threshold": 0.55, "lookback_days": 30, "topic_cooldown_days": 7},
    "notifications": {"webhook_url": "", "notify_on": ["job_failed", "publish_failed", "budget_exceeded", "reauth_required"]},
    "oauth": {
        "redirect_base_url": "",  # default http://127.0.0.1:<port>
        "youtube": {"client_id": ""},
        "instagram": {"client_id": "", "variant": "facebook_login", "graph_version": "v25.0"},
        "tiktok": {"client_key": ""},
    },
}

SECTIONS = tuple(DEFAULTS.keys())


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_section(db: Session, section: str) -> dict[str, Any]:
    if section not in DEFAULTS:
        raise KeyError(section)
    row = db.get(SystemSetting, section)
    return _deep_merge(DEFAULTS[section], row.value if row and isinstance(row.value, dict) else {})


def update_section(db: Session, section: str, patch: dict[str, Any]) -> dict[str, Any]:
    if section not in DEFAULTS:
        raise KeyError(section)
    current = get_section(db, section)
    merged = _deep_merge(current, patch)
    row = db.get(SystemSetting, section)
    if row:
        row.value = merged
    else:
        db.add(SystemSetting(key=section, value=merged))
    db.flush()
    return merged


def all_settings(db: Session) -> dict[str, dict[str, Any]]:
    return {s: get_section(db, s) for s in SECTIONS}


def effective_video_settings(db: Session, app_overrides: dict | None) -> dict[str, Any]:
    return _deep_merge(get_section(db, "video"), app_overrides or {})
