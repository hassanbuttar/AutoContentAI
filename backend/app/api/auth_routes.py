from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import func, select

from app.api.deps import AuthUser, Depends, Session, User, get_db
from app.core.auth import COOKIE_NAME, create_session_token, hash_password, set_session_cookie, touch_login, verify_password
from app.schemas.api import LoginIn, PasswordChangeIn, SetupIn
from app.services.audit import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])
_failures: dict[str, list[float]] = {}


def _rate_limit(key: str) -> None:
    now = time.time()
    attempts = [t for t in _failures.get(key, []) if now - t < 300]
    _failures[key] = attempts
    if len(attempts) >= 8:
        raise HTTPException(429, "Too many failed logins; wait 5 minutes")


@router.get("/status")
def status(request: Request, db: Session = Depends(get_db)):
    users = db.scalar(select(func.count(User.id))) or 0
    from app.core.auth import decode_session_token

    token = request.cookies.get(COOKIE_NAME)
    data = decode_session_token(token) if token else None
    user = db.get(User, int(data["sub"])) if data else None
    if user and user.session_version != data.get("ver"):
        user = None
    return {"setup_required": users == 0, "authenticated": user is not None,
            "user": {"id": user.id, "username": user.username} if user else None}


@router.post("/setup")
def setup(body: SetupIn, response: Response, request: Request, db: Session = Depends(get_db)):
    if (db.scalar(select(func.count(User.id))) or 0) > 0:
        raise HTTPException(409, "Setup already completed")
    user = User(username=body.username, password_hash=hash_password(body.password), is_admin=True)
    db.add(user)
    db.flush()
    audit(db, "user.setup", user.id, "user", user.id, ip=request.client.host if request.client else None)
    db.commit()
    set_session_cookie(response, create_session_token(user))
    return {"id": user.id, "username": user.username}


@router.post("/login")
def login(body: LoginIn, response: Response, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    _rate_limit(ip)
    user = db.scalar(select(User).where(User.username == body.username))
    if not user or not verify_password(body.password, user.password_hash):
        _failures.setdefault(ip, []).append(time.time())
        audit(db, "user.login_failed", None, "user", body.username[:64], ip=ip)
        db.commit()
        raise HTTPException(401, "Invalid username or password")
    touch_login(db, user)
    audit(db, "user.login", user.id, ip=ip)
    db.commit()
    set_session_cookie(response, create_session_token(user))
    return {"id": user.id, "username": user.username}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/change-password")
def change_password(body: PasswordChangeIn, response: Response, user: User = AuthUser, db: Session = Depends(get_db)):
    user = db.get(User, user.id)
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    user.session_version += 1  # revoke all other sessions
    audit(db, "user.password_changed", user.id)
    db.commit()
    set_session_cookie(response, create_session_token(user))
    return {"ok": True}
