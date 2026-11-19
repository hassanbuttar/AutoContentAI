"""Storyboard -> finished vertical MP4.

Per scene: visual (ComfyUI image/video | product screenshot composition | branded title card)
-> Ken Burns motion -> narration (local TTS) -> timed captions + overlay text + logo -> H.264/AAC clip.
Then: concat, optional intro/outro + licensed music, thumbnail, probe, QC.
ComfyUI failures degrade per scene to local fallbacks with an actionable log entry."""
from __future__ import annotations

import hashlib
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.paths import storage_path, to_storage_rel
from app.models import (Application, ApplicationAsset, ComfyWorkflow, GeneratedAsset, RenderedVideo, Storyboard,
                        VideoScript, utcnow)
from app.services.settings_service import effective_video_settings, get_section
from app.services.video import ffmpeg, graphics, qc, tts
from app.services.video.comfyui import ComfyClient, ComfyError

log = logging.getLogger(__name__)
FADE = 0.25


@dataclass
class RenderHooks:
    progress: Callable[[float, str], None] = lambda f, m: None
    log: Callable[..., None] = lambda m, lvl="info": None
    cancelled: Callable[[], bool] = lambda: False


@dataclass
class SceneMedia:
    number: int
    duration: float
    frame: Path | None = None  # still image (2x resolution) for Ken Burns
    clip: Path | None = None  # source video
    audio: Path | None = None
    source: str = "title_card"
    gpu_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)


def _motion_expr(motion: str, frames: int) -> tuple[str, str, str]:
    n = max(1, frames - 1)
    center_x, center_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "zoom_out":
        return f"1.12-0.12*on/{n}", center_x, center_y
    if motion == "pan_left":
        return "1.10", f"(iw-iw/zoom)*(1-on/{n})", center_y
    if motion == "pan_right":
        return "1.10", f"(iw-iw/zoom)*on/{n}", center_y
    if motion == "static":
        return "1.0", "0", "0"
    return f"1+0.12*on/{n}", center_x, center_y


def pick_workflow(db: Session, kind: str, app_id: int, content_type: str | None) -> ComfyWorkflow | None:
    rows = list(db.scalars(select(ComfyWorkflow).where(ComfyWorkflow.kind == kind, ComfyWorkflow.enabled.is_(True))))
    if not rows:
        return None

    def score(w: ComfyWorkflow) -> tuple:
        return (w.application_id == app_id, w.content_type == content_type, w.is_default, w.validated, -w.id)

    rows = [w for w in rows if w.application_id in (None, app_id)]
    return max(rows, key=score) if rows else None


class Renderer:
    def __init__(self, db: Session, hooks: RenderHooks | None = None, comfy: ComfyClient | None = None):
        self.db = db
        self.h = hooks or RenderHooks()
        self.comfy_cfg = get_section(db, "comfyui")
        self._comfy = comfy
        self._comfy_down: str | None = None

    # -------------------------------------------------------------- comfy helpers
    def comfy(self) -> ComfyClient | None:
        if self._comfy_down or not self.comfy_cfg.get("enabled"):
            return None
        if self._comfy is None:
            self._comfy = ComfyClient(self.comfy_cfg["url"])
        try:
            self._comfy.system_stats()
        except ComfyError as exc:
            self._comfy_down = f"{exc} {exc.actionable}"
            self.h.log(f"ComfyUI unavailable, using local fallback renderer: {self._comfy_down}", "warning")
            return None
        return self._comfy

    def _comfy_image(self, wf: ComfyWorkflow, prompt: str, out_dir: Path, prefix: str, scene_no: int) -> tuple[Path, float, str]:
        c = self.comfy()
        if c is None:
            raise ComfyError(self._comfy_down or "ComfyUI disabled")
        values = {"positive_prompt": prompt, "negative_prompt": (wf.defaults or {}).get("negative_prompt"),
                  "filename_prefix": f"autocontent/{prefix}", **{k: v for k, v in (wf.defaults or {}).items()
                                                                  if k in ("width", "height", "steps", "length", "fps")}}
        res = c.run(wf.workflow, wf.mappings, values, out_dir, prefix, timeout=float(wf.timeout_seconds or self.comfy_cfg["timeout_seconds"]),
                    on_progress=lambda f, m: self.h.progress(-1, f"Scene {scene_no}: {m}"), should_cancel=self.h.cancelled)
        return res.files[0], res.seconds, res.prompt_id

    def _comfy_animate(self, wf: ComfyWorkflow, image: Path, prompt: str, out_dir: Path, prefix: str, scene_no: int) -> tuple[Path, float]:
        c = self.comfy()
        if c is None:
            raise ComfyError(self._comfy_down or "ComfyUI disabled")
        name = c.upload_image(image)
        values = {"input_image": name, "positive_prompt": prompt or "subtle camera motion, cinematic",
                  "negative_prompt": (wf.defaults or {}).get("negative_prompt"), "filename_prefix": f"autocontent/{prefix}",
                  **{k: v for k, v in (wf.defaults or {}).items() if k in ("width", "height", "steps", "length", "fps")}}
        res = c.run(wf.workflow, wf.mappings, values, out_dir, prefix, timeout=float(wf.timeout_seconds or self.comfy_cfg["timeout_seconds"]),
                    on_progress=lambda f, m: self.h.progress(-1, f"Scene {scene_no} animate: {m}"), should_cancel=self.h.cancelled)
        vids = [f for f in res.files if f.suffix.lower() in (".mp4", ".webm", ".mov", ".gif", ".webp", ".mkv")]
        if not vids:
            raise ComfyError("img2vid workflow returned no video file")
        return vids[0], res.seconds

    # -------------------------------------------------------------- main
    def render(self, storyboard_id: int, *, regenerate_scenes: set[int] | None = None, video: RenderedVideo | None = None,
               job_id: int | None = None) -> RenderedVideo:
        t0 = time.monotonic()
        db = self.db
        sb = db.get(Storyboard, storyboard_id)
        script = db.get(VideoScript, sb.video_script_id)
        app = db.get(Application, script.application_id)
        vs = effective_video_settings(db, app.video_settings)
        W, H, FPS = int(vs["width"]), int(vs["height"]), int(vs["fps"])
        if video is None:
            video = RenderedVideo(application_id=app.id, content_idea_id=script.content_idea_id, video_script_id=script.id,
                                  storyboard_id=sb.id, status="rendering", render_job_id=job_id)
            db.add(video)
            db.flush()
        else:
            video.status, video.error = "rendering", None
        db.commit()
        settings = get_settings()
        work = settings.storage("work", f"render_{video.id}_{int(time.time())}")
        assets_dir = settings.storage("generated_assets", app.slug, f"sb{sb.id}")
        try:
            result = self._render_inner(app, script, sb, video, vs, W, H, FPS, work, assets_dir, regenerate_scenes or set())
        except Exception as exc:
            video.status, video.error = "failed", f"{type(exc).__name__}: {exc}"[:2000]
            db.commit()
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)
        video.render_seconds = round(time.monotonic() - t0, 1)
        db.commit()
        return result

    def _scene_visual(self, app: Application, sb: Storyboard, scene: dict, vs: dict, W: int, H: int, assets_dir: Path,
                      regenerate: bool, comfy_budget: list[int], content_type: str) -> SceneMedia:
        db = self.db
        n = scene["scene_number"]
        sm = SceneMedia(number=n, duration=float(scene["duration_seconds"]))
        src = scene.get("visual_source", "title_card")
        frame_path = assets_dir / f"scene{n:02d}_frame.png"
        logo = self._asset_path(app.id, "logo")
        accent, _ = graphics.brand_pair(app.brand_colors)
        img: Image.Image | None = None

        if src == "application_screenshot" and scene.get("screenshot_asset_id"):
            asset = db.get(ApplicationAsset, int(scene["screenshot_asset_id"]))
            if asset and asset.application_id == app.id:
                p = storage_path(asset.path)
                if asset.kind == "demo_video" or p.suffix.lower() in (".mp4", ".mov", ".webm"):
                    sm.clip, sm.source = p, "demo_video"
                    return sm
                img = graphics.compose_screenshot(p, W * 2, H * 2, accent)
                sm.source = "screenshot"
            else:
                sm.notes.append("screenshot asset missing; using title card")
        if src == "comfyui" and img is None:
            wf = pick_workflow(db, "txt2img", app.id, content_type)
            prev = db.scalar(select(GeneratedAsset).where(GeneratedAsset.storyboard_id == sb.id, GeneratedAsset.scene_number == n,
                                                          GeneratedAsset.source == "comfyui", GeneratedAsset.kind == "image",
                                                          GeneratedAsset.status == "ready", GeneratedAsset.prompt == scene.get("visual_prompt", ""))
                             .order_by(GeneratedAsset.id.desc()).limit(1))
            if prev and not regenerate and storage_path(prev.path).exists():
                img = graphics.cover(Image.open(storage_path(prev.path)), W * 2, H * 2)
                sm.source = "comfyui (reused)"
            elif wf is None:
                sm.notes.append("no enabled txt2img ComfyUI workflow; using fallback")
            elif comfy_budget[0] <= 0:
                sm.notes.append("ComfyUI scene limit per video reached; using fallback")
            else:
                try:
                    self.h.progress(-1, f"Scene {n}: generating image in ComfyUI")
                    out, secs, pid = self._comfy_image(wf, scene.get("visual_prompt") or scene.get("overlay_text") or app.name,
                                                       assets_dir, f"scene{n:02d}_{int(time.time())}", n)
                    comfy_budget[0] -= 1
                    sm.gpu_seconds += secs
                    db.add(GeneratedAsset(application_id=app.id, storyboard_id=sb.id, scene_number=n, kind="image", source="comfyui",
                                          path=to_storage_rel(out), workflow_id=wf.id, prompt=scene.get("visual_prompt", ""),
                                          comfy_prompt_id=pid, gpu_seconds=secs))
                    db.commit()
                    if out.suffix.lower() in (".mp4", ".webm", ".mov", ".mkv", ".gif", ".webp") and wf.output_type == "video":
                        sm.clip, sm.source = out, "comfyui-video"
                        return sm
                    img = graphics.cover(Image.open(out), W * 2, H * 2)
                    sm.source = "comfyui"
                    if self.comfy_cfg.get("use_img2vid"):
                        vwf = pick_workflow(db, "img2vid", app.id, content_type)
                        if vwf:
                            try:
                                clip, vsecs = self._comfy_animate(vwf, out, scene.get("visual_prompt", ""), assets_dir, f"scene{n:02d}_anim", n)
                                sm.gpu_seconds += vsecs
                                db.add(GeneratedAsset(application_id=app.id, storyboard_id=sb.id, scene_number=n, kind="video",
                                                      source="comfyui", path=to_storage_rel(clip), workflow_id=vwf.id,
                                                      prompt=scene.get("visual_prompt", ""), gpu_seconds=vsecs))
                                db.commit()
                                sm.clip, sm.source = clip, "comfyui-img2vid"
                                return sm
                            except ComfyError as exc:
                                sm.notes.append(f"img2vid failed ({exc}); using still with motion")
                except ComfyError as exc:
                    sm.notes.append(f"ComfyUI failed: {exc} {exc.actionable}".strip())
                    db.add(GeneratedAsset(application_id=app.id, storyboard_id=sb.id, scene_number=n, kind="image", source="comfyui",
                                          path="", workflow_id=wf.id, prompt=scene.get("visual_prompt", ""), status="failed", error=str(exc)[:1000]))
                    db.commit()
        if img is None:
            # Fallback order: a product screenshot (if any) else a branded title card
            shot = self._asset_path(app.id, "screenshot", rotate=n)
            if src == "comfyui" and shot:
                img = graphics.compose_screenshot(shot, W * 2, H * 2, accent)
                sm.source = "screenshot (fallback)"
            else:
                is_last = scene.get("_is_last")
                title = scene.get("overlay_text") or scene.get("narration", "")[:60] or app.name
                sub = (app.website_url or "") if is_last else ""
                img = graphics.title_card(W * 2, H * 2, app.brand_colors, title, sub, logo if (is_last or n == 1) else None)
                sm.source = "title_card"
                scene["_text_baked"] = True
        img.save(frame_path, optimize=False)
        sm.frame = frame_path
        db.add(GeneratedAsset(application_id=app.id, storyboard_id=sb.id, scene_number=n, kind="frame",
                              source=sm.source.split(" ")[0], path=to_storage_rel(frame_path), prompt=scene.get("visual_prompt", "")))
        return sm

    def _asset_path(self, app_id: int, kind: str, rotate: int = 0) -> Path | None:
        rows = list(self.db.scalars(select(ApplicationAsset).where(ApplicationAsset.application_id == app_id,
                                                                   ApplicationAsset.kind == kind).order_by(ApplicationAsset.id)))
        if not rows:
            return None
        p = storage_path(rows[rotate % len(rows)].path)
        return p if p.exists() else None

    def _render_inner(self, app, script, sb, video, vs, W, H, FPS, work: Path, assets_dir: Path, regen: set[int]) -> RenderedVideo:
        db = self.db
        scenes = [dict(s) for s in sb.scenes]
        if not scenes:
            raise ValueError("Storyboard has no scenes")
        scenes[-1]["_is_last"] = True
        comfy_budget = [int(self.comfy_cfg.get("max_comfy_scenes_per_video", 4))]
        content_type = (script.plan_json or {}).get("content_type")
        cap = vs["caption_style"]
        logo_src = self._asset_path(app.id, "logo")
        logo_png = None
        if logo_src and vs.get("logo_position", "none") != "none":
            logo_png = graphics.logo_png(logo_src, work / "logo.png", int(W * 0.16))
        clips: list[Path] = []
        total_gpu = 0.0
        renderer_parts = set()
        n_sc = len(scenes)
        for idx, scene in enumerate(scenes):
            if self.h.cancelled():
                raise RuntimeError("Render cancelled")
            n = scene["scene_number"]
            self.h.progress(0.05 + 0.75 * idx / n_sc, f"Scene {n}/{n_sc}: visuals")
            sm = self._scene_visual(app, sb, scene, vs, W, H, assets_dir, n in regen, comfy_budget, content_type)
            for note in sm.notes:
                self.h.log(f"Scene {n}: {note}", "warning")
            total_gpu += sm.gpu_seconds
            renderer_parts.add(sm.source.split(" ")[0])
            # narration
            narration = (scene.get("narration") or "").strip()
            wav = tts.synthesize(narration, work / f"scene{n:02d}.wav", vs["voice"]) if narration else None
            dur = sm.duration
            if wav:
                dur = max(dur, tts.wav_duration(wav) + 0.45)
                renderer_parts.add("tts")
            dur = round(min(max(dur, 1.5), 20.0), 2)
            sm.duration, sm.audio = dur, wav
            clips.append(self._scene_clip(sm, scene, cap, vs, W, H, FPS, work, logo_png))
        self.h.progress(0.82, "Assembling video")
        joined = work / "joined.mp4"
        (work / "list.txt").write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
        ffmpeg.run(["-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "-movflags", "+faststart", joined.name], cwd=work)
        current = joined
        # intro/outro (user-supplied assets) are normalized then concatenated
        intro, outro = self._asset_path(app.id, "intro"), self._asset_path(app.id, "outro")
        if intro or outro:
            parts = []
            for i, p in enumerate([intro, current, outro]):
                if p is None:
                    continue
                if p == current:
                    parts.append(current)
                else:
                    norm = work / f"bookend{i}.mp4"
                    self._normalize_clip(p, norm, W, H, FPS)
                    parts.append(norm)
            (work / "list2.txt").write_text("".join(f"file '{c.name}'\n" for c in parts), encoding="utf-8")
            ffmpeg.run(["-f", "concat", "-safe", "0", "-i", "list2.txt", "-c", "copy", "withbookends.mp4"], cwd=work)
            current = work / "withbookends.mp4"
        music = self._licensed_music(app.id)
        if music:
            self.h.progress(0.88, "Mixing background music")
            total = ffmpeg.probe(current).duration
            vol = float(vs.get("music_volume", 0.12))
            fade_st = max(0.0, total - 1.5)
            ffmpeg.run(["-i", current.name, "-stream_loop", "-1", "-i", str(music), "-filter_complex",
                        f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={vol},atrim=0:{total:.2f},"
                        f"afade=t=out:st={fade_st:.2f}:d=1.5[m];[0:a][m]amix=inputs=2:duration=first:normalize=0[a]",
                        "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.2f}",
                        "-movflags", "+faststart", "withmusic.mp4"], cwd=work)
            current = work / "withmusic.mp4"
            renderer_parts.add("music")
        self.h.progress(0.93, "Validating output")
        out_dir = get_settings().storage("videos", app.slug)
        final = out_dir / f"{datetime.now():%Y%m%d_%H%M%S}_v{video.id}.mp4"
        shutil.move(str(current), final)
        thumb = get_settings().storage("thumbnails", app.slug) / (final.stem + ".jpg")
        ffmpeg.run(["-ss", "1.0", "-i", str(final), "-frames:v", "1", "-vf", "scale=540:-2", "-q:v", "3", str(thumb)])
        pr = ffmpeg.probe(final)
        sha = hashlib.sha256(final.read_bytes()).hexdigest()
        dup = db.scalar(select(RenderedVideo).where(RenderedVideo.application_id == app.id, RenderedVideo.sha256 == sha,
                                                    RenderedVideo.id != video.id))
        report = qc.check_video(pr, vs, script, captions_enabled=cap.get("enabled", True), duplicate_of=dup.id if dup else None)
        video.path, video.thumbnail_path = to_storage_rel(final), to_storage_rel(thumb)
        video.width, video.height, video.fps, video.duration, video.size_bytes = pr.width, pr.height, pr.fps, round(pr.duration, 2), pr.size_bytes
        video.sha256 = None if dup else sha
        video.renderer = "+".join(sorted(renderer_parts))
        video.gpu_seconds = round(total_gpu, 1)
        video.qc_report = report
        video.status = "ready" if report["ok"] else "failed"
        video.error = None if report["ok"] else "QC failed: " + "; ".join(c["detail"] for c in report["checks"] if not c["ok"])[:1500]
        video.updated_at = utcnow()
        db.commit()
        self.h.progress(1.0, "Render complete")
        return video

    def _scene_clip(self, sm: SceneMedia, scene: dict, cap: dict, vs: dict, W: int, H: int, FPS: int, work: Path,
                    logo_png: Path | None) -> Path:
        n, dur = sm.number, sm.duration
        frames = int(round(dur * FPS))
        inputs: list[str] = []
        if sm.clip is not None:
            inputs += ["-stream_loop", "-1", "-t", f"{dur:.3f}", "-i", str(sm.clip)]
            base = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1,"
                    f"trim=duration={dur:.3f},setpts=PTS-STARTPTS[bg]")
        else:
            inputs += ["-i", str(sm.frame)]
            z, x, y = _motion_expr(scene.get("motion", "zoom_in") if vs.get("ken_burns", True) else "static", frames)
            base = (f"[0:v]scale={W * 2}:{H * 2},zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={W}x{H}:fps={FPS},"
                    f"setsar=1,trim=duration={dur:.3f},setpts=PTS-STARTPTS[bg]")
        filters = [base]
        last = "bg"
        idx = 1
        # overlay text (top third) unless already baked into a title card
        overlay = (scene.get("overlay_text") or "").strip()
        if overlay and not scene.get("_text_baked"):
            p = work / f"s{n}_ov.png"
            graphics.text_png(overlay, p, width=int(W * 0.9), size=int(W * 0.062), color="#FFFFFF",
                              box=(0, 0, 0, 150), max_lines=2)
            inputs += ["-i", str(p)]
            filters.append(f"[{last}][{idx}:v]overlay=x=(W-w)/2:y=H*0.10:enable='between(t,0.2,{dur:.2f})'[v{idx}]")
            last, idx = f"v{idx}", idx + 1
        if logo_png is not None:
            pos = vs.get("logo_position", "top-right")
            m = int(W * 0.04)
            xy = {"top-left": (f"{m}", f"{m}"), "top-right": (f"W-w-{m}", f"{m}"), "bottom-left": (f"{m}", f"H-h-{m}"),
                  "bottom-right": (f"W-w-{m}", f"H-h-{m}")}.get(pos, (f"W-w-{m}", f"{m}"))
            inputs += ["-i", str(logo_png)]
            filters.append(f"[{last}][{idx}:v]overlay=x={xy[0]}:y={xy[1]}[v{idx}]")
            last, idx = f"v{idx}", idx + 1
        narration = (scene.get("narration") or "").strip()
        baked_same = False
        if scene.get("_text_baked"):
            from app.services.content.dedup import tokens

            nt, ot = set(tokens(narration)), set(tokens(scene.get("overlay_text") or ""))
            baked_same = not nt or len(nt - ot) <= max(1, len(nt) // 4)
        if cap.get("enabled", True) and narration and not baked_same:
            ypos = {"lower": "H*0.70", "middle": "(H-h)/2", "bottom": "H*0.80"}.get(cap.get("position", "lower"), "H*0.70")
            for ci, (text, s, e) in enumerate(graphics.caption_chunks(narration, dur)):
                p = work / f"s{n}_c{ci}.png"
                graphics.text_png(text, p, width=int(W * 0.92), size=int(cap.get("font_size", 66)), color=cap.get("color", "#FFFFFF"),
                                  stroke=cap.get("stroke", "#000000"), stroke_w=max(3, int(cap.get("font_size", 66)) // 12), max_lines=2)
                inputs += ["-i", str(p)]
                filters.append(f"[{last}][{idx}:v]overlay=x=(W-w)/2:y={ypos}:enable='between(t,{s:.3f},{e:.3f})'[v{idx}]")
                last, idx = f"v{idx}", idx + 1
        vtail = f"[{last}]format=yuv420p"
        if vs.get("transition", "fade") == "fade":
            vtail += f",fade=t=in:st=0:d={FADE},fade=t=out:st={max(0, dur - FADE):.3f}:d={FADE}"
        filters.append(vtail + "[vout]")
        if sm.audio:
            inputs += ["-i", str(sm.audio)]
            filters.append(f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay=120|120,apad,atrim=0:{dur:.3f},"
                           f"afade=t=out:st={max(0, dur - 0.08):.3f}:d=0.08[aout]")
        else:
            inputs += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            filters.append(f"[{idx}:a]atrim=0:{dur:.3f}[aout]")
        out = work / f"clip{n:02d}.mp4"
        crf = str(vs.get("crf", 20))
        ffmpeg.run([*inputs, "-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", crf, "-pix_fmt", "yuv420p", "-r", str(FPS),
                    "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-t", f"{dur:.3f}", out.name], cwd=work)
        return out

    def _normalize_clip(self, src: Path, out: Path, W: int, H: int, FPS: int) -> None:
        has_audio = bool(ffmpeg.probe(src).acodec)
        vf = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={FPS},setsar=1,format=yuv420p[v]"
        audio = ["-map", "0:a:0"] if has_audio else ["-map", "1:a", "-shortest"]
        ffmpeg.run(["-i", str(src), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-filter_complex", vf,
                    "-map", "[v]", *audio, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-c:a", "aac", "-ar", "48000", "-ac", "2", str(out)])

    def _licensed_music(self, app_id: int) -> Path | None:
        rows = list(self.db.scalars(select(ApplicationAsset).where(ApplicationAsset.application_id == app_id,
                                                                   ApplicationAsset.kind == "music")))
        rows = [r for r in rows if (r.license_note or "").strip()]  # only music with a recorded license
        if not rows:
            return None
        p = storage_path(rows[int(time.time()) % len(rows)].path)
        return p if p.exists() else None
