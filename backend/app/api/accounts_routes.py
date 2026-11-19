from __future__ import annotations

import secrets as pysecrets
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select

from app.api.deps import (AuthUser, Depends, Session, User, get_db, get_or_404, iso, ser_account, ser_pub, ser_rule,
                          to_utc_naive)
from app.core import secrets
from app.models import (Application, OAuthState, PublicationRecord, PublishingRule, SocialAccount, utcnow)
from app.schemas.api import AccountIn, AccountPatch, RuleIn
from app.services.audit import audit
from app.services.publishing import service as pubsvc
from app.services.publishing.base import NotConfigured, PublishError, pkce_pair
from app.services.publishing.registry import get_adapter
from app.services.scheduling.slots import RuleSpec, compute_slots

router = APIRouter(prefix="/api", tags=["accounts"])


def _caps(db: Session, a: SocialAccount) -> dict:
    return get_adapter(a.platform).capabilities(pubsvc.account_settings(db, a))


@router.get("/accounts")
def list_accounts(application_id: int | None = None, user: User = AuthUser, db: Session = Depends(get_db)):
    q = select(SocialAccount).order_by(SocialAccount.application_id, SocialAccount.platform)
    if application_id:
        q = q.where(SocialAccount.application_id == application_id)
    return [ser_account(a, _caps(db, a)) for a in db.scalars(q)]


@router.post("/accounts", status_code=201)
def create_account(body: AccountIn, user: User = AuthUser, db: Session = Depends(get_db)):
    get_or_404(db, Application, body.application_id)
    a = SocialAccount(application_id=body.application_id, platform=body.platform, display_name=body.display_name,
                      publishing_mode=body.publishing_mode, settings=body.settings,
                      status="export_only" if (body.export_only or body.platform == "export") else "disconnected")
    db.add(a)
    db.flush()
    audit(db, "account.create", user.id, "account", a.id, {"platform": a.platform})
    db.commit()
    return ser_account(a, _caps(db, a))


@router.patch("/accounts/{account_id}")
def patch_account(account_id: int, body: AccountPatch, user: User = AuthUser, db: Session = Depends(get_db)):
    a = get_or_404(db, SocialAccount, account_id)
    d = body.model_dump(exclude_unset=True)
    if "display_name" in d and d["display_name"]:
        a.display_name = d["display_name"]
    if "publishing_mode" in d:
        a.publishing_mode = d["publishing_mode"]
    if d.get("settings") is not None:
        a.settings = {**(a.settings or {}), **d["settings"]}
    if d.get("paused") is not None:
        a.paused = d["paused"]
    if d.get("export_only") is not None and a.platform != "export":
        if d["export_only"]:
            a.status = "export_only"
        elif a.status == "export_only":
            a.status = "connected" if secrets.get_secret(db, pubsvc.token_secret_name(a.id)) else "disconnected"
    db.commit()
    return ser_account(a, _caps(db, a))


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    a = get_or_404(db, SocialAccount, account_id)
    secrets.delete_secret(db, pubsvc.token_secret_name(a.id))
    audit(db, "account.delete", user.id, "account", a.id)
    db.delete(a)
    db.commit()
    return {"ok": True}


@router.post("/accounts/{account_id}/oauth/start")
def oauth_start(account_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    a = get_or_404(db, SocialAccount, account_id)
    if a.platform == "export":
        raise HTTPException(400, "Export accounts do not connect")
    try:
        client = pubsvc.oauth_client(db, a.platform)
    except NotConfigured as exc:
        raise HTTPException(400, str(exc)) from exc
    db.execute(delete(OAuthState).where(OAuthState.expires_at < utcnow()))
    state = pysecrets.token_urlsafe(32)
    verifier, challenge = pkce_pair()
    redirect = pubsvc.redirect_uri(db, a.platform)
    db.add(OAuthState(state=state, social_account_id=a.id, platform=a.platform, code_verifier=verifier, redirect_uri=redirect,
                      user_id=user.id, expires_at=utcnow() + timedelta(minutes=15)))
    db.commit()
    url = get_adapter(a.platform).authorization_url(client, redirect, state, challenge, pubsvc.account_settings(db, a))
    return {"authorization_url": url, "redirect_uri": redirect}


@router.get("/oauth/callback/{platform}")
def oauth_callback(platform: str, state: str = Query(""), code: str = Query(""), error: str = Query(""),
                   error_description: str = Query(""), db: Session = Depends(get_db)):
    """Browser redirect target. Authenticated by the single-use, short-lived `state` value."""
    st = db.get(OAuthState, state) if state else None
    if st is None or st.platform != platform or st.expires_at < utcnow():
        return RedirectResponse("/accounts?oauth=error&reason=invalid_state", status_code=302)
    account_id = st.social_account_id
    verifier, redirect = st.code_verifier, st.redirect_uri
    db.delete(st)  # single use
    db.commit()
    a = db.get(SocialAccount, account_id)
    if error or not code:
        if a:
            a.last_error = f"Authorization declined: {error} {error_description}"[:500]
            db.commit()
        return RedirectResponse(f"/accounts?oauth=error&account={account_id}&reason=declined", status_code=302)
    try:
        adapter = get_adapter(platform)
        settings = pubsvc.account_settings(db, a)
        tokens = adapter.exchange_code(pubsvc.oauth_client(db, platform), code, redirect, verifier, settings)
        info = adapter.account_info(tokens, settings)
        pubsvc.save_tokens(db, a, tokens)
        a.external_account_id = info.get("external_account_id")
        if info.get("display_name"):
            a.settings = {**(a.settings or {}), "platform_display_name": info["display_name"]}
        a.capabilities = adapter.capabilities(settings)
        a.last_error = None
        audit(db, "account.oauth_connected", st.user_id, "account", a.id, {"platform": platform})
        db.commit()
    except (PublishError, NotConfigured, Exception) as exc:  # noqa: BLE001 - surface any provider failure
        db.rollback()
        a = db.get(SocialAccount, account_id)
        a.status, a.last_error = "error", f"OAuth failed: {exc}"[:1000]
        db.commit()
        return RedirectResponse(f"/accounts?oauth=error&account={account_id}&reason=exchange_failed", status_code=302)
    return RedirectResponse(f"/accounts?oauth=connected&account={account_id}", status_code=302)


@router.post("/accounts/{account_id}/disconnect")
def disconnect(account_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    a = get_or_404(db, SocialAccount, account_id)
    secrets.delete_secret(db, pubsvc.token_secret_name(a.id))
    if a.oauth:
        db.delete(a.oauth)
    a.status = "disconnected"
    audit(db, "account.disconnect", user.id, "account", a.id)
    db.commit()
    return ser_account(a, _caps(db, a))


@router.post("/accounts/{account_id}/test")
def test_account(account_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    a = get_or_404(db, SocialAccount, account_id)
    if a.platform == "export" or a.status == "export_only":
        return {"ok": True, "detail": "Manual export account"}
    try:
        tokens = pubsvc.load_tokens(db, a)
        info = get_adapter(a.platform).account_info(tokens, pubsvc.account_settings(db, a))
        return {"ok": True, "detail": f"Connected as {info.get('display_name')}"}
    except (PublishError, NotConfigured) as exc:
        return {"ok": False, "detail": str(exc)}


@router.post("/accounts/{account_id}/refresh")
def refresh(account_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    a = get_or_404(db, SocialAccount, account_id)
    try:
        pubsvc.refresh_account_tokens(db, a)
        return {"ok": True}
    except (PublishError, NotConfigured) as exc:
        raise HTTPException(400, str(exc)) from exc


# ------------------------------------------------------------------ rules
@router.get("/rules")
def list_rules(application_id: int | None = None, user: User = AuthUser, db: Session = Depends(get_db)):
    q = select(PublishingRule).order_by(PublishingRule.application_id, PublishingRule.id)
    if application_id:
        q = q.where(PublishingRule.application_id == application_id)
    return [ser_rule(r) for r in db.scalars(q)]


@router.post("/rules", status_code=201)
def create_rule(body: RuleIn, user: User = AuthUser, db: Session = Depends(get_db)):
    acc = get_or_404(db, SocialAccount, body.social_account_id)
    r = PublishingRule(application_id=acc.application_id, **body.model_dump())
    db.add(r)
    db.flush()
    audit(db, "rule.create", user.id, "rule", r.id)
    db.commit()
    return ser_rule(r)


@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, body: RuleIn, user: User = AuthUser, db: Session = Depends(get_db)):
    r = get_or_404(db, PublishingRule, rule_id)
    acc = get_or_404(db, SocialAccount, body.social_account_id)
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    r.application_id = acc.application_id
    db.commit()
    return ser_rule(r)


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, user: User = AuthUser, db: Session = Depends(get_db)):
    r = get_or_404(db, PublishingRule, rule_id)
    for p in db.scalars(select(PublicationRecord).where(PublicationRecord.publishing_rule_id == r.id,
                                                        PublicationRecord.status.in_(["planned"]))):
        p.status = "cancelled"
    db.delete(r)
    db.commit()
    return {"ok": True}


@router.post("/rules/preview")
def preview_rule(body: RuleIn, days: int = 14, user: User = AuthUser):
    now = utcnow()
    slots = compute_slots(RuleSpec(**{k: v for k, v in body.model_dump().items() if k in RuleSpec.__dataclass_fields__}),
                          now, now + timedelta(days=min(days, 60)))
    return {"slots": [iso(s) for s in slots], "count": len(slots)}


# ------------------------------------------------------------------ calendar
@router.get("/calendar")
def calendar(start: datetime, end: datetime, application_id: int | None = None, user: User = AuthUser,
             db: Session = Depends(get_db)):
    start, end = to_utc_naive(start), to_utc_naive(end)
    if end - start > timedelta(days=62):
        raise HTTPException(400, "Range too large (max 62 days)")
    q = (select(PublicationRecord, SocialAccount, Application)
         .join(SocialAccount, PublicationRecord.social_account_id == SocialAccount.id)
         .join(Application, PublicationRecord.application_id == Application.id)
         .where(PublicationRecord.scheduled_for >= start, PublicationRecord.scheduled_for < end))
    if application_id:
        q = q.where(PublicationRecord.application_id == application_id)
    items = [{**ser_pub(p, a, ap), "projected": False} for p, a, ap in db.execute(q).all()]
    existing = {(i["publishing_rule_id"], i["scheduled_for"]) for i in items}
    # projected (not yet materialized) slots so the calendar shows the full plan
    rq = select(PublishingRule, SocialAccount, Application).join(SocialAccount, PublishingRule.social_account_id == SocialAccount.id) \
        .join(Application, PublishingRule.application_id == Application.id).where(PublishingRule.enabled.is_(True))
    if application_id:
        rq = rq.where(PublishingRule.application_id == application_id)
    now = utcnow()
    for r, a, ap in db.execute(rq).all():
        if a.paused or ap.paused or not ap.active:
            continue
        for s in compute_slots(RuleSpec.from_model(r), max(start, now), end):
            if (r.id, iso(s)) in existing:
                continue
            items.append({"id": None, "projected": True, "application_id": ap.id, "application_name": ap.name,
                          "social_account_id": a.id, "account_name": a.display_name, "platform": a.platform,
                          "publishing_rule_id": r.id, "scheduled_for": iso(s), "status": "projected"})
    items.sort(key=lambda x: x["scheduled_for"])
    return items
