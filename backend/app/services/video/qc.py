"""Technical and content quality checks for rendered videos."""
from __future__ import annotations

from app.models import VideoScript
from app.services.video.ffmpeg import ProbeResult

MAX_BYTES = 250 * 1024 * 1024


def _description_check(script: VideoScript, caption_required: bool) -> dict:
    sc = script.social_content or {}
    has_desc = any([(sc.get("instagram") or {}).get("caption"), (sc.get("tiktok") or {}).get("caption"),
                    (sc.get("youtube") or {}).get("description")])
    if has_desc:
        detail = "social captions present"
    elif caption_required:
        detail = "all captions are empty"
    else:
        detail = "no caption yet: add one in the Captions tab before publishing"
    return {"name": "description", "ok": has_desc, "detail": detail, "severity": "error" if caption_required else "warning"}


def summarize(checks: list[dict]) -> dict:
    """`ok` = no hard failures (claims are reviewed separately); `auto_publish_ok` = nothing flagged at all."""
    hard_fail = [c for c in checks if not c["ok"] and c["severity"] == "error" and c["name"] not in ("claims",)]
    review_flags = [c["name"] for c in checks if not c["ok"]]
    return {"ok": not hard_fail, "auto_publish_ok": not review_flags, "checks": checks, "flags": review_flags}


def refresh_caption_check(report: dict, script: VideoScript, *, caption_required: bool = True) -> dict:
    """Recompute only the caption ("description") check of an existing report, e.g. after captions are edited."""
    new = _description_check(script, caption_required)
    checks = [new if c.get("name") == "description" else dict(c) for c in report.get("checks", [])]
    if not any(c.get("name") == "description" for c in checks):
        checks.append(new)
    return {**report, **summarize(checks)}


def check_video(pr: ProbeResult, vs: dict, script: VideoScript | None, *, captions_enabled: bool = True,
                duplicate_of: int | None = None, caption_required: bool = True) -> dict:
    """`caption_required=False` (imports): an empty social caption is a warning to fix before publishing,
    not a failed video, because captions can be added afterwards."""
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, severity: str = "error"):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "severity": severity})

    W, H, FPS = int(vs["width"]), int(vs["height"]), int(vs["fps"])
    add("media_present", pr.size_bytes > 10_000 and pr.duration > 0, f"{pr.size_bytes} bytes, {pr.duration:.1f}s")
    add("dimensions", (pr.width, pr.height) == (W, H), f"{pr.width}x{pr.height} (expected {W}x{H})")
    add("aspect_9_16", pr.height > 0 and abs(pr.width / pr.height - 9 / 16) < 0.01, f"ratio {pr.width}:{pr.height}")
    add("fps", abs(pr.fps - FPS) < 1.0, f"{pr.fps} fps (expected {FPS})")
    lo, hi = float(vs["min_duration"]), float(vs["max_duration"])
    add("duration", lo - 3 <= pr.duration <= hi + 5, f"{pr.duration:.1f}s (allowed {lo:.0f}-{hi:.0f}s)")
    add("video_codec", pr.vcodec == "h264", f"video codec {pr.vcodec}")
    add("audio_codec", pr.acodec == "aac", f"audio codec {pr.acodec}")
    add("pixel_format", pr.pix_fmt in ("yuv420p", ""), f"pixel format {pr.pix_fmt or 'unknown'}", "warning")
    add("file_size", pr.size_bytes <= MAX_BYTES, f"{pr.size_bytes / 1e6:.1f} MB (max {MAX_BYTES / 1e6:.0f} MB)")
    if script is not None:
        add("captions", (not captions_enabled) or bool(script.script_text.strip()), "captions generated from narration")
        checks.append(_description_check(script, caption_required))
        cc = script.claim_check or {}
        add("claims", cc.get("ok", True), f"{cc.get('errors', 0)} claim errors, {cc.get('warnings', 0)} warnings",
            "error" if not cc.get("ok", True) else "info")
        too_similar = any(i.get("type") == "too_similar" for i in cc.get("issues", []))
        add("originality", not too_similar, "similar to a recent video" if too_similar else "unique", "warning")
    add("duplicate", duplicate_of is None, f"identical to video #{duplicate_of}" if duplicate_of else "unique file")
    return summarize(checks)
