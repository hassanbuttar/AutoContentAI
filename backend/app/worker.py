"""`python -m app.worker` — run workers + scheduler without the HTTP server.

Use with AUTOCONTENT_RUN_WORKERS=false on the API process to split roles (e.g. Mode B, where the
API runs elsewhere against a shared PostgreSQL database and this process stays on the GPU PC)."""
from __future__ import annotations

import signal
import threading

from app.core.db import get_engine
from app.core.logging_setup import setup_logging


def main() -> None:
    setup_logging()
    get_engine()
    from app.main import run_migrations, seed_workflows
    from app.workers.handlers import HANDLERS
    from app.workers.runner import WorkerPool, start_scheduler

    run_migrations()
    seed_workflows()
    pool = WorkerPool(HANDLERS)
    pool.start()
    sched = start_scheduler(pool)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    stop.wait()
    sched.shutdown(wait=False)
    pool.stop()


if __name__ == "__main__":
    main()
