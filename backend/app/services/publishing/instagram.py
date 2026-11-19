"""Instagram Reels via the Instagram Platform content publishing API.

Verified against developers.facebook.com/docs/instagram-platform/content-publishing (Nov 2026):
  container: POST /<IG_ID>/media media_type=REELS (video_url | upload_type=resumable)
  resumable upload: POST https://rupload.facebook.com/ig-api-upload/<ver>/<container> (Authorization: OAuth, offset, file_size)
  status: GET /<container>?fields=status_code  (IN_PROGRESS|FINISHED|PUBLISHED|ERROR|EXPIRED)
  publish: POST /<IG_ID>/media_publish creation_id=<container>; limit checked via /content_publishing_limit
Variants:
  facebook_login  (graph.facebook.com) - local file upload via rupload (documented for Facebook Login for Business)
  instagram_login (graph.instagram.com) - Meta must fetch a PUBLIC video_url; requires AUTOCONTENT_PUBLIC_BASE_URL
Requires an Instagram professional (business/creator) account."""
from __future__ import annotations

import time
from urllib.parse import urlencode

from app.services.publishing.base import (Metrics, OAuthClient, PlatformAdapter, PublishContext, PublishError,
                                          PublishResult, TokenSet, expires_in)

FB_SCOPES = "instagram_basic,instagram_content_publish,instagram_manage_insights,pages_show_list,pages_read_engagement,business_management"
IG_SCOPES = "instagram_business_basic,instagram_business_content_publish,instagram_business_manage_insights"


class InstagramAdapter(PlatformAdapter):
    platform = "instagram"
    display_name = "Instagram Reels"
    max_duration = 180.0

    def _variant(self, s: dict) -> str:
        return s.get("variant", "facebook_login")

    def _ver(self, s: dict) -> str:
        return s.get("graph_version", "v25.0")

    def _graph(self, s: dict) -> str:
        host = "graph.instagram.com" if self._variant(s) == "instagram_login" else "graph.facebook.com"
        return f"https://{host}/{self._ver(s)}"

    def capabilities(self, s: dict) -> dict:
        notes = ["Requires an Instagram professional account and a Meta app with content publishing permission (app review for other users).",
                 "Meta limits API publishing per account per rolling 24 hours (checked via content_publishing_limit)."]
        if self._variant(s) == "instagram_login":
            notes.append("Instagram Login variant: Meta downloads the video from a public URL. Set AUTOCONTENT_PUBLIC_BASE_URL (e.g. a tunnel).")
        return {"direct_publish": True, "oauth": True, "metrics": ["views", "likes", "comments", "shares"], "notes": notes}

    # ---------------------------------------------------------------- OAuth
    def authorization_url(self, client: OAuthClient, redirect_uri: str, state: str, code_challenge: str, s: dict) -> str:
        if self._variant(s) == "instagram_login":
            return "https://www.instagram.com/oauth/authorize?" + urlencode(
                {"client_id": client.client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": IG_SCOPES, "state": state})
        return f"https://www.facebook.com/{self._ver(s)}/dialog/oauth?" + urlencode(
            {"client_id": client.client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": FB_SCOPES, "state": state})

    def exchange_code(self, client, code, redirect_uri, code_verifier, s) -> TokenSet:
        if self._variant(s) == "instagram_login":
            r = self.http.post("https://api.instagram.com/oauth/access_token",
                               data={"client_id": client.client_id, "client_secret": client.client_secret,
                                     "grant_type": "authorization_code", "redirect_uri": redirect_uri, "code": code})
            self.raise_for(r, "Instagram token exchange")
            short = r.json()
            r = self.http.get("https://graph.instagram.com/access_token",
                              params={"grant_type": "ig_exchange_token", "client_secret": client.client_secret, "access_token": short["access_token"]})
            self.raise_for(r, "Instagram long-lived token")
            d = r.json()
            return TokenSet(d["access_token"], None, expires_in(d.get("expires_in")), None, IG_SCOPES,
                            {"ig_user_id": str(short.get("user_id", ""))})
        r = self.http.get(f"https://graph.facebook.com/{self._ver(s)}/oauth/access_token",
                          params={"client_id": client.client_id, "client_secret": client.client_secret, "redirect_uri": redirect_uri, "code": code})
        self.raise_for(r, "Facebook token exchange")
        short = r.json()["access_token"]
        r = self.http.get(f"https://graph.facebook.com/{self._ver(s)}/oauth/access_token",
                          params={"grant_type": "fb_exchange_token", "client_id": client.client_id,
                                  "client_secret": client.client_secret, "fb_exchange_token": short})
        self.raise_for(r, "Facebook long-lived token")
        d = r.json()
        return TokenSet(d["access_token"], None, expires_in(d.get("expires_in", 60 * 24 * 3600)), None, FB_SCOPES)

    def connect_with_token(self, client, access_token: str, s) -> TokenSet:
        """Facebook Login variant: accept a user access token from Graph API Explorer (needs the FB_SCOPES
        permissions) and exchange it for a long-lived (~60 day) token with the app id/secret."""
        if self._variant(s) == "instagram_login":
            r = self.http.get("https://graph.instagram.com/access_token",
                              params={"grant_type": "ig_exchange_token", "client_secret": client.client_secret,
                                      "access_token": access_token})
            self.raise_for(r, "Instagram long-lived token")
            d = r.json()
            return TokenSet(d["access_token"], None, expires_in(d.get("expires_in")), None, IG_SCOPES)
        r = self.http.get(f"https://graph.facebook.com/{self._ver(s)}/oauth/access_token",
                          params={"grant_type": "fb_exchange_token", "client_id": client.client_id,
                                  "client_secret": client.client_secret, "fb_exchange_token": access_token})
        self.raise_for(r, "Facebook long-lived token")
        d = r.json()
        return TokenSet(d["access_token"], None, expires_in(d.get("expires_in", 60 * 24 * 3600)), None, FB_SCOPES)

    def refresh(self, client, tokens: TokenSet, s) -> TokenSet:
        if self._variant(s) == "instagram_login":
            r = self.http.get("https://graph.instagram.com/refresh_access_token",
                              params={"grant_type": "ig_refresh_token", "access_token": tokens.access_token})
        else:
            r = self.http.get(f"https://graph.facebook.com/{self._ver(s)}/oauth/access_token",
                              params={"grant_type": "fb_exchange_token", "client_id": client.client_id,
                                      "client_secret": client.client_secret, "fb_exchange_token": tokens.access_token})
        if r.status_code in (400, 401):
            raise PublishError(f"Instagram authorization expired: {r.text[:200]}", reauth=True)
        self.raise_for(r, "Instagram token refresh")
        d = r.json()
        return TokenSet(d["access_token"], None, expires_in(d.get("expires_in", 60 * 24 * 3600)), None, tokens.scope, tokens.extra)

    def account_info(self, tokens: TokenSet, s: dict) -> dict:
        if self._variant(s) == "instagram_login":
            r = self.http.get(f"{self._graph(s)}/me", params={"fields": "user_id,username", "access_token": tokens.access_token})
            self.raise_for(r, "Instagram account lookup")
            d = r.json()
            ig_id = str(d.get("user_id") or tokens.extra.get("ig_user_id"))
            tokens.extra["ig_user_id"] = ig_id
            return {"external_account_id": ig_id, "display_name": "@" + d.get("username", ig_id)}
        r = self.http.get(f"{self._graph(s)}/me/accounts", params={"fields": "name,instagram_business_account{id,username}",
                                                                   "access_token": tokens.access_token})
        self.raise_for(r, "Facebook pages lookup")
        wanted = s.get("ig_user_id")
        for page in r.json().get("data", []):
            ig = page.get("instagram_business_account")
            if ig and (not wanted or ig["id"] == wanted):
                tokens.extra.update({"ig_user_id": ig["id"], "page_id": page.get("id")})
                return {"external_account_id": ig["id"], "display_name": "@" + ig.get("username", ig["id"])}
        raise PublishError("No Instagram professional account is linked to the authorized Facebook Pages")

    # ---------------------------------------------------------------- publishing
    def _ig(self, ctx: PublishContext) -> str:
        ig = ctx.tokens.extra.get("ig_user_id") or ctx.account_settings.get("ig_user_id")
        if not ig:
            raise PublishError("Instagram user id unknown; reconnect the account", reauth=True)
        return ig

    def _status(self, ctx: PublishContext, container: str) -> str:
        r = self.http.get(f"{self._graph(ctx.account_settings)}/{container}",
                          params={"fields": "status_code,status", "access_token": ctx.tokens.access_token})
        self.raise_for(r, "Instagram container status")
        return r.json().get("status_code", "")

    def _publish_container(self, ctx: PublishContext, container: str) -> PublishResult:
        g, ig = self._graph(ctx.account_settings), self._ig(ctx)
        r = self.http.post(f"{g}/{ig}/media_publish", data={"creation_id": container, "access_token": ctx.tokens.access_token})
        self.raise_for(r, "Instagram media_publish")
        media_id = r.json()["id"]
        ctx.save_state({"media_id": media_id})
        url = None
        p = self.http.get(f"{g}/{media_id}", params={"fields": "permalink", "access_token": ctx.tokens.access_token})
        if p.status_code == 200:
            url = p.json().get("permalink")
        return PublishResult("published", media_id, url)

    def publish(self, ctx: PublishContext) -> PublishResult:
        s, g, ig, tok = ctx.account_settings, self._graph(ctx.account_settings), self._ig(ctx), ctx.tokens.access_token
        lim = self.http.get(f"{g}/{ig}/content_publishing_limit", params={"fields": "quota_usage,config", "access_token": tok})
        if lim.status_code == 200:
            data = (lim.json().get("data") or [{}])[0]
            total = (data.get("config") or {}).get("quota_total")
            if total and data.get("quota_usage", 0) >= total:
                raise PublishError(f"Instagram publishing limit reached ({data.get('quota_usage')}/{total} in 24h)", retryable=True)
        container = ctx.state.get("container_id")
        resumable = not (self._variant(s) == "instagram_login" or s.get("use_video_url"))
        if container and resumable and not ctx.state.get("uploaded"):
            # Resuming: the upload may have completed even though we never recorded it.
            if self._status(ctx, container) in ("FINISHED", "PUBLISHED"):
                ctx.save_state({"uploaded": True})
        if not container:
            params = {"media_type": "REELS", "caption": ctx.post.caption_with_tags(2200), "share_to_feed": "true", "access_token": tok}
            if not resumable:
                if not ctx.public_url:
                    raise PublishError("Instagram Login requires a public video URL; set AUTOCONTENT_PUBLIC_BASE_URL or use the facebook_login variant")
                params["video_url"] = ctx.public_url()
            else:
                params["upload_type"] = "resumable"
            r = self.http.post(f"{g}/{ig}/media", data=params)
            self.raise_for(r, "Instagram create container")
            d = r.json()
            container = d["id"]
            ctx.save_state({"container_id": container, "upload_uri": d.get("uri"), "uploaded": False})
        # Upload (or resume a failed upload into the same container). Nothing goes live until media_publish, so
        # re-sending the file into an unpublished container cannot create a duplicate post.
        if resumable and not ctx.state.get("uploaded"):
            ctx.progress("Uploading video to Meta")
            size = ctx.video_path.stat().st_size
            uri = ctx.state.get("upload_uri") or f"https://rupload.facebook.com/ig-api-upload/{self._ver(s)}/{container}"
            with open(ctx.video_path, "rb") as fh:
                u = self.http.post(uri, content=fh.read(), headers={"Authorization": f"OAuth {tok}", "offset": "0",
                                                                    "file_size": str(size)})
            self.raise_for(u, "Instagram resumable upload")
            ctx.save_state({"uploaded": True})
        deadline = time.monotonic() + float(s.get("processing_timeout", 600))
        while time.monotonic() < deadline:
            st = self._status(ctx, container)
            if st == "FINISHED":
                return self._publish_container(ctx, container)
            if st == "PUBLISHED":
                return PublishResult("published", ctx.state.get("media_id"), detail="container already published")
            if st in ("ERROR", "EXPIRED"):
                ctx.save_state({"container_id": None, "uploaded": False})
                raise PublishError(f"Instagram processing {st}", retryable=st == "EXPIRED")
            ctx.progress(f"Instagram processing ({st or 'pending'})")
            time.sleep(float(s.get("poll_seconds", 15)))
        raise PublishError("Instagram processing timed out; will verify and retry", retryable=True)

    def verify(self, ctx: PublishContext) -> PublishResult:
        if ctx.state.get("media_id"):
            return PublishResult("published", ctx.state["media_id"])
        container = ctx.state.get("container_id")
        if not container:
            return PublishResult("not_published", detail="no container created")
        st = self._status(ctx, container)
        if st == "PUBLISHED":
            return PublishResult("published", None, detail="container shows PUBLISHED")
        if st == "FINISHED":  # created and processed but never published: safe to publish exactly this container
            return self._publish_container(ctx, container)
        if st in ("ERROR", "EXPIRED"):
            ctx.save_state({"container_id": None, "uploaded": False})
            return PublishResult("not_published", detail=f"container {st}")
        if st == "IN_PROGRESS":  # nothing is live until media_publish succeeds; publish() resumes upload/polling
            return PublishResult("not_published", detail="container still in progress (upload incomplete or processing)")
        return PublishResult("unknown", detail=f"container status {st}")

    def fetch_metrics(self, tokens: TokenSet, external_id: str, s: dict) -> Metrics:
        g = self._graph(s)
        r = self.http.get(f"{g}/{external_id}/insights", params={"metric": "views,likes,comments,shares", "access_token": tokens.access_token})
        vals: dict = {}
        unavailable: dict = {}
        if r.status_code == 200:
            for m in r.json().get("data", []):
                v = (m.get("values") or [{}])[0].get("value")
                vals[m["name"]] = v if v is not None else m.get("total_value", {}).get("value")
        else:
            unavailable["insights"] = f"insights unavailable (HTTP {r.status_code}); needs insights permission"
            f = self.http.get(f"{g}/{external_id}", params={"fields": "like_count,comments_count", "access_token": tokens.access_token})
            if f.status_code == 200:
                d = f.json()
                vals = {"likes": d.get("like_count"), "comments": d.get("comments_count")}
        for k in ("views", "likes", "comments", "shares"):
            if vals.get(k) is None:
                unavailable.setdefault(k, "not returned by Instagram")
        return Metrics(vals.get("views"), vals.get("likes"), vals.get("comments"), vals.get("shares"), unavailable, vals)
