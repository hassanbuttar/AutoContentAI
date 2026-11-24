"""Delete files under storage/ without failing the caller when Windows has them locked.

On Windows a file that another handle has open (the dashboard's <video> player streaming it, an antivirus scan,
a running upload) cannot be deleted (WinError 32). Callers remove database rows first and then call
`remove_files`; anything still locked is handed to a `cleanup_files` job that retries with backoff."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.core.paths import PathNotAllowed, storage_path

log = logging.getLogger("autocontent")


def try_remove(rels: list[str]) -> list[str]:
    """Delete each storage-relative path; return the ones still locked. Paths outside storage are ignored."""
    left: list[str] = []
    for rel in rels:
        if not rel:
            continue
        try:
            storage_path(rel).unlink(missing_ok=True)
        except PathNotAllowed:
            log.warning("Refusing to delete a path outside storage: %s", rel)
        except OSError:
            left.append(rel)
    return left


def remove_files(db: Session, rels: list[str | None]) -> list[str]:
    """Delete now if possible; schedule a retrying cleanup job for locked files. Returns the deferred paths."""
    pending = try_remove([r for r in rels if r])
    if pending:
        from app.workers.queue import enqueue

        enqueue(db, "cleanup_files", {"paths": pending}, idempotency_key=f"cleanup:{uuid.uuid4().hex[:12]}", priority=90)
        db.commit()
        log.info("Deferred deletion of %d locked file(s): %s", len(pending), ", ".join(pending))
    return pending
