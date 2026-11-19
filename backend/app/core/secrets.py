"""Secret storage.

A random Fernet master key lives in the OS keychain (Windows Credential Manager,
macOS Keychain, Secret Service on Linux) via `keyring`. Secret values are
encrypted with that key and stored in the `secret_values` table, which avoids
keychain size limits for large OAuth token sets.

Headless fallback: AUTOCONTENT_MASTER_KEY env var, then a key file under
storage/ (created with restrictive permissions, with a logged warning).

Secret values are never returned by the API; only `configured` + a hint.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import SecretValue

log = logging.getLogger(__name__)
_lock = threading.Lock()
_fernet: Fernet | None = None
_key_source = "unknown"


def _load_master_key() -> bytes:
    global _key_source
    s = get_settings()
    if s.master_key:
        _key_source = "env"
        return s.master_key.encode()
    if s.env != "test":
        try:
            import keyring

            existing = keyring.get_password(s.keyring_service, "master-key")
            if existing:
                _key_source = "os-keychain"
                return existing.encode()
            new = Fernet.generate_key().decode()
            keyring.set_password(s.keyring_service, "master-key", new)
            if keyring.get_password(s.keyring_service, "master-key") == new:
                _key_source = "os-keychain"
                return new.encode()
        except Exception as exc:  # keyring backend missing (headless Linux)
            log.warning("OS keychain unavailable (%s); falling back to key file", type(exc).__name__)
    key_file = s.storage_dir / ".master.key"
    if key_file.exists():
        _key_source = "key-file"
        return key_file.read_bytes().strip()
    key = Fernet.generate_key()
    key_file.write_bytes(key)
    try:
        os.chmod(key_file, 0o600)
    except OSError:  # pragma: no cover - Windows ACLs
        pass
    _key_source = "key-file"
    if s.env != "test":
        log.warning("Master key stored in %s; protect this file or set AUTOCONTENT_MASTER_KEY", key_file)
    return key


def fernet() -> Fernet:
    global _fernet
    with _lock:
        if _fernet is None:
            _fernet = Fernet(_load_master_key())
        return _fernet


def key_source() -> str:
    fernet()
    return _key_source


def reset_for_tests() -> None:
    global _fernet
    with _lock:
        _fernet = None


def _hint(value: str) -> str:
    return ("…" + value[-4:]) if len(value) >= 8 else "…"


def set_secret(db: Session, name: str, value: str | dict | None) -> None:
    if value is None or value == "":
        delete_secret(db, name)
        return
    raw = value if isinstance(value, str) else json.dumps(value)
    ct = fernet().encrypt(raw.encode("utf-8"))
    row = db.get(SecretValue, name)
    hint = _hint(raw) if isinstance(value, str) else "json"
    if row:
        row.ciphertext, row.hint = ct, hint
    else:
        db.add(SecretValue(name=name, ciphertext=ct, hint=hint))
    db.flush()


def get_secret(db: Session, name: str) -> str | None:
    row = db.get(SecretValue, name)
    if not row:
        return None
    try:
        return fernet().decrypt(row.ciphertext).decode("utf-8")
    except InvalidToken:
        log.error("Secret %s could not be decrypted (master key changed?)", name)
        return None


def get_secret_json(db: Session, name: str) -> dict[str, Any] | None:
    raw = get_secret(db, name)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def delete_secret(db: Session, name: str) -> None:
    row = db.get(SecretValue, name)
    if row:
        db.delete(row)
        db.flush()


def secret_status(db: Session, name: str) -> dict[str, Any]:
    row = db.get(SecretValue, name)
    return {"configured": row is not None, "hint": row.hint if row else None,
            "updated_at": row.updated_at.isoformat() if row else None}
