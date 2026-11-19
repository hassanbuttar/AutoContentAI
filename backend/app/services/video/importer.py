"""Import a finished video made elsewhere (another script, an editor, a phone) into the Content Library.

The file is probed, conformed to the platform spec when needed (9:16 1080x1920, H.264/AAC, yuv420p, target fps),
thumbnailed, de-duplicated by SHA-256 and quality-checked. It gets a manual ContentIdea + VideoScript that hold its
captions, so it publishes, schedules and appears in the Library exactly like a generated video."""
from __future__ import annotations

import hashlib
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.paths import to_storage_rel
from app.models import Application, ContentIdea, RenderedVideo, VideoScript
from app.services.settings_service import effective_video_settings
from app.services.video import ffmpeg, qc

FIT_MODES = ("auto", "pad", "crop")
IMPORT_MAX_DURATION = 180.0  # longest clip every supported platform accepts through its API (Shorts / Reels)


class ImportProblem(ValueError):
    pass


class DuplicateVideo(ImportProblem):
    def __init__(self, video_id: int):
        super().__init__(f"This exact file is already in the library (video #{video_id})")
        self.video_id = video_id


def _compliant(pr: ffmpeg.ProbeResult, vs: dict) -> bool:
    return ((pr.width, pr.height) == (int(vs["width"]), int(vs["height"])) and pr.vcodec == "h264" and pr.acodec == "aac"
            and pr.pix_fmt == "yuv420p" and abs(pr.fps - float(vs["fps"])) < 1.0)


def _conform(src: Path, dest: Path, pr: ffmpeg.ProbeResult, vs: dict, fit: str) -> None:
    W, H, FPS, crf = int(vs["width"]), int(vs["height"]), int(vs["fps"]), int(vs.get("crf", 20))
    tail = f"fps={FPS},format=yuv420p,setsar=1"
    if fit == "crop":
        vf = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},{tail}[v]"
    else:  # pad: fit inside, fill the rest with a blurred copy (no black bars)
        vf = (f"[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:4[bg];"
              f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,{tail}[v]")
    args = ["-i", str(src)]
    if pr.acodec is None:  # platforms and QC expect an audio track; add silence
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    args += ["-filter_complex", vf, "-map", "[v]", "-map", "1:a" if pr.acodec is None else "0:a:0",
             "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-profile:v", "high",
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", "-movflags", "+faststart", str(dest)]
    ffmpeg.run(args, timeout=3600)


def import_video(db: Session, app: Application, src: Path, *, original_name: str, title: str, caption: str,
                 hashtags: list[str], youtube_title: str = "", fit: str = "auto") -> RenderedVideo:
    """`src` is a temporary file owned by the caller; it is never modified."""
    if fit not in FIT_MODES:
        raise ImportProblem(f"fit must be one of {', '.join(FIT_MODES)}")
    try:
        pr = ffmpeg.probe(src)
    except Exception as exc:  # noqa: BLE001
        raise ImportProblem("Could not read the file as a video") from exc
    if not pr.width or not pr.height or pr.duration <= 0:
        raise ImportProblem("The file has no readable video stream")
    if pr.duration > IMPORT_MAX_DURATION + 1:
        raise ImportProblem(f"Video is {pr.duration:.0f}s; the limit is {IMPORT_MAX_DURATION:.0f}s (YouTube Shorts / Instagram Reels)")

    vs = effective_video_settings(db, app.video_settings)
    out_dir = get_settings().storage("videos", app.slug)
    final = out_dir / f"{datetime.now():%Y%m%d_%H%M%S}_import_{uuid.uuid4().hex[:6]}.mp4"
    t0 = time.monotonic()
    normalized = not _compliant(pr, vs)
    try:
        if normalized:
            _conform(src, final, pr, vs, "pad" if fit == "auto" else fit)
        else:
            shutil.copyfile(src, final)
        sha = hashlib.sha256(final.read_bytes()).hexdigest()
        dup = db.scalar(select(RenderedVideo.id).where(RenderedVideo.application_id == app.id, RenderedVideo.sha256 == sha))
        if dup:
            raise DuplicateVideo(dup)
        thumb = get_settings().storage("thumbnails", app.slug) / (final.stem + ".jpg")
        ffmpeg.run(["-ss", str(min(1.0, pr.duration / 2)), "-i", str(final), "-frames:v", "1", "-vf", "scale=540:-2",
                    "-q:v", "3", str(thumb)])
        out = ffmpeg.probe(final)
    except Exception:
        final.unlink(missing_ok=True)
        raise

    title = title.strip() or Path(original_name).stem[:200] or "Imported video"
    tags = [t if t.startswith("#") else f"#{t}" for t in (h.strip() for h in hashtags) if t.strip("#")][:30]
    social = {"instagram": {"caption": caption, "hashtags": tags}, "tiktok": {"caption": caption, "hashtags": tags},
              "youtube": {"title": (youtube_title or title)[:100], "description": caption,
                          "tags": [t.lstrip("#") for t in tags]}}
    idea = ContentIdea(application_id=app.id, content_type="imported", topic=title[:500], angle="Imported video",
                       fingerprint=sha, source="manual", status="rendered")
    db.add(idea)
    db.flush()
    script = VideoScript(content_idea_id=idea.id, application_id=app.id, hook=title, script_text="", cta="",
                         language=app.language or "en", duration_seconds=round(out.duration, 2), social_content=social,
                         claim_check={"ok": True, "errors": 0, "warnings": 0, "issues": [], "imported": True},
                         plan_json={"imported": {"original_name": original_name[:300], "source": pr.as_dict(),
                                                 "normalized": normalized, "fit": fit}})
    db.add(script)
    db.flush()
    report = qc.check_video(out, {**vs, "min_duration": 3, "max_duration": IMPORT_MAX_DURATION}, script,
                            captions_enabled=False)
    for c in report["checks"]:
        if c["name"] == "captions":
            c["detail"] = "burned-in captions are up to the source video"
        elif c["name"] == "claims":
            c["detail"], c["severity"] = "not checked: imported video, review the caption yourself", "info"
    video = RenderedVideo(application_id=app.id, content_idea_id=idea.id, video_script_id=script.id,
                          status="ready" if report["ok"] else "failed", review_status="pending",
                          path=to_storage_rel(final), thumbnail_path=to_storage_rel(thumb), width=out.width,
                          height=out.height, fps=out.fps, duration=round(out.duration, 2), size_bytes=out.size_bytes,
                          sha256=sha, renderer="imported+conformed" if normalized else "imported", qc_report=report,
                          render_seconds=round(time.monotonic() - t0, 1),
                          error=None if report["ok"] else "Failed QC: " + ", ".join(report["flags"]))
    db.add(video)
    db.flush()
    return video
