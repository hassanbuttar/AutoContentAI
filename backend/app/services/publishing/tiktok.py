"""TikTok via the Content Posting API (Direct Post or Upload-to-inbox).

Verified against developers.tiktok.com (Nov 2026):
  creator info: POST https://open.tiktokapis.com/v2/post/publish/creator_info/query/
  init:  POST /v2/post/publish/video/init/ (direct, scope video.publish) or /v2/post/publish/inbox/video/init/ (scope video.upload)
         source_info FILE_UPLOAD with video_size/chunk_size/total_chunk_count; upload_url valid 1 hour
  upload: PUT upload_url with Content-Range; chunks >= 5 MB (<= 64 MB), last chunk may hold the remainder (<= 128 MB)
  status: POST /v2/post/publish/status/fetch/ {publish_id}
  token: POST https://open.tiktokapis.com/v2/oauth/token/ (access 2h, refresh 180d, refresh token rotates)
Unaudited clients may only post with private (SELF_ONLY) visibility; audit is required for public posts."""
from __future__ import annotations

import math
import time
from urllib.parse import urlencode

from app.services.publishing.base import (Metrics, OAuthClient, PlatformAdapter, PublishContext, PublishError,
                                          PublishResult, TokenSet, expires_in)

API = "https://open.tiktokapis.com"
MB = 1024 * 1024


def chunk_plan(size: int, preferred: int = 10 * MB) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) following the media transfer rules."""
    if size < 5 * MB:
        return size, 1
    if size <= 64 * MB and size <= preferred:
        return size, 1
    chunk = max(5 * MB, min(preferred, 64 * MB))
    count = max(1, size // chunk)
    if size - chunk * (count - 1) > 128 * MB:  # pragma: no cover - only for >1GB files
        count = math.ceil(size / chunk)
    return chunk, count


class TikTokAdapter(PlatformAdapter):
    platform = "tiktok"
    display_name = "TikTok"
    max_duration = 600.0

    def capabilities(self, s: dict) -> dict:
        mode = s.get("post_mode", "direct")
        return {"direct_publish": mode == "direct", "oauth": True, "metrics": ["views", "likes", "comments", "shares"],
                "notes": ["Requires a TikTok developer app with Content Posting API.",
                          "Until TikTok audits the app, posts are restricted to private (SELF_ONLY) visibility.",
                          "Inbox mode sends a draft to the TikTok app; the creator finishes posting there.",
                          "6 init requests per minute per user token; daily post caps apply."]}

    def authorization_url(self, client: OAuthClient, redirect_uri: str, state: str, code_challenge: str, s: dict) -> str:
        scope = "user.info.basic,video.publish,video.upload" + (",video.list" if s.get("metrics", True) else "")
        q = {"client_key": client.client_id, "scope": scope, "response_type": "code", "redirect_uri": redirect_uri, "state": state}
        if s.get("use_pkce"):
            q.update({"code_challenge": code_challenge, "code_challenge_method": "S256"})
        return "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(q)

    def _token(self, data: dict) -> TokenSet:
        r = self.http.post(f"{API}/v2/oauth/token/", data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.raise_for(r, "TikTok token")
        d = r.json()
        if "access_token" not in d:
            raise PublishError(f"TikTok token error: {d.get('error_description') or d.get('error') or d}",
                               reauth=data.get("grant_type") == "refresh_token")
        return TokenSet(d["access_token"], d.get("refresh_token"), expires_in(d.get("expires_in")),
                        expires_in(d.get("refresh_expires_in")), d.get("scope", ""), {"open_id": d.get("open_id")})

    def exchange_code(self, client, code, redirect_uri, code_verifier, s) -> TokenSet:
        data = {"client_key": client.client_id, "client_secret": client.client_secret, "code": code,
                "grant_type": "authorization_code", "redirect_uri": redirect_uri}
        if s.get("use_pkce"):
            data["code_verifier"] = code_verifier
        return self._token(data)

    def refresh(self, client, tokens: TokenSet, s) -> TokenSet:
        if not tokens.refresh_token:
            raise PublishError("No TikTok refresh token; reconnect", reauth=True)
        new = self._token({"client_key": client.client_id, "client_secret": client.client_secret,
                           "grant_type": "refresh_token", "refresh_token": tokens.refresh_token})
        new.extra = {**tokens.extra, **{k: v for k, v in new.extra.items() if v}}
        return new  # refresh token rotates: caller must persist the new one

    @staticmethod
    def _h(tokens: TokenSet) -> dict:
        return {"Authorization": f"Bearer {tokens.access_token}", "Content-Type": "application/json; charset=UTF-8"}

    def _json(self, r, what: str) -> dict:
        self.raise_for(r, what)
        d = r.json()
        err = d.get("error") or {}
        if err.get("code") not in (None, "ok"):
            code = err.get("code")
            retry = code in ("rate_limit_exceeded", "internal_error")
            raise PublishError(f"{what}: {code} - {err.get('message', '')}", retryable=retry,
                               reauth=code in ("access_token_invalid", "scope_not_authorized"))
        return d.get("data") or {}

    def account_info(self, tokens: TokenSet, s: dict) -> dict:
        r = self.http.get(f"{API}/v2/user/info/", params={"fields": "open_id,display_name"}, headers=self._h(tokens))
        d = self._json(r, "TikTok user info").get("user", {})
        return {"external_account_id": d.get("open_id") or tokens.extra.get("open_id"), "display_name": d.get("display_name", "TikTok")}

    def creator_info(self, tokens: TokenSet) -> dict:
        r = self.http.post(f"{API}/v2/post/publish/creator_info/query/", headers=self._h(tokens), json={})
        return self._json(r, "TikTok creator info")

    def publish(self, ctx: PublishContext) -> PublishResult:
        s = ctx.account_settings
        mode = s.get("post_mode", "direct")
        publish_id = ctx.state.get("publish_id")
        if not publish_id:
            size = ctx.video_path.stat().st_size
            chunk, count = chunk_plan(size)
            source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
            if mode == "direct":
                ci = self.creator_info(ctx.tokens)
                options = ci.get("privacy_level_options") or []
                privacy = s.get("privacy_level", "SELF_ONLY")
                if options and privacy not in options:
                    raise PublishError(f"TikTok privacy '{privacy}' not allowed for this creator; options: {options}")
                maxd = ci.get("max_video_post_duration_sec")
                if maxd and ctx.video_meta.get("duration", 0) > maxd:
                    raise PublishError(f"Video is longer than this creator may post ({maxd}s)")
                post_info = {"title": ctx.post.caption_with_tags(2200), "privacy_level": privacy,
                             "disable_comment": bool(s.get("disable_comment") or ci.get("comment_disabled")),
                             "disable_duet": bool(s.get("disable_duet") or ci.get("duet_disabled")),
                             "disable_stitch": bool(s.get("disable_stitch") or ci.get("stitch_disabled")),
                             "video_cover_timestamp_ms": 1000, "is_aigc": bool(ctx.post.ai_generated_visuals)}
                body = {"post_info": post_info, "source_info": source}
                url = f"{API}/v2/post/publish/video/init/"
            else:
                body = {"source_info": source}
                url = f"{API}/v2/post/publish/inbox/video/init/"
            data = self._json(self.http.post(url, headers=self._h(ctx.tokens), json=body), "TikTok init")
            publish_id = data["publish_id"]
            ctx.save_state({"publish_id": publish_id, "mode": mode})
            ctx.progress("Uploading to TikTok")
            with open(ctx.video_path, "rb") as fh:  # one chunk in memory at a time (files can be up to 4 GB)
                for i in range(count):
                    start = i * chunk
                    end = size if i == count - 1 else start + chunk
                    fh.seek(start)
                    part = fh.read(end - start)
                    u = self.http.put(data["upload_url"], content=part, headers={
                        "Content-Type": "video/mp4", "Content-Length": str(len(part)),
                        "Content-Range": f"bytes {start}-{end - 1}/{size}"})
                    self.raise_for(u, f"TikTok chunk {i + 1}/{count}")
        return self._wait(ctx, publish_id, float(s.get("processing_timeout", 600)))

    def _fetch(self, ctx: PublishContext, publish_id: str) -> dict:
        r = self.http.post(f"{API}/v2/post/publish/status/fetch/", headers=self._h(ctx.tokens), json={"publish_id": publish_id})
        return self._json(r, "TikTok status")

    def _interpret(self, d: dict) -> PublishResult | None:
        st = d.get("status")
        if st == "PUBLISH_COMPLETE":
            ids = d.get("publicaly_available_post_id") or d.get("publicly_available_post_id") or []
            pid = str(ids[0]) if ids else None
            return PublishResult("published", pid, None, detail="published" + ("" if pid else " (private; no public post id)"))
        if st == "SEND_TO_USER_INBOX":
            return PublishResult("sent_to_inbox", None, None, detail="Draft delivered to the TikTok app inbox")
        if st == "FAILED":
            reason = d.get("fail_reason", "unknown")
            raise PublishError(f"TikTok publish failed: {reason}", retryable=reason in ("internal", "video_pull_failed"))
        return None

    def _wait(self, ctx: PublishContext, publish_id: str, timeout: float) -> PublishResult:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            res = self._interpret(self._fetch(ctx, publish_id))
            if res:
                return res
            ctx.progress("TikTok processing")
            time.sleep(float(ctx.account_settings.get("poll_seconds", 10)))
        raise PublishError("TikTok processing timed out; will verify", retryable=True)

    def verify(self, ctx: PublishContext) -> PublishResult:
        pid = ctx.state.get("publish_id")
        if not pid:
            return PublishResult("not_published", detail="init never completed")
        try:
            res = self._interpret(self._fetch(ctx, pid))
        except PublishError as exc:
            ctx.save_state({"publish_id": None})
            return PublishResult("not_published", detail=str(exc))
        return res or PublishResult("unknown", detail="still processing")

    def fetch_metrics(self, tokens: TokenSet, external_id: str, s: dict) -> Metrics:
        if "video.list" not in (tokens.scope or ""):
            return Metrics(unavailable={"all": "TikTok metrics need the video.list scope; reconnect the account"})
        r = self.http.post(f"{API}/v2/video/query/", params={"fields": "id,view_count,like_count,comment_count,share_count"},
                           headers=self._h(tokens), json={"filters": {"video_ids": [external_id]}})
        d = self._json(r, "TikTok video query")
        vids = d.get("videos") or []
        if not vids:
            return Metrics(unavailable={"all": "video not returned (private posts may be excluded)"})
        v = vids[0]
        return Metrics(v.get("view_count"), v.get("like_count"), v.get("comment_count"), v.get("share_count"), {}, v)
