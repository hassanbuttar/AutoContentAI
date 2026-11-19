"""Functions fired by the APScheduler clock. Each only enqueues idempotent DB jobs."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import Application, PublicationRecord, SocialAccount, utcnow
from app.services.scheduling.engine import tick
from app.services.settings_service import get_section
from app.workers.queue import enqueue

log = logging.getLogger(__name__)
_last_tick: dict = {}


def scheduler_tick() -> None:
    try:
        with SessionLocal() as db:
            res = tick(db)
            _last_tick.update({"at": utcnow().isoformat(), "result": res})
            if any(res.get(k) for k in ("prepare_jobs", "publish_jobs", "missed")) or res["materialized"]["created"]:
                log.info("tick: %s", res)
    except Exception:
        log.exception("scheduler tick failed")
        _last_tick.update({"at": utcnow().isoformat(), "error": True})


def last_tick() -> dict:
    return dict(_last_tick)


def _local_today(db) -> str:
    tz = ZoneInfo(get_section(db, "general").get("timezone") or "UTC")
    return datetime.now(timezone.utc).astimezone(tz).date().isoformat()


def daily_analysis() -> None:
    with SessionLocal() as db:
        if not get_section(db, "scheduler").get("auto_analyze", True):
            return
        day = _local_today(db)
        for app in db.scalars(select(Application).where(Application.active.is_(True), Application.paused.is_(False))):
            enqueue(db, "analyze_application", {"application_id": app.id, "trigger": "daily"},
                    idempotency_key=f"analyze:{app.id}:{day}", application_id=app.id, priority=80)
        db.commit()


def catch_up_daily_analysis() -> None:
    """If the PC was off at the daily analysis time, run today's analysis once at startup."""
    with SessionLocal() as db:
        gen = get_section(db, "general")
        tz = ZoneInfo(gen.get("timezone") or "UTC")
        hh, mm = (gen.get("daily_analysis_time") or "05:30").split(":")
        now_local = datetime.now(timezone.utc).astimezone(tz)
        if (now_local.hour, now_local.minute) >= (int(hh), int(mm)):
            daily_analysis()


def refresh_tokens() -> None:
    with SessionLocal() as db:
        bucket = utcnow().strftime("%Y%m%d%H") + ("a" if utcnow().minute < 30 else "b")
        enqueue(db, "refresh_tokens", {}, idempotency_key=f"refresh:{bucket}", priority=20)
        db.commit()


def collect_metrics() -> None:
    with SessionLocal() as db:
        bucket = utcnow().strftime("%Y%m%d") + str(utcnow().hour // 6)
        has = db.scalar(select(PublicationRecord.id).join(SocialAccount).where(
            PublicationRecord.status == "published", SocialAccount.platform != "export").limit(1))
        if has:
            enqueue(db, "fetch_metrics", {}, idempotency_key=f"metrics:{bucket}", priority=150)
            db.commit()


def daily_backup() -> None:
    with SessionLocal() as db:
        enqueue(db, "backup", {}, idempotency_key=f"backup:{utcnow().date().isoformat()}", priority=200)
        db.commit()
