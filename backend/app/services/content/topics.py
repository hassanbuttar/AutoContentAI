"""Deterministic topic selection (no AI): prefers fresh significant changes, then least-featured facts,
rotating content types and respecting topic cool-downs."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, ProjectChangeEvent, utcnow
from app.services.content.dedup import recent_ideas, topic_fingerprint

DEFAULT_TYPE_WEIGHTS = {"feature_announcement": 3, "problem_solution": 3, "benefits_use_cases": 3, "tips_tutorial": 2,
                        "product_demo": 2, "productivity_tip": 1, "educational": 1, "evergreen": 1}
CHANGE_TYPES = {"feature": "feature_announcement", "improvement": "feature_announcement", "release": "release_highlights",
                "docs": "tips_tutorial", "fix": "release_highlights"}


@dataclass
class TopicCandidate:
    content_type: str
    topic: str
    fact_ids: list[str] = field(default_factory=list)
    change_event_ids: list[int] = field(default_factory=list)
    angle_hint: str = ""
    reason: str = ""

    @property
    def fingerprint(self) -> str:
        return topic_fingerprint(self.content_type, self.topic, self.fact_ids)


def _facts_for_text(facts: list[dict], text: str, n: int = 3) -> list[str]:
    from app.services.content.dedup import tokens

    t = set(tokens(text))
    scored = sorted(((len(t & set(tokens(f["text"]))), f["id"]) for f in facts), reverse=True)
    return [fid for s, fid in scored[:n] if s > 0]


def select_topic(db: Session, app: Application, *, content_types: list[str] | None = None, cooldown_days: int = 7,
                 rng: random.Random | None = None, exclude_fingerprints: set[str] | None = None,
                 significance_threshold: float = 0.45) -> TopicCandidate:
    rng = rng or random.Random()
    k = app.knowledge
    facts = [f for f in (k.facts if k else []) if f.get("active", True) and f.get("status") != "planned"]
    recent = recent_ideas(db, app.id, max(cooldown_days, 30))
    cooldown_since = utcnow() - timedelta(days=cooldown_days)
    blocked = {i.fingerprint for i in recent if i.created_at >= cooldown_since} | (exclude_fingerprints or set())
    recent_types = [i.content_type for i in recent[:6]]
    strategy = app.content_strategy or {}
    weights = dict(strategy.get("content_type_weights") or DEFAULT_TYPE_WEIGHTS)
    if content_types:
        weights = {t: weights.get(t, 1) for t in content_types}

    # 1) fresh, significant, not yet promoted changes
    events = list(db.scalars(select(ProjectChangeEvent).where(
        ProjectChangeEvent.application_id == app.id, ProjectChangeEvent.promoted.is_(False),
        ProjectChangeEvent.significance >= significance_threshold,
        ProjectChangeEvent.category.in_(["feature", "improvement", "release"]),
        ProjectChangeEvent.detected_at >= utcnow() - timedelta(days=21)).order_by(ProjectChangeEvent.significance.desc()).limit(10)))
    for ev in events:
        if (ev.details or {}).get("initial"):
            continue
        ctype = CHANGE_TYPES.get(ev.category, "feature_announcement")
        if content_types and ctype not in content_types:
            ctype = content_types[0]
        title = ev.title.split(":", 1)[-1].strip() if ev.title.lower().startswith(("feat", "changelog", "release")) else ev.title
        cand = TopicCandidate(ctype, title[:200], _facts_for_text(facts, ev.title + " " + str((ev.details or {}).get("body", ""))),
                              [ev.id], angle_hint="Announce this recent improvement", reason="recent change")
        if cand.fingerprint not in blocked:
            return cand

    # 2) evergreen: least-featured facts, rotate content type
    if facts:
        def feat_key(f):
            return (f.get("times_featured", 0), f.get("last_featured_at") or "", rng.random())
        pool = sorted([f for f in facts if f.get("kind") in ("feature", "benefit", "usp", "integration")] or facts, key=feat_key)
        types = [t for t in weights if t not in recent_types[:2]] or list(weights)
        for f in pool[:12]:
            ctype = rng.choices(types, weights=[weights.get(t, 1) for t in types])[0]
            related = [f["id"]] + [x for x in _facts_for_text(facts, f["text"], 3) if x != f["id"]][:1]
            cand = TopicCandidate(ctype, f["text"][:200], related, [], reason="least-featured fact")
            if cand.fingerprint not in blocked:
                return cand
    # 3) last resort: general overview
    ctype = "evergreen" if not content_types else content_types[0]
    return TopicCandidate(ctype, f"Why {app.name}", [f["id"] for f in facts[:3]], [], reason="fallback overview")
