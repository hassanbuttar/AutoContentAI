"""Platform adapter contract. The scheduler/planner/renderer depend only on this interface."""
from __future__ import annotations

import base64
import hashlib
import secrets as pysecrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx


class PublishError(Exception):
    def __init__(self, message: str, retryable: bool = False, reauth: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.reauth = reauth


class NotConfigured(Exception):
    """OAuth client credentials / account settings missing. Message says exactly what to configure."""


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None  # naive UTC
    refresh_expires_at: datetime | None = None
    scope: str = ""
    extra: dict = field(default_factory=dict)  # open_id, ig_user_id, page_id, ...

    def to_dict(self) -> dict:
        return {"access_token": self.access_token, "refresh_token": self.refresh_token,
                "expires_at": self.expires_at.isoformat() if self.expires_at else None,
                "refresh_expires_at": self.refresh_expires_at.isoformat() if self.refresh_expires_at else None,
                "scope": self.scope, "extra": self.extra}

    @classmethod
    def from_dict(cls, d: dict) -> "TokenSet":
        def p(v):
            return datetime.fromisoformat(v) if v else None
        return cls(d["access_token"], d.get("refresh_token"), p(d.get("expires_at")), p(d.get("refresh_expires_at")),
                   d.get("scope", ""), d.get("extra") or {})

    def expiring(self, within: timedelta = timedelta(minutes=10)) -> bool:
        return bool(self.expires_at and self.expires_at - within <= datetime.now(timezone.utc).replace(tzinfo=None))


def expires_in(seconds: Any) -> datetime | None:
    try:
        return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=int(seconds))
    except (TypeError, ValueError):
        return None


@dataclass
class OAuthClient:
    client_id: str
    client_secret: str


@dataclass
class PostContent:
    title: str
    caption: str
    hashtags: list[str]
    description: str = ""
    tags: list[str] = field(default_factory=list)
    ai_generated_visuals: bool = False

    def caption_with_tags(self, limit: int = 2200) -> str:
        tags = " ".join(h for h in self.hashtags if h.lower() not in self.caption.lower())
        text = (self.caption.strip() + ("\n\n" + tags if tags else "")).strip()
        return text[:limit]


@dataclass
class PublishContext:
    video_path: Path
    video_meta: dict  # width, height, duration, size_bytes, fps
    post: PostContent
    tokens: TokenSet
    account_settings: dict
    state: dict  # platform_state persisted before risky steps
    save_state: Callable[[dict], None]
    progress: Callable[[str], None] = lambda m: None
    public_url: Callable[[], str] | None = None  # for platforms that pull media from a URL


@dataclass
class PublishResult:
    status: str  # published | sent_to_inbox | not_published | unknown
    external_id: str | None = None
    url: str | None = None
    detail: str = ""


@dataclass
class Metrics:
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    unavailable: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


class PlatformAdapter:
    platform = "base"
    display_name = "Base"
    max_duration = 60.0

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=httpx.Timeout(60, read=300))

    # ---- capabilities / config
    def capabilities(self, account_settings: dict) -> dict:
        return {"direct_publish": False, "oauth": False, "notes": []}

    def authorization_url(self, client: OAuthClient, redirect_uri: str, state: str, code_challenge: str, settings: dict) -> str:
        raise NotConfigured(f"{self.display_name} does not use OAuth")

    def exchange_code(self, client: OAuthClient, code: str, redirect_uri: str, code_verifier: str, settings: dict) -> TokenSet:
        raise NotImplementedError

    def connect_with_token(self, client: OAuthClient, access_token: str, settings: dict) -> TokenSet:
        """Connect with a token the user obtained from the platform's own developer tools (no redirect flow)."""
        raise NotConfigured(f"{self.display_name} only connects through the OAuth sign-in flow")

    def refresh(self, client: OAuthClient, tokens: TokenSet, settings: dict) -> TokenSet:
        raise PublishError("Token refresh not supported; reconnect the account", reauth=True)

    def account_info(self, tokens: TokenSet, settings: dict) -> dict:
        return {}

    # ---- publishing
    def preflight(self, video_meta: dict, post: PostContent, account_settings: dict) -> list[str]:
        from app.services.publishing import limits

        problems = []
        if self.platform in limits.LIMITS:  # official per-platform size/length limits
            fit = limits.check_platform(self.platform, video_meta.get("duration"), video_meta.get("size_bytes"))
            problems += [f"{self.display_name}: {p}" for p in fit["problems"]]
        elif video_meta.get("duration", 0) > self.max_duration:
            problems.append(f"{self.display_name}: video is {video_meta['duration']:.0f}s, max {self.max_duration:.0f}s")
        if (video_meta.get("width"), video_meta.get("height")) != (1080, 1920) and video_meta.get("height", 0) <= video_meta.get("width", 1):
            problems.append(f"{self.display_name}: video must be vertical")
        return problems

    def publish(self, ctx: PublishContext) -> PublishResult:
        raise NotImplementedError

    def verify(self, ctx: PublishContext) -> PublishResult:
        return PublishResult("unknown", detail="Verification not supported")

    def fetch_metrics(self, tokens: TokenSet, external_id: str, settings: dict) -> Metrics:
        return Metrics(unavailable={"all": f"{self.display_name} metrics not supported"})

    # ---- helpers
    @staticmethod
    def raise_for(r: httpx.Response, what: str) -> None:
        if r.status_code < 400:
            return
        try:
            body = r.json()
        except Exception:
            body = {"text": r.text[:300]}
        retry = r.status_code in (408, 429) or r.status_code >= 500
        reauth = r.status_code == 401
        raise PublishError(f"{what} failed (HTTP {r.status_code}): {str(body)[:500]}", retryable=retry, reauth=reauth)


def pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(pysecrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge
