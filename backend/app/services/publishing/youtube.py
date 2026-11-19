"""YouTube Shorts via YouTube Data API v3 (videos.insert, resumable upload).

Verified against developers.google.com/youtube/v3/docs/videos/insert (Nov 2026): scopes youtube.upload,
POST https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status,
status.containsSyntheticMedia settable. Shorts = vertical video up to 3 minutes."""
from __future__ import annotations

from urllib.parse import urlencode

from app.services.publishing.base import (Metrics, OAuthClient, PlatformAdapter, PostContent, PublishContext,
                                          PublishError, PublishResult, TokenSet, expires_in)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"


class YouTubeAdapter(PlatformAdapter):
    platform = "youtube"
    display_name = "YouTube Shorts"
    max_duration = 180.0

    def capabilities(self, s: dict) -> dict:
        return {"direct_publish": True, "oauth": True, "metrics": ["views", "likes", "comments"],
                "notes": ["Requires a Google Cloud project with YouTube Data API v3 enabled and an OAuth client.",
                          "videos.insert has its own daily quota; check the Google Cloud console.",
                          "Videos with ComfyUI visuals are flagged containsSyntheticMedia=true."]}

    def authorization_url(self, client: OAuthClient, redirect_uri: str, state: str, code_challenge: str, settings: dict) -> str:
        return AUTH_URL + "?" + urlencode({"client_id": client.client_id, "redirect_uri": redirect_uri, "response_type": "code",
                                           "scope": SCOPES, "access_type": "offline", "prompt": "consent", "state": state,
                                           "code_challenge": code_challenge, "code_challenge_method": "S256",
                                           "include_granted_scopes": "true"})

    def exchange_code(self, client, code, redirect_uri, code_verifier, settings) -> TokenSet:
        r = self.http.post(TOKEN_URL, data={"code": code, "client_id": client.client_id, "client_secret": client.client_secret,
                                            "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": code_verifier})
        self.raise_for(r, "Google token exchange")
        d = r.json()
        return TokenSet(d["access_token"], d.get("refresh_token"), expires_in(d.get("expires_in")), None, d.get("scope", ""))

    def refresh(self, client, tokens: TokenSet, settings) -> TokenSet:
        if not tokens.refresh_token:
            raise PublishError("No refresh token; reconnect YouTube", reauth=True)
        r = self.http.post(TOKEN_URL, data={"client_id": client.client_id, "client_secret": client.client_secret,
                                            "refresh_token": tokens.refresh_token, "grant_type": "refresh_token"})
        if r.status_code in (400, 401):
            raise PublishError(f"YouTube authorization expired or revoked: {r.text[:200]}", reauth=True)
        self.raise_for(r, "Google token refresh")
        d = r.json()
        return TokenSet(d["access_token"], d.get("refresh_token") or tokens.refresh_token, expires_in(d.get("expires_in")),
                        None, d.get("scope", tokens.scope), tokens.extra)

    def account_info(self, tokens: TokenSet, settings: dict) -> dict:
        r = self.http.get(f"{API}/channels", params={"part": "snippet", "mine": "true"}, headers=self._h(tokens))
        self.raise_for(r, "YouTube channel lookup")
        items = r.json().get("items", [])
        if not items:
            raise PublishError("This Google account has no YouTube channel")
        return {"external_account_id": items[0]["id"], "display_name": items[0]["snippet"]["title"]}

    @staticmethod
    def _h(tokens: TokenSet) -> dict:
        return {"Authorization": f"Bearer {tokens.access_token}"}

    def _metadata(self, post: PostContent, s: dict) -> dict:
        title = (post.title or post.caption.split("\n")[0])[:100] or "New video"
        desc = post.description or post.caption
        if s.get("add_shorts_tag", True) and "#shorts" not in (title + desc).lower():
            desc = (desc + "\n\n#Shorts").strip()
        return {"snippet": {"title": title, "description": desc[:5000], "tags": [t.lstrip("#") for t in post.tags][:30],
                            "categoryId": str(s.get("category_id", "28"))},
                "status": {"privacyStatus": s.get("privacy", "public"), "selfDeclaredMadeForKids": False,
                           "containsSyntheticMedia": bool(post.ai_generated_visuals)}}

    def publish(self, ctx: PublishContext) -> PublishResult:
        size = ctx.video_path.stat().st_size
        session = ctx.state.get("upload_session")
        if not session:
            r = self.http.post(UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"},
                               json=self._metadata(ctx.post, ctx.account_settings),
                               headers={**self._h(ctx.tokens), "X-Upload-Content-Type": "video/mp4",
                                        "X-Upload-Content-Length": str(size)})
            self.raise_for(r, "YouTube upload session")
            session = r.headers.get("Location")
            if not session:
                raise PublishError("YouTube did not return an upload session URL", retryable=True)
            ctx.save_state({"upload_session": session})
        ctx.progress("Uploading to YouTube")
        with open(ctx.video_path, "rb") as fh:
            r = self.http.put(session, content=fh.read(), headers={**self._h(ctx.tokens), "Content-Type": "video/mp4"})
        if r.status_code in (200, 201):
            vid = r.json()["id"]
            ctx.save_state({"video_id": vid})
            return PublishResult("published", vid, f"https://www.youtube.com/shorts/{vid}")
        if r.status_code == 404:  # session expired
            ctx.save_state({"upload_session": None})
        self.raise_for(r, "YouTube upload")
        raise PublishError(f"Unexpected YouTube upload response {r.status_code}", retryable=True)

    def verify(self, ctx: PublishContext) -> PublishResult:
        vid = ctx.state.get("video_id")
        if vid:
            r = self.http.get(f"{API}/videos", params={"part": "status", "id": vid}, headers=self._h(ctx.tokens))
            self.raise_for(r, "YouTube verify")
            if r.json().get("items"):
                return PublishResult("published", vid, f"https://www.youtube.com/shorts/{vid}")
            return PublishResult("not_published", detail="video id not found")
        session = ctx.state.get("upload_session")
        if not session:
            return PublishResult("not_published", detail="no upload was started")
        size = ctx.video_path.stat().st_size
        r = self.http.put(session, headers={**self._h(ctx.tokens), "Content-Range": f"bytes */{size}"})
        if r.status_code in (200, 201):
            vid = r.json()["id"]
            ctx.save_state({"video_id": vid})
            return PublishResult("published", vid, f"https://www.youtube.com/shorts/{vid}")
        if r.status_code in (308, 404, 410):  # incomplete or expired: nothing was published
            ctx.save_state({"upload_session": None if r.status_code != 308 else session})
            return PublishResult("not_published", detail=f"upload incomplete ({r.status_code})")
        return PublishResult("unknown", detail=f"status {r.status_code}")

    def fetch_metrics(self, tokens: TokenSet, external_id: str, settings: dict) -> Metrics:
        r = self.http.get(f"{API}/videos", params={"part": "statistics", "id": external_id}, headers=self._h(tokens))
        self.raise_for(r, "YouTube statistics")
        items = r.json().get("items", [])
        if not items:
            return Metrics(unavailable={"all": "video not found"})
        st = items[0].get("statistics", {})

        def i(k):
            return int(st[k]) if k in st else None
        return Metrics(views=i("viewCount"), likes=i("likeCount"), comments=i("commentCount"), shares=None,
                       unavailable={"shares": "not exposed by YouTube Data API", **({} if "likeCount" in st else {"likes": "hidden by owner"})},
                       raw=st)
