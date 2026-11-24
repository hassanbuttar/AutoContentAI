"""Per-platform upload limits for API publishing, and which platforms a given video can go to.

A video is no longer rejected because one platform can't take it: QC lists where it *can* be published, and
scheduling/publishing refuse only the platforms whose limits it breaks.

Sources (read 2026-11-24):
- Instagram Reels, IG User Media reference: "File size: 300MB maximum", "Duration: 15 mins maximum, 3 seconds
  minimum", "VBR, 25Mbps maximum", 23-60 FPS.
  https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media
- TikTok Content Posting API, media transfer guide: "Maximum of 4GB"; the init endpoint accepts at most 10 minutes;
  "All TikTok creators can post 3-minute videos, while some have access to post 5-minute or 10-minute videos"
  (the adapter checks the account's creator_info.max_video_post_duration_sec before posting).
  https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide
- YouTube: "256 GB or 12 hours, whichever is less"; "By default, you can upload videos that are up to 15 minutes
  long" (verified accounts longer); vertical videos up to three minutes are categorized as Shorts.
  https://support.google.com/youtube/answer/71673 , https://support.google.com/youtube/answer/15424877
"""
from __future__ import annotations

from dataclasses import dataclass

MB = 1024 * 1024
GB = 1024 * MB


@dataclass(frozen=True)
class PlatformLimits:
    label: str
    max_bytes: int
    max_seconds: float
    min_seconds: float = 0.0
    max_avg_mbps: float | None = None  # average bitrate ceiling (approximates a max-bitrate spec)


LIMITS: dict[str, PlatformLimits] = {
    "instagram": PlatformLimits("Instagram Reels", max_bytes=300 * MB, max_seconds=15 * 60, min_seconds=3,
                                max_avg_mbps=25.5),  # 25 Mbps video + audio
    "tiktok": PlatformLimits("TikTok", max_bytes=4 * GB, max_seconds=10 * 60),
    "youtube": PlatformLimits("YouTube", max_bytes=256 * GB, max_seconds=12 * 3600),
}
TIKTOK_ALL_CREATORS_SECONDS = 180
YOUTUBE_SHORTS_SECONDS = 180
YOUTUBE_UNVERIFIED_SECONDS = 15 * 60
MAX_SECONDS_ANY = max(lim.max_seconds for lim in LIMITS.values())


def _size(b: float) -> str:
    return f"{b / GB:.1f} GB" if b >= GB else f"{b / MB:.0f} MB"


def _dur(s: float) -> str:
    return f"{s / 60:.1f} min" if s >= 90 else f"{s:.0f} s"


def check_platform(platform: str, duration: float | None, size_bytes: int | None) -> dict:
    """{"ok": bool, "problems": [...], "notes": [...]} for one platform. Unknown platforms (export) always fit."""
    lim = LIMITS.get(platform)
    out: dict = {"ok": True, "problems": [], "notes": []}
    if lim is None:
        return out
    d, b = float(duration or 0), int(size_bytes or 0)
    if b > lim.max_bytes:
        out["problems"].append(f"file {_size(b)} is over the {_size(lim.max_bytes)} limit")
    if d > lim.max_seconds:
        out["problems"].append(f"length {_dur(d)} is over the {_dur(lim.max_seconds)} limit")
    if lim.min_seconds and 0 < d < lim.min_seconds:
        out["problems"].append(f"length {d:.1f} s is under the {lim.min_seconds:.0f} s minimum")
    if lim.max_avg_mbps and d > 0 and b * 8 / d / 1e6 > lim.max_avg_mbps:
        out["problems"].append(f"bitrate {b * 8 / d / 1e6:.0f} Mbps is over the 25 Mbps limit")
    if platform == "tiktok" and TIKTOK_ALL_CREATORS_SECONDS < d <= lim.max_seconds:
        out["notes"].append("over 3 min: only if this TikTok account is allowed 5- or 10-minute posts")
    if platform == "youtube":
        if d > YOUTUBE_UNVERIFIED_SECONDS:
            out["notes"].append("over 15 min: needs a verified YouTube account")
        elif d > YOUTUBE_SHORTS_SECONDS:
            out["notes"].append("over 3 min: posted as a regular YouTube video, not a Short")
    out["ok"] = not out["problems"]
    return out


def eligibility(duration: float | None, size_bytes: int | None) -> dict[str, dict]:
    return {p: {"label": lim.label, **check_platform(p, duration, size_bytes)} for p, lim in LIMITS.items()}


def summary(elig: dict[str, dict]) -> tuple[str, str]:
    """(severity, detail) for the QC "platforms" row: info = fits everywhere, notice = passes but only on some
    platforms or with caveats (shown amber), error = no platform accepts it (the video fails QC)."""
    ok = [v["label"] for v in elig.values() if v["ok"]]
    blocked = [f"{v['label']}: {'; '.join(v['problems'])}" for v in elig.values() if not v["ok"]]
    notes = [f"{v['label']}: {'; '.join(v['notes'])}" for v in elig.values() if v["ok"] and v["notes"]]
    if not ok:
        return "error", "No platform accepts this video. " + " | ".join(blocked)
    detail = ("Fits " if not blocked else "Will only be published to ") + ", ".join(ok) + "."
    if blocked:
        detail += " Not published to " + " | ".join(blocked) + "."
    if notes:
        detail += " Note: " + " | ".join(notes) + "."
    return ("notice" if blocked or notes else "info"), detail
