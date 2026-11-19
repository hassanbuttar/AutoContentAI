"""Local repetition checks (no AI): topic fingerprints and word-shingle Jaccard similarity."""
from __future__ import annotations

import hashlib
import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ContentIdea, VideoScript, utcnow

STOP = set("""a an the and or but if then so to of in on for with at by from is are was were be been it its this that these
those you your we our they their i me my as into about over more most can will just than very really how what why when
new now get got make makes made use uses using app""".split())


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in STOP and len(w) > 1]


def shingles(text: str, k: int = 3) -> set[str]:
    t = tokens(text)
    if len(t) < k:
        return {" ".join(t)} if t else set()
    return {" ".join(t[i:i + k]) for i in range(len(t) - k + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def topic_fingerprint(content_type: str, topic: str, fact_ids: list[str]) -> str:
    key_words = sorted(set(tokens(topic)))[:6]
    raw = f"{content_type}|{','.join(sorted(fact_ids))}|{' '.join(key_words)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:24]


def recent_ideas(db: Session, application_id: int, days: int) -> list[ContentIdea]:
    since = utcnow() - timedelta(days=days)
    return list(db.scalars(select(ContentIdea).where(ContentIdea.application_id == application_id,
                                                     ContentIdea.created_at >= since, ContentIdea.status != "rejected")
                           .order_by(ContentIdea.created_at.desc()).limit(200)))


def topic_recently_used(db: Session, application_id: int, fingerprint: str, cooldown_days: int) -> bool:
    return any(i.fingerprint == fingerprint for i in recent_ideas(db, application_id, cooldown_days))


def max_script_similarity(db: Session, application_id: int, script_text: str, days: int,
                          exclude_script_id: int | None = None) -> tuple[float, int | None]:
    since = utcnow() - timedelta(days=days)
    rows = db.execute(select(VideoScript.id, VideoScript.script_text).where(
        VideoScript.application_id == application_id, VideoScript.created_at >= since).limit(300)).all()
    sh = shingles(script_text)
    best, best_id = 0.0, None
    for sid, txt in rows:
        if sid == exclude_script_id:
            continue
        s = jaccard(sh, shingles(txt))
        if s > best:
            best, best_id = s, sid
    return round(best, 3), best_id
