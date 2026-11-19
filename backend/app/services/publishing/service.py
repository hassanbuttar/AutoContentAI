"""Publishing orchestration: tokens, idempotent publishing (compare-and-set), crash verification, metrics.

Duplicate-post prevention:
 1. one publication_record per (rule, slot) and one publish job per record (idempotency keys);
 2. approved -> publishing is a compare-and-set; only one worker can win;
 3. adapters persist platform ids (upload session / container / publish_id) BEFORE the final step;
 4. a record found in 'publishing' (crash/reboot) is never re-published blindly: adapter.verify() decides,
    and if the outcome is unknowable the record goes to 'verify_required' for a human."""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import secrets
from app.core.config import get_settings
from app.core.paths import storage_path
from app.models import (ContentIdea, ContentPerformance, OAuthConnection, PublicationRecord, RenderedVideo, SocialAccount,
                        VideoScript, utcnow)
from app.services.audit import job_log, notify
from app.services.publishing.base import (NotConfigured, OAuthClient, PostContent, PublishContext, PublishError,
                                          PublishResult, TokenSet)
from app.services.publishing.registry import export_dir_for, get_adapter
from app.services.settings_service import get_section
from app.workers.queue import FatalError, RetryableError

log = logging.getLogger(__name__)


def token_secret_name(account_id: int) -> str:
    return f"oauth.account.{account_id}"


def oauth_client(db: Session, platform: str) -> OAuthClient:
    cfg = get_section(db, "oauth").get(platform, {})
    cid = cfg.get("client_id") or cfg.get("client_key")
    secret = secrets.get_secret(db, f"oauth.{platform}.client_secret")
    if not cid or not secret:
        raise NotConfigured(f"{platform} OAuth client is not configured (Settings → Social platforms → {platform}: client id and secret)")
    return OAuthClient(cid, secret)


def account_settings(db: Session, account: SocialAccount) -> dict:
    platform_defaults = {k: v for k, v in get_section(db, "oauth").get(account.platform, {}).items() if k not in ("client_id", "client_key")}
    return {**platform_defaults, **(account.settings or {})}


def redirect_uri(db: Session, platform: str) -> str:
    s = get_settings()
    base = get_section(db, "oauth").get("redirect_base_url") or s.public_base_url or f"http://127.0.0.1:{s.port}"
    return f"{base.rstrip('/')}/api/oauth/callback/{platform}"


def load_tokens(db: Session, account: SocialAccount, refresh_if_needed: bool = True) -> TokenSet:
    raw = secrets.get_secret_json(db, token_secret_name(account.id))
    if not raw:
        raise PublishError(f"{account.display_name} is not connected", reauth=True)
    tokens = TokenSet.from_dict(raw)
    if refresh_if_needed and tokens.expiring():
        tokens = refresh_account_tokens(db, account, tokens)
    return tokens


def save_tokens(db: Session, account: SocialAccount, tokens: TokenSet) -> None:
    secrets.set_secret(db, token_secret_name(account.id), tokens.to_dict())
    conn = account.oauth or OAuthConnection(social_account_id=account.id, provider=account.platform,
                                            token_secret_name=token_secret_name(account.id))
    conn.scopes, conn.expires_at, conn.refresh_expires_at = tokens.scope, tokens.expires_at, tokens.refresh_expires_at
    conn.status, conn.last_error = "active", None
    if account.oauth is None:
        db.add(conn)
        account.oauth = conn
    account.status = "connected"


def refresh_account_tokens(db: Session, account: SocialAccount, tokens: TokenSet | None = None) -> TokenSet:
    tokens = tokens or TokenSet.from_dict(secrets.get_secret_json(db, token_secret_name(account.id)) or {})
    adapter = get_adapter(account.platform)
    try:
        new = adapter.refresh(oauth_client(db, account.platform), tokens, account_settings(db, account))
    except PublishError as exc:
        if exc.reauth:
            account.status = "needs_reauth"
            account.last_error = str(exc)[:1000]
            if account.oauth:
                account.oauth.status, account.oauth.last_error = "expired", str(exc)[:1000]
            notify(db, f"Reconnect {account.platform}: {account.display_name}", str(exc), "warning",
                   link="/accounts", event="reauth_required")
            db.commit()
        raise
    save_tokens(db, account, new)
    if account.oauth:
        account.oauth.last_refresh_at = utcnow()
    db.commit()
    return new


def refresh_expiring_tokens(db: Session) -> dict:
    done, failed = 0, 0
    for acc in db.scalars(select(SocialAccount).where(SocialAccount.status == "connected", SocialAccount.platform != "export")):
        raw = secrets.get_secret_json(db, token_secret_name(acc.id))
        if not raw:
            continue
        t = TokenSet.from_dict(raw)
        if t.expiring(timedelta(hours=2 if acc.platform == "tiktok" else 24 * 7 if acc.platform == "instagram" else 1)):
            try:
                refresh_account_tokens(db, acc, t)
                done += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                log.warning("token refresh failed for account %s: %s", acc.id, type(exc).__name__)
    return {"refreshed": done, "failed": failed}


# ------------------------------------------------------------------ public media links (Instagram Login variant)
def public_media_token(video_id: int, ttl_hours: int = 24) -> str:
    exp = int(time.time()) + ttl_hours * 3600
    key = hashlib.sha256(b"media:" + secrets.fernet()._signing_key).digest()  # noqa: SLF001
    sig = hmac.new(key, f"{video_id}.{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{video_id}.{exp}.{sig}"


def check_public_media_token(token: str) -> int | None:
    try:
        vid, exp, sig = token.split(".")
        key = hashlib.sha256(b"media:" + secrets.fernet()._signing_key).digest()  # noqa: SLF001
        good = hmac.new(key, f"{vid}.{exp}".encode(), hashlib.sha256).hexdigest()[:32]
        if hmac.compare_digest(sig, good) and int(exp) > time.time():
            return int(vid)
    except Exception:
        return None
    return None


# ------------------------------------------------------------------ publishing
def post_content(db: Session, pub: PublicationRecord, video: RenderedVideo) -> PostContent:
    script = db.get(VideoScript, video.video_script_id) if video.video_script_id else None
    sc = (script.social_content if script else {}) or {}
    plat = {"youtube": "youtube", "instagram": "instagram", "tiktok": "tiktok"}.get(pub.platform, "instagram")
    pc = sc.get(plat) or {}
    yt = sc.get("youtube") or {}
    caption = pub.caption or pc.get("caption") or (script.hook if script else "")
    hashtags = pub.hashtags or pc.get("hashtags") or (sc.get("instagram") or {}).get("hashtags") or []
    return PostContent(title=pub.title or yt.get("title") or (script.hook if script else "")[:100], caption=caption,
                       hashtags=hashtags, description=yt.get("description") or caption, tags=yt.get("tags") or [],
                       ai_generated_visuals="comfyui" in (video.renderer or ""))


def _ctx(db: Session, pub: PublicationRecord, account: SocialAccount, video: RenderedVideo, tokens: TokenSet,
         progress=lambda m: None) -> PublishContext:
    def save_state(patch: dict) -> None:
        # committed immediately so a crash after this point leaves evidence for verify()
        cur = dict(pub.platform_state or {})
        cur.update(patch)
        pub.platform_state = cur
        db.commit()

    base = get_settings().public_base_url

    def public_url() -> str:
        if not base:
            raise PublishError("AUTOCONTENT_PUBLIC_BASE_URL is not set; Meta cannot fetch the video")
        return f"{base.rstrip('/')}/media/public/{public_media_token(video.id)}.mp4"

    return PublishContext(video_path=storage_path(video.path), video_meta={"width": video.width, "height": video.height,
                          "duration": video.duration, "size_bytes": video.size_bytes, "fps": video.fps},
                          post=post_content(db, pub, video), tokens=tokens, account_settings=account_settings(db, account),
                          state=dict(pub.platform_state or {}), save_state=save_state, progress=progress,
                          public_url=public_url if base else None)


def _apply_result(db: Session, pub: PublicationRecord, account: SocialAccount, res: PublishResult) -> None:
    if res.status == "published":
        pub.status, pub.published_at = "published", utcnow()
        pub.external_post_id, pub.external_url = res.external_id, res.url
        pub.last_error = None
        account.last_published_at = utcnow()
        if pub.rendered_video_id:
            v = db.get(RenderedVideo, pub.rendered_video_id)
            if v and v.content_idea_id:
                idea = db.get(ContentIdea, v.content_idea_id)
                if idea:
                    idea.status = "used"
    elif res.status in ("exported", "sent_to_inbox"):
        pub.status = "exported"
        pub.export_path = res.detail if res.status == "exported" else None
        pub.last_error = None if res.status == "exported" else res.detail
    db.commit()


def publish_publication(db: Session, pub_id: int, job_id: int | None = None, progress=lambda m: None) -> dict:
    pub = db.get(PublicationRecord, pub_id)
    if pub is None:
        raise FatalError(f"Publication {pub_id} not found")
    # compare-and-set: approved -> publishing
    res = db.execute(update(PublicationRecord).where(PublicationRecord.id == pub_id, PublicationRecord.status == "approved")
                     .values(status="publishing", attempts=PublicationRecord.attempts + 1, updated_at=utcnow()))
    db.commit()
    db.refresh(pub)
    if res.rowcount == 0:
        if pub.status == "publishing":
            job_log(db, f"Publication {pub_id} was interrupted mid-publish; verifying with the platform", "warning", job_id=job_id)
            db.commit()
            return verify_publication(db, pub_id, job_id)
        return {"skipped": True, "status": pub.status, "reason": "not in approved state (already handled)"}
    account = db.get(SocialAccount, pub.social_account_id)
    video = db.get(RenderedVideo, pub.rendered_video_id) if pub.rendered_video_id else None
    try:
        if video is None or video.status != "ready" or not video.path or not storage_path(video.path).exists():
            raise PublishError("Video file is missing or not ready")
        if video.review_status == "rejected":
            raise PublishError("Video was rejected in review")
        platform = account.platform
        if account.status == "export_only" or platform == "export":
            platform = "export"
            pub.platform_state = {**(pub.platform_state or {}),
                                  "export_dir": str(export_dir_for(account.platform, pub.id, pub.scheduled_for.strftime("%Y%m%d_%H%M")))}
            db.commit()
        adapter = get_adapter(platform)
        tokens = load_tokens(db, account) if platform != "export" else TokenSet("")
        ctx = _ctx(db, pub, account, video, tokens, progress)
        problems = adapter.preflight(ctx.video_meta, ctx.post, ctx.account_settings) if platform != "export" else []
        if problems:
            raise PublishError("; ".join(problems))
        result = adapter.publish(ctx)
    except PublishError as exc:
        db.rollback()
        pub = db.get(PublicationRecord, pub_id)
        account = db.get(SocialAccount, pub.social_account_id)
        pub.last_error = str(exc)[:2000]
        if exc.reauth:
            account.status = "needs_reauth"
            account.last_error = str(exc)[:1000]
        if exc.retryable:
            pub.status = "approved"  # safe: adapter state is persisted; retry resumes or verifies
            db.commit()
            raise RetryableError(str(exc)) from exc
        pub.status = "failed"
        notify(db, f"Publishing failed: {account.display_name}", str(exc), "error", link=f"/queue?pub={pub.id}", event="publish_failed")
        db.commit()
        raise FatalError(str(exc)) from exc
    _apply_result(db, pub, account, result)
    job_log(db, f"Publication {pub.id} -> {pub.status} {pub.external_url or pub.export_path or ''}", job_id=job_id,
            application_id=pub.application_id)
    db.commit()
    return {"status": pub.status, "external_id": pub.external_post_id, "url": pub.external_url}


def verify_publication(db: Session, pub_id: int, job_id: int | None = None) -> dict:
    pub = db.get(PublicationRecord, pub_id)
    account = db.get(SocialAccount, pub.social_account_id)
    video = db.get(RenderedVideo, pub.rendered_video_id) if pub.rendered_video_id else None
    if account.platform == "export" or account.status == "export_only":
        pub.status = "approved"  # export is local and idempotent
        db.commit()
        return publish_publication(db, pub_id, job_id)
    try:
        tokens = load_tokens(db, account)
        res = get_adapter(account.platform).verify(_ctx(db, pub, account, video, tokens))
    except PublishError as exc:
        res = PublishResult("unknown", detail=str(exc))
    if res.status in ("published", "sent_to_inbox"):
        _apply_result(db, pub, account, res)
        return {"status": pub.status, "verified": True}
    if res.status == "not_published":
        pub.status = "approved"
        pub.last_error = f"Verified not published ({res.detail}); will retry"
        db.commit()
        return publish_publication(db, pub_id, job_id)
    pub.status = "verify_required"
    pub.last_error = f"Could not confirm whether the post went live ({res.detail}). Check the platform, then mark it published or retry."
    notify(db, "Publication needs manual verification", pub.last_error, "warning", link=f"/queue?pub={pub.id}", event="publish_failed")
    db.commit()
    return {"status": pub.status, "verified": False}


def fetch_all_metrics(db: Session, max_age_days: int = 30) -> dict:
    since = utcnow() - timedelta(days=max_age_days)
    n = errors = 0
    pubs = db.scalars(select(PublicationRecord).where(PublicationRecord.status == "published",
                                                      PublicationRecord.external_post_id.is_not(None),
                                                      PublicationRecord.published_at >= since))
    for pub in pubs:
        account = db.get(SocialAccount, pub.social_account_id)
        if account.platform == "export":
            continue
        try:
            tokens = load_tokens(db, account)
            m = get_adapter(account.platform).fetch_metrics(tokens, pub.external_post_id, account_settings(db, account))
            db.add(ContentPerformance(publication_record_id=pub.id, views=m.views, likes=m.likes, comments=m.comments,
                                      shares=m.shares, unavailable=m.unavailable, raw=m.raw))
            db.commit()
            n += 1
        except Exception as exc:  # noqa: BLE001 - metrics are best-effort
            db.rollback()
            errors += 1
            log.warning("metrics failed for publication %s: %s", pub.id, type(exc).__name__)
    return {"fetched": n, "errors": errors}


_ = datetime
