"""Logging with secret redaction. Never log tokens or API keys."""
from __future__ import annotations

import logging
import logging.handlers
import re

from app.core.config import get_settings

_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), "sk-ant-***"),
    (re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)(bearer|oauth)\s+[^\s'\",]+"), r"\1\2 ***"),
    (re.compile(r"(?i)((access|refresh|id)_token['\"]?\s*[:=]\s*['\"]?)[^\s'\",&]+"), r"\1***"),
    (re.compile(r"(?i)((client_secret|api_key|apikey|password|code_verifier)['\"]?\s*[:=]\s*['\"]?)[^\s'\",&]+"), r"\1***"),
    (re.compile(r"ya29\.[A-Za-z0-9_\-\.]+"), "ya29.***"),
    (re.compile(r"(?i)(gh[pousr]_[A-Za-z0-9]{20,})"), "gh*_***"),
    (re.compile(r"(?i)(glpat-[A-Za-z0-9_\-]{10,})"), "glpat-***"),
]


def redact(text: str) -> str:
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover
            return True
        red = redact(msg)
        if red != msg:
            record.msg = red
            record.args = ()
        return True


_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    s = get_settings()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    redactor = RedactingFilter()
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(redactor)
    root.addHandler(console)
    fh = logging.handlers.RotatingFileHandler(
        s.storage("logs") / "autocontent.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    fh.addFilter(redactor)
    root.addHandler(fh)
    for noisy in ("httpx", "httpcore", "apscheduler.executors.default", "websockets", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
