"""Worker runtime: lane threads that claim jobs from the DB queue, plus the APScheduler clock."""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
import traceback
from datetime import timedelta
from typing import Callable

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import ScheduledJob
from app.services.audit import job_log, notify
from app.services.settings_service import get_section
from app.workers import queue as q

log = logging.getLogger(__name__)


class JobContext:
    """Passed to handlers: progress reporting, cancellation checks, logging."""

    def __init__(self, job_id: int, worker_id: str, lease_seconds: int):
        self.job_id = job_id
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self._last_beat = 0.0

    def progress(self, fraction: float | None = None, message: str | None = None, force: bool = False) -> None:
        now = time.monotonic()
        if not force and message is None and now - self._last_beat < 5:
            return
        self._last_beat = now
        with SessionLocal() as db:
            q.heartbeat(db, self.job_id, self.worker_id, self.lease_seconds, fraction, message)

    def cancelled(self) -> bool:
        with SessionLocal() as db:
            job = db.get(ScheduledJob, self.job_id)
            return bool(job and job.cancel_requested)

    def log(self, message: str, level: str = "info", application_id: int | None = None) -> None:
        with SessionLocal() as db:
            job_log(db, message, level, job_id=self.job_id, application_id=application_id)
            db.commit()


class JobCancelled(Exception):
    pass


Handler = Callable[[ScheduledJob, JobContext], dict | None]


class WorkerPool:
    def __init__(self, handlers: dict[str, Handler]):
        self.handlers = handlers
        self.settings = get_settings()
        self.hostname = socket.gethostname()
        self.prefix = f"{self.hostname}:{os.getpid()}"
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.active: dict[str, int | None] = {}

    def start(self) -> None:
        with SessionLocal() as db:
            recovered = q.recover_dead_workers_on_startup(db, self.hostname)
            for job in recovered:
                job_log(db, f"Recovered interrupted job {job.id} ({job.job_type}) -> {job.status}", "warning", job_id=job.id)
            db.commit()
        lanes = [("default", self.settings.default_lane_concurrency), ("gpu", self.settings.gpu_lane_concurrency)]
        for lane, n in lanes:
            for i in range(max(0, n)):
                wid = f"{self.prefix}:{lane}-{i}"
                t = threading.Thread(target=self._loop, args=(lane, wid), name=f"worker-{lane}-{i}", daemon=True)
                t.start()
                self._threads.append(t)
        threading.Thread(target=self._lease_reaper, name="lease-reaper", daemon=True).start()
        log.info("Worker pool started: %s", ", ".join(f"{lane}x{n}" for lane, n in lanes))

    def stop(self, timeout: float = 20) -> None:
        """Safe shutdown: stop claiming, let running jobs finish (up to timeout)."""
        self._stop.set()
        deadline = time.monotonic() + timeout
        for t in self._threads:
            t.join(max(0.1, deadline - time.monotonic()))
        log.info("Worker pool stopped")

    def _lease_reaper(self) -> None:
        while not self._stop.wait(60):
            try:
                with SessionLocal() as db:
                    for job in q.recover_expired_leases(db):
                        job_log(db, f"Lease expired; job {job.id} -> {job.status}", "warning", job_id=job.id)
                    db.commit()
            except Exception:
                log.exception("lease reaper failed")

    def _loop(self, lane: str, worker_id: str) -> None:
        while not self._stop.is_set():
            try:
                with SessionLocal() as db:
                    job = q.claim_next(db, lane, worker_id, self.settings.lease_seconds)
                if job is None:
                    self._stop.wait(self.settings.poll_seconds)
                    continue
                self.active[worker_id] = job.id
                self.run_job(job.id, worker_id)
            except Exception:
                log.exception("worker loop error")
                self._stop.wait(5)
            finally:
                self.active[worker_id] = None

    def run_job(self, job_id: int, worker_id: str) -> None:
        ctx = JobContext(job_id, worker_id, self.settings.lease_seconds)
        with SessionLocal() as db:
            job = db.get(ScheduledJob, job_id)
            sched = get_section(db, "scheduler")
            handler = self.handlers.get(job.job_type)
            if handler is None:
                q.mark_failed(db, job, f"No handler for {job.job_type}", retryable=False)
                return
            # Heartbeat thread keeps the lease alive during long blocking calls (FFmpeg, ComfyUI).
            stop_beat = threading.Event()

            def beat():
                while not stop_beat.wait(max(5, self.settings.lease_seconds // 4)):
                    try:
                        with SessionLocal() as bdb:
                            q.heartbeat(bdb, job_id, worker_id, self.settings.lease_seconds)
                    except Exception:
                        log.debug("heartbeat failed", exc_info=True)

            bt = threading.Thread(target=beat, daemon=True, name=f"beat-{job_id}")
            bt.start()
            try:
                result = handler(job, ctx)
                db.refresh(job)
                q.mark_succeeded(db, job, result or {})
            except q.JobBlocked as exc:
                db.rollback()
                db.refresh(job)
                q.mark_blocked(db, job, exc.reason, exc.retry_after)
                job_log(db, f"Job blocked: {exc.reason}", "warning", job_id=job.id, application_id=job.application_id)
                db.commit()
            except JobCancelled:
                db.rollback()
                db.refresh(job)
                job.status = "cancelled"
                job.locked_by = None
                db.commit()
            except Exception as exc:
                db.rollback()
                db.refresh(job)
                retryable = not isinstance(exc, (q.FatalError, ValueError, KeyError))
                msg = f"{type(exc).__name__}: {exc}"
                new_status = q.mark_failed(db, job, msg, traceback.format_exc(), retryable=retryable,
                                           base=int(sched["retry_base_seconds"]), cap=int(sched["retry_max_seconds"]))
                job_log(db, f"Job {job.job_type} failed ({new_status}): {msg}", "error", job_id=job.id,
                        application_id=job.application_id)
                if new_status == "failed":
                    notify(db, f"Job failed: {job.job_type} #{job.id}", msg, "error", link=f"/logs?job={job.id}", event="job_failed")
                    _on_job_failed(db, job)
                db.commit()
            finally:
                stop_beat.set()


def _on_job_failed(db, job: ScheduledJob) -> None:
    """Propagate permanent failure to the publication waiting on this job (never silently dropped)."""
    from app.models import PublicationRecord

    pub_id = (job.payload or {}).get("publication_id")
    pub_ids = (job.payload or {}).get("publication_ids") or ([pub_id] if pub_id else [])
    for pid in pub_ids:
        pub = db.get(PublicationRecord, pid)
        if pub and pub.status in ("planned", "preparing", "approved", "publishing"):
            if job.job_type == "publish" and pub.status == "publishing":
                # outcome unknown after repeated errors mid-publish: never auto-retry, ask a human
                pub.status = "verify_required"
                pub.last_error = f"Publishing was interrupted ({job.last_error}). Check the platform, then mark published or retry."[:2000]
                continue
            pub.status = "failed"
            pub.last_error = f"{job.job_type} failed: {job.last_error}"[:2000]


def start_scheduler(pool: WorkerPool | None = None):
    """APScheduler is only a clock; all state is in the DB. Jobstore is in-memory on purpose:
    schedules are rebuilt from DB settings at startup, so nothing can drift or duplicate."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    from app.workers import clock

    s = get_settings()
    with SessionLocal() as db:
        gen = get_section(db, "general")
    tz = gen.get("timezone") or "UTC"
    hh, mm = (gen.get("daily_analysis_time") or "05:30").split(":")
    sched = BackgroundScheduler(timezone=tz, job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600})
    sched.add_job(clock.scheduler_tick, "interval", seconds=s.tick_seconds, id="tick")
    sched.add_job(clock.daily_analysis, CronTrigger(hour=int(hh), minute=int(mm), timezone=tz), id="daily_analysis")
    sched.add_job(clock.refresh_tokens, "interval", minutes=30, id="refresh_tokens")
    sched.add_job(clock.collect_metrics, "interval", hours=6, id="metrics")
    sched.add_job(clock.daily_backup, CronTrigger(hour=3, minute=15, timezone=tz), id="backup")
    sched.start()
    # first tick shortly after startup (handles missed runs while offline)
    sched.add_job(clock.scheduler_tick, "date", run_date=None, id="tick-startup")
    clock.catch_up_daily_analysis()
    return sched


def wait_for_job(job_id: int, timeout: float = 600, poll: float = 0.5) -> ScheduledJob:
    """Test/CLI helper."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            job = db.get(ScheduledJob, job_id)
            if job and job.status in q.TERMINAL | {"blocked"}:
                return job
        time.sleep(poll)
    raise TimeoutError(f"job {job_id} did not finish in {timeout}s")


_ = timedelta  # re-exported for handlers
