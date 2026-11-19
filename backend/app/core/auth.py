"""Local authentication: scrypt password hashes + signed session cookie (JWT HS256)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.models import User, utcnow

COOKIE_NAME = "ac_session"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), **_SCRYPT)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


def _signing_key() -> str:
    s = get_settings()
    if s.session_secret:
        return s.session_secret
    from app.core.secrets import fernet  # derive from master key, stable across restarts

    return hashlib.sha256(b"session:" + fernet()._signing_key).hexdigest()  # noqa: SLF001


def create_session_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user.id), "ver": user.session_version, "iat": now, "exp": now + timedelta(hours=s.session_hours)}
    return jwt.encode(payload, _signing_key(), algorithm="HS256")


def decode_session_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _signing_key(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def set_session_cookie(response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        COOKIE_NAME, token, httponly=True, secure=s.cookie_secure,
        samesite="none" if (s.cookie_secure and s.cors_origins) else "lax",
        max_age=s.session_hours * 3600, path="/",
    )


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    data = decode_session_token(token)
    if not data:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    user = db.get(User, int(data["sub"]))
    if not user or user.session_version != data.get("ver"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session revoked")
    return user


def touch_login(db: Session, user: User) -> None:
    user.last_login_at = utcnow()
    db.commit()
