"""Scheduler tick: materialize slots, enqueue preparation and publishing, apply missed-run policy.

Everything here is idempotent; running tick() twice (or after a reboot) never duplicates work:
publication slots are unique per (rule, scheduled_for) and every job has an idempotency key.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Application, PublicationRecord, PublishingRule, ScheduledJob, SocialAccount, utcnow
from app.services.audit import job_log
from app.services.scheduling.slots import RuleSpec, compute_slots
from app.services.settings_service import get_section
from app.workers.queue import enqueue

log = logging.getLogger(__name__)

OPEN_STATUSES = ("planned", "preparing", "awaiting_approval", "approved")
SKIP_TOLERANCE = timedelta(minutes=10)


def pub_key(rule_id: int, slot: datetime) -> str:
    return f"rule:{rule_id}:{slot.strftime('%Y%m%dT%H%M')}"


def rule_is_active(rule: PublishingRule, account: SocialAccount, app: Application) -> bool:
    return bool(rule.enabled and not account.paused and app.active and not app.paused)


def materialize(db: Session, now: datetime, horizon_days: int) -> dict:
    created = cancelled = 0
    end = now + timedelta(days=horizon_days)
    rows = db.execute(select(PublishingRule, SocialAccount, Application)
                      .join(SocialAccount, PublishingRule.social_account_id == SocialAccount.id)
                      .join(Application, PublishingRule.application_id == Application.id)).all()
    for rule, account, app in rows:
        existing = {p.scheduled_for: p for p in db.scalars(select(PublicationRecord).where(
            PublicationRecord.publishing_rule_id == rule.id, PublicationRecord.scheduled_for >= now - timedelta(days=1)))}
        if not rule_is_active(rule, account, app):
            wanted: set[datetime] = set()
        else:
            # Look back so slots missed while the PC was off are still handled by the missed policy.
            lookback = timedelta(minutes=rule.grace_minutes) if rule.missed_policy == "publish_late" else SKIP_TOLERANCE
            try:
                wanted = set(compute_slots(RuleSpec.from_model(rule), now - lookback, end))
            except Exception as exc:  # bad timezone etc.
                log.warning("rule %s: cannot compute slots: %s", rule.id, exc)
                continue
        for slot in wanted & set(existing):
            pub = existing[slot]
            if pub.status == "cancelled" and pub.last_error == "Slot removed by schedule change" and slot > now:
                pub.status, pub.last_error = "planned", None
        for slot in sorted(wanted - set(existing)):
            sp = db.begin_nested()
            try:
                db.add(PublicationRecord(application_id=app.id, social_account_id=account.id, publishing_rule_id=rule.id,
                                         platform=account.platform, scheduled_for=slot, status="planned",
                                         idempotency_key=pub_key(rule.id, slot)))
                db.flush()
                sp.commit()
                created += 1
            except IntegrityError:
                sp.rollback()
        # Future slots that no longer match the rule (rule edited / paused) and have no content yet.
        for slot, pub in existing.items():
            if slot > now and slot not in wanted and pub.status == "planned":
                pub.status = "cancelled"
                pub.last_error = "Slot removed by schedule change"
                cancelled += 1
    db.commit()
    return {"created": created, "cancelled": cancelled}


def _jobs_created_today(db: Session, job_type: str, now: datetime) -> int:
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(select(func.count()).select_from(ScheduledJob).where(
        ScheduledJob.job_type == job_type, ScheduledJob.created_at >= day_start)) or 0


def enqueue_preparation(db: Session, now: datetime, daily_limit: int) -> int:
    n = 0
    budget_left = max(0, daily_limit - _jobs_created_today(db, "prepare_content", now))
    pubs = list(db.scalars(select(PublicationRecord).where(PublicationRecord.status == "planned")
                           .order_by(PublicationRecord.scheduled_for)))
    for pub in pubs:
        rule = db.get(PublishingRule, pub.publishing_rule_id) if pub.publishing_rule_id else None
        lead = timedelta(hours=rule.generation_lead_hours if rule else 12)
        if pub.scheduled_for - lead > now:
            continue
        if budget_left <= 0:
            log.info("daily generation limit reached; %s will be prepared later", pub.id)
            break
        job, created = enqueue(db, "prepare_content", {"publication_id": pub.id}, idempotency_key=f"prepare:{pub.id}",
                               application_id=pub.application_id, priority=50)
        pub.status = "preparing"
        if created:
            n += 1
            budget_left -= 1
    db.commit()
    return n


def apply_missed_and_publish(db: Session, now: datetime) -> dict:
    published = missed = 0
    pubs = list(db.scalars(select(PublicationRecord).where(
        PublicationRecord.status.in_(OPEN_STATUSES), PublicationRecord.scheduled_for <= now)))
    for pub in pubs:
        rule = db.get(PublishingRule, pub.publishing_rule_id) if pub.publishing_rule_id else None
        policy = rule.missed_policy if rule else "publish_late"
        grace = timedelta(minutes=rule.grace_minutes if rule else 240)
        lateness = now - pub.scheduled_for
        limit = grace if policy == "publish_late" else SKIP_TOLERANCE
        if pub.manual:
            limit = timedelta(days=7)  # manual "publish now / at time" items are never auto-skipped quickly
        if lateness > limit:
            pub.status = "missed"
            pub.last_error = (f"Missed: {int(lateness.total_seconds() // 60)} min late "
                              f"(policy {policy}, limit {int(limit.total_seconds() // 60)} min)")
            if policy == "next_slot":
                pub.last_error += "; video released for the next slot"
            job_log(db, f"Publication {pub.id} missed its slot: {pub.last_error}", "warning", application_id=pub.application_id)
            missed += 1
            continue
        if pub.status == "approved":
            _, created = enqueue(db, "publish", {"publication_id": pub.id}, idempotency_key=f"publish:{pub.id}",
                                 application_id=pub.application_id, priority=10)
            if created:
                published += 1
    db.commit()
    return {"publish_jobs": published, "missed": missed}


def tick(db: Session, now: datetime | None = None) -> dict:
    now = now or utcnow()
    cfg = get_section(db, "scheduler")
    out = {"materialized": materialize(db, now, int(cfg["horizon_days"]))}
    out["prepare_jobs"] = enqueue_preparation(db, now, int(cfg["daily_generation_limit"]))
    out.update(apply_missed_and_publish(db, now))
    return out
