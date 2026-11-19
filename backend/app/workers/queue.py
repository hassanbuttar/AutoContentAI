"""Database-backed job queue with leases, idempotency keys and exponential backoff."""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import JobAttempt, ScheduledJob, utcnow

TERMINAL = {"succeeded", "failed", "cancelled"}
ACTIVE = {"queued", "running", "retry_wait", "blocked"}

# job_type -> (lane, default max_attempts)
JOB_TYPES: dict[str, tuple[str, int]] = {
    "analyze_application": ("default", 3),
    "prepare_content": ("default", 3),
    "plan_content": ("default", 2),
    "render_video": ("gpu", 2),
    "publish": ("default", 4),
    "verify_publication": ("default", 3),
    "fetch_metrics": ("default", 2),
    "refresh_tokens": ("default", 2),
    "backup": ("default", 1),
}


class JobBlocked(Exception):
    """Raise from a handler to park the job (e.g. budget exceeded) without consuming attempts."""

    def __init__(self, reason: str, retry_after: timedelta | None = None):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after or timedelta(minutes=30)


class RetryableError(Exception):
    """Transient failure; retried with backoff while attempts remain."""


class FatalError(Exception):
    """Permanent failure; never retried."""


def enqueue(db: Session, job_type: str, payload: dict[str, Any] | None = None, *, idempotency_key: str | None = None,
            application_id: int | None = None, run_at: datetime | None = None, priority: int = 100,
            max_attempts: int | None = None, parent_job_id: int | None = None) -> tuple[ScheduledJob, bool]:
    """Insert a job. Returns (job, created). If the idempotency key exists, returns the existing job."""
    if job_type not in JOB_TYPES:
        raise ValueError(f"Unknown job type {job_type}")
    lane, default_attempts = JOB_TYPES[job_type]
    if idempotency_key:
        existing = db.scalar(select(ScheduledJob).where(ScheduledJob.idempotency_key == idempotency_key))
        if existing:
            return existing, False
    job = ScheduledJob(job_type=job_type, lane=lane, payload=payload or {}, idempotency_key=idempotency_key,
                       application_id=application_id, run_at=run_at or utcnow(), priority=priority,
                       max_attempts=max_attempts or default_attempts, parent_job_id=parent_job_id)
    sp = db.begin_nested()
    try:
        db.add(job)
        db.flush()
        sp.commit()
    except IntegrityError:
        sp.rollback()
        existing = db.scalar(select(ScheduledJob).where(ScheduledJob.idempotency_key == idempotency_key))
        if existing is None:
            raise
        return existing, False
    return job, True


def claim_next(db: Session, lane: str, worker_id: str, lease_seconds: int) -> ScheduledJob | None:
    """Atomically claim the next runnable job in `lane` (compare-and-set on status)."""
    now = utcnow()
    # Promote retry_wait / blocked jobs whose time has come.
    db.execute(update(ScheduledJob)
               .where(ScheduledJob.status.in_(["retry_wait", "blocked"]), ScheduledJob.run_at <= now)
               .values(status="queued"))
    db.commit()
    for _ in range(5):
        cand = db.scalar(
            select(ScheduledJob)
            .where(ScheduledJob.lane == lane, ScheduledJob.status == "queued", ScheduledJob.run_at <= now)
            .order_by(ScheduledJob.priority, ScheduledJob.run_at, ScheduledJob.id).limit(1))
        if cand is None:
            return None
        res = db.execute(
            update(ScheduledJob)
            .where(ScheduledJob.id == cand.id, ScheduledJob.status == "queued")
            .values(status="running", locked_by=worker_id, lease_expires_at=now + timedelta(seconds=lease_seconds),
                    attempts=ScheduledJob.attempts + 1, started_at=now, progress=0.0, progress_message="Starting"))
        db.commit()
        if res.rowcount == 1:
            job = db.get(ScheduledJob, cand.id)
            db.refresh(job)
            db.add(JobAttempt(job_id=job.id, attempt_no=job.attempts, worker_id=worker_id, status="running"))
            db.commit()
            return job
    return None


def heartbeat(db: Session, job_id: int, worker_id: str, lease_seconds: int, progress: float | None = None,
              message: str | None = None) -> bool:
    values: dict[str, Any] = {"lease_expires_at": utcnow() + timedelta(seconds=lease_seconds)}
    if progress is not None:
        values["progress"] = max(0.0, min(1.0, progress))
    if message is not None:
        values["progress_message"] = message[:500]
    res = db.execute(update(ScheduledJob)
                     .where(ScheduledJob.id == job_id, ScheduledJob.locked_by == worker_id, ScheduledJob.status == "running")
                     .values(**values))
    db.commit()
    return res.rowcount == 1


def backoff_seconds(attempt: int, base: int = 30, cap: int = 3600) -> float:
    delay = min(cap, base * (2 ** max(0, attempt - 1)))
    return delay * random.uniform(0.85, 1.15)


def _finish_attempt(db: Session, job: ScheduledJob, status: str, error: str | None = None, tb: str | None = None) -> None:
    att = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt_no == job.attempts)
                    .order_by(JobAttempt.id.desc()).limit(1))
    if att:
        att.status = status
        att.error = error
        att.traceback = tb
        att.finished_at = utcnow()
        att.duration_s = (att.finished_at - att.started_at).total_seconds()


def mark_succeeded(db: Session, job: ScheduledJob, result: dict | None = None) -> None:
    job.status = "succeeded"
    job.result = result or {}
    job.progress = 1.0
    job.progress_message = "Done"
    job.finished_at = utcnow()
    job.locked_by = None
    job.lease_expires_at = None
    job.last_error = None
    job.blocked_reason = None
    _finish_attempt(db, job, "succeeded")
    db.commit()


def mark_failed(db: Session, job: ScheduledJob, error: str, tb: str | None = None, *, retryable: bool = True,
                base: int = 30, cap: int = 3600) -> str:
    """Returns the new status (retry_wait or failed)."""
    job.last_error = error[:4000]
    job.locked_by = None
    job.lease_expires_at = None
    _finish_attempt(db, job, "failed", error[:4000], tb)
    if retryable and job.attempts < job.max_attempts:
        job.status = "retry_wait"
        job.run_at = utcnow() + timedelta(seconds=backoff_seconds(job.attempts, base, cap))
        job.progress_message = f"Retry scheduled (attempt {job.attempts}/{job.max_attempts})"
    else:
        job.status = "failed"
        job.finished_at = utcnow()
        job.progress_message = "Failed"
    db.commit()
    return job.status


def mark_blocked(db: Session, job: ScheduledJob, reason: str, retry_after: timedelta) -> None:
    job.status = "blocked"
    job.blocked_reason = reason[:300]
    job.run_at = utcnow() + retry_after
    job.attempts = max(0, job.attempts - 1)  # blocking does not consume an attempt
    job.locked_by = None
    job.lease_expires_at = None
    job.progress_message = f"Blocked: {reason}"[:500]
    _finish_attempt(db, job, "blocked", reason)
    db.commit()


def cancel(db: Session, job: ScheduledJob) -> bool:
    if job.status in TERMINAL:
        return False
    if job.status == "running":
        job.cancel_requested = True
    else:
        job.status = "cancelled"
        job.finished_at = utcnow()
    db.commit()
    return True


def retry_now(db: Session, job: ScheduledJob, include_succeeded: bool = False) -> None:
    """Requeue a job to run now. `include_succeeded` re-runs a finished job; only for handlers that are
    idempotent on their own (publish: guarded by the publication's approved->publishing compare-and-set)."""
    allowed = ("failed", "cancelled", "retry_wait", "blocked") + (("succeeded",) if include_succeeded else ())
    if job.status in allowed:
        if job.attempts >= job.max_attempts:
            job.max_attempts = job.attempts + 1
        job.status = "queued"
        job.run_at = utcnow()
        job.finished_at = None
        job.cancel_requested = False
        db.commit()


def recover_expired_leases(db: Session) -> list[ScheduledJob]:
    """Called on startup and periodically: jobs whose worker died. Returns recovered jobs."""
    now = utcnow()
    stale = list(db.scalars(select(ScheduledJob).where(
        ScheduledJob.status == "running",
        or_(ScheduledJob.lease_expires_at.is_(None), ScheduledJob.lease_expires_at < now))))
    for job in stale:
        _finish_attempt(db, job, "interrupted", "worker lease expired (process stopped or crashed)")
        job.locked_by = None
        job.lease_expires_at = None
        if job.attempts < job.max_attempts:
            job.status = "queued"
            job.run_at = now
            job.progress_message = "Recovered after interruption"
        else:
            job.status = "failed"
            job.finished_at = now
            job.last_error = "Interrupted and out of attempts"
    db.commit()
    return stale


def pid_alive(pid: int) -> bool:
    import os
    import sys

    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return bool(ok) and code.value == STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def recover_dead_workers_on_startup(db: Session, hostname: str) -> list[ScheduledJob]:
    """Expire leases held by worker processes on this host that no longer exist (crash / reboot).

    Worker ids look like "<host>:<pid>:<lane>-<n>". Leases held by live processes are left alone,
    so a separately running worker process is never disturbed.
    """
    running = list(db.scalars(select(ScheduledJob).where(ScheduledJob.status == "running",
                                                         ScheduledJob.locked_by.is_not(None))))
    expired = False
    for job in running:
        parts = (job.locked_by or "").split(":")
        if len(parts) >= 2 and parts[0] == hostname and parts[1].isdigit() and not pid_alive(int(parts[1])):
            job.lease_expires_at = utcnow() - timedelta(seconds=1)
            expired = True
    if expired:
        db.commit()
    return recover_expired_leases(db)


def claim_specific(db: Session, job_id: int, worker_id: str, lease_seconds: int) -> ScheduledJob | None:
    """Claim one particular queued job (used by tests and synchronous CLI runs)."""
    now = utcnow()
    res = db.execute(update(ScheduledJob).where(ScheduledJob.id == job_id, ScheduledJob.status == "queued")
                     .values(status="running", locked_by=worker_id, lease_expires_at=now + timedelta(seconds=lease_seconds),
                             attempts=ScheduledJob.attempts + 1, started_at=now, progress=0.0, progress_message="Starting"))
    db.commit()
    if res.rowcount != 1:
        return None
    job = db.get(ScheduledJob, job_id)
    db.refresh(job)
    db.add(JobAttempt(job_id=job.id, attempt_no=job.attempts, worker_id=worker_id, status="running"))
    db.commit()
    return job
