from __future__ import annotations

import logging
import threading
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models import AuditLog, LogEntry, Notification

log = logging.getLogger(__name__)


def audit(db: Session, action: str, user_id: int | None = None, entity_type: str | None = None,
          entity_id: Any = None, details: dict | None = None, ip: str | None = None) -> None:
    db.add(AuditLog(user_id=user_id, action=action, entity_type=entity_type,
                    entity_id=str(entity_id) if entity_id is not None else None, details=details or {}, ip=ip))


def job_log(db: Session, message: str, level: str = "info", job_id: int | None = None, application_id: int | None = None) -> None:
    from app.core.logging_setup import redact

    db.add(LogEntry(level=level, message=redact(message)[:4000], job_id=job_id, application_id=application_id))
    getattr(log, "warning" if level == "warning" else ("error" if level == "error" else "info"))(
        "[job %s] %s", job_id, redact(message)[:500])


def notify(db: Session, title: str, message: str = "", level: str = "info", link: str | None = None, event: str | None = None) -> None:
    db.add(Notification(title=title[:200], message=message[:4000], level=level, link=link))
    try:
        from app.services.settings_service import get_section

        cfg = get_section(db, "notifications")
        url = cfg.get("webhook_url")
        if url and (event is None or event in cfg.get("notify_on", [])):
            threading.Thread(target=_post_webhook, args=(url, title, message, level), daemon=True).start()
    except Exception:  # never let notifications break the caller
        log.exception("notification webhook dispatch failed")


def _post_webhook(url: str, title: str, message: str, level: str) -> None:
    try:
        text = f"[AutoContent AI] {level.upper()}: {title}\n{message}"[:1900]
        httpx.post(url, json={"text": text, "content": text}, timeout=10)
    except Exception as exc:  # pragma: no cover - network
        log.warning("webhook failed: %s", type(exc).__name__)
