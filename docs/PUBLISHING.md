# Social publishing setup

AutoContent AI publishes only through official APIs and OAuth. It never asks for or stores social
media passwords, and it never automates a browser. Where direct publishing isn't available, it
falls back to **manual export**: the MP4 plus caption files land in `storage/exports/...`, and you
mark the post as published when you upload it yourself.

The endpoint details below were checked against the official documentation in November 2026.
**None of the three integrations has been exercised against a live account from this build.** They
are implemented to the documented contracts and tested with mocked HTTP. Before you rely on
automatic mode, do one supervised "Publish now" per platform.

## Redirect URIs

Register `<base>/api/oauth/callback/<platform>` with each provider. `<base>` is
`http://127.0.0.1:8000` by default; change it under Settings → Social platforms → Redirect base URL.
Meta and TikTok require **HTTPS** redirect URIs, so for those you need a tunnel URL (see
`docs/OPERATIONS.md`, Mode B). Google accepts `http://127.0.0.1` for desktop/web clients in testing.

## YouTube Shorts (YouTube Data API v3)

1. In Google Cloud Console, create a project, enable **YouTube Data API v3**, and create an **OAuth client
   (Web application)** with the redirect URI above. Add yourself as a test user on the consent screen.
2. Settings → Social platforms → YouTube: paste the Client ID and save, then paste the Client secret.
3. Social Accounts → Add account (YouTube) → **Connect**.

| Item | Value |
|---|---|
| Scopes | `youtube.upload`, `youtube.readonly` |
| Upload | `POST https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status`, then `PUT` to the session URL |
| Shorts | vertical video ≤ 3 min; `#Shorts` is appended to the description (configurable) |
| AI disclosure | `status.containsSyntheticMedia=true` when ComfyUI visuals were used |
| Quota | videos.insert has its own upload quota bucket; check your project quota |
| Metrics | views, likes, comments (`videos.list?part=statistics`); shares are not exposed |
| Crash safety | the upload session URL is stored before the upload. Verification asks Google for the upload status (`Content-Range: bytes */size`) |

Google's current docs state that uploads from unverified API projects are *not* restricted to private
visibility. Your consent screen may still be in "testing", which limits who can authorize.

## Instagram Reels (Instagram Platform API)

Requirements: an Instagram **professional** (business or creator) account, and a Meta app with the
content publishing permission. App Review is needed before accounts other than the app's own roles can use it.

Two variants (Settings → Social platforms → Instagram → Login variant):

| Variant | Host | Upload | Notes |
|---|---|---|---|
| `facebook_login` (default) | graph.facebook.com | **resumable upload of the local file** to `rupload.facebook.com/ig-api-upload/<ver>/<container>` | The IG account must be linked to a Facebook Page. Scopes: `instagram_basic, instagram_content_publish, pages_show_list, pages_read_engagement, business_management` |
| `instagram_login` | graph.instagram.com | Meta **downloads** the video from a public `video_url` | Requires `AUTOCONTENT_PUBLIC_BASE_URL` (tunnel). The app serves `/media/public/<signed-token>.mp4`, valid for 24 h, approved videos only |

Flow: `POST /<IG_ID>/media` (media_type=REELS) → upload → poll `status_code` until `FINISHED` →
`POST /<IG_ID>/media_publish`. The publishing quota is checked first via `content_publishing_limit`
(Meta documents 100 API posts per rolling 24 h; one section of its docs says 50).
**Crash safety:** the container ID is stored before publishing. A `FINISHED` container is published
exactly once; a `PUBLISHED` container is recorded as done.

## TikTok (Content Posting API)

1. Create an app at developers.tiktok.com, then add **Login Kit** and **Content Posting API** (Direct Post).
2. Register the HTTPS redirect URI. Settings → TikTok: Client key and Client secret.

| Item | Value |
|---|---|
| Scopes | `user.info.basic, video.publish, video.upload` (+ `video.list` for metrics) |
| Direct post | `creator_info/query` → `POST /v2/post/publish/video/init/` (FILE_UPLOAD) → chunked `PUT` → `status/fetch` |
| Inbox mode | account setting `post_mode=inbox`: a draft goes to the TikTok app and the creator finishes posting there (shown as *exported*) |
| Chunks | ≥ 5 MB, ≤ 64 MB, final chunk may absorb the remainder (≤ 128 MB); files < 5 MB are sent whole |
| **Unaudited apps** | content is restricted to private viewing (`SELF_ONLY`). Public posting requires TikTok's audit |
| Tokens | access token lasts 2 h, refresh token 180 d and **rotates** on every refresh (the new one is stored) |
| Limits | 6 init requests/min per user token; daily per-creator caps (`spam_risk_too_many_posts`) |
| AI disclosure | `is_aigc=true` when ComfyUI visuals were used |

## Manual export

Any account can be switched to **Export only** (account settings), and the `export` platform is
always available. Exports are written to `storage/exports/<platform>/<id>_<slot>/` as
`video.mp4`, `caption.txt` and `metadata.json`. Use **Mark published** in the queue when you post it.

## Duplicate-post protection (summary)
- One publication per `(rule, slot)` and one publish job per publication (unique keys).
- `approved → publishing` is a database compare-and-set.
- Platform IDs are persisted before the irreversible step. After a crash, `verify()` decides. An
  unknowable outcome becomes `verify_required` and waits for you ("It is live" / "Not live").
