"""FFmpeg discovery, execution and probing."""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)
REQUIRED_FILTERS = ("zoompan", "overlay", "scale", "crop", "fade", "afade", "amix", "apad", "concat")


class FFmpegError(RuntimeError):
    pass


@dataclass
class Binaries:
    ffmpeg: str
    ffprobe: str | None
    version: str
    source: str


def _version(exe: str) -> str:
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, timeout=20).stdout.decode("utf-8", "replace")
        return out.splitlines()[0] if out else "unknown"
    except Exception:
        return "unknown"


def _major(version_line: str) -> int:
    m = re.search(r"version\s+n?(\d+)\.", version_line)
    return int(m.group(1)) if m else 0


@lru_cache(maxsize=4)
def find_binaries(configured: str = "") -> Binaries:
    candidates: list[tuple[str, str]] = []
    if configured:
        candidates.append((configured, "configured"))
    try:
        import imageio_ffmpeg

        candidates.append((imageio_ffmpeg.get_ffmpeg_exe(), "bundled (imageio-ffmpeg)"))
    except Exception:
        pass
    path_ff = shutil.which("ffmpeg")
    if path_ff:
        candidates.append((path_ff, "PATH"))
    for exe, src in candidates:
        if exe and Path(exe).exists():
            v = _version(exe)
            if _major(v) >= 5 or src == "configured":
                probe = None
                sib = Path(exe).with_name("ffprobe" + Path(exe).suffix)
                if sib.exists():
                    probe = str(sib)
                elif shutil.which("ffprobe") and _major(_version(shutil.which("ffprobe"))) >= 5:
                    probe = shutil.which("ffprobe")
                return Binaries(exe, probe, v, src)
            log.warning("Ignoring outdated ffmpeg at %s (%s)", exe, v)
    raise FFmpegError("No usable FFmpeg (>= 5.0) found. Install backend requirements (imageio-ffmpeg) or set the FFmpeg path in Settings.")


def run(args: list[str], cwd: Path | None = None, timeout: int = 900) -> str:
    b = find_binaries()
    cmd = [b.ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *args]
    proc = subprocess.run(cmd, capture_output=True, cwd=str(cwd) if cwd else None, timeout=timeout)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace")[-1500:]
        raise FFmpegError(f"ffmpeg failed ({proc.returncode}): {err}")
    return proc.stderr.decode("utf-8", "replace")


@dataclass
class ProbeResult:
    duration: float
    width: int
    height: int
    fps: float
    vcodec: str
    acodec: str | None
    pix_fmt: str
    size_bytes: int

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def probe(path: Path) -> ProbeResult:
    path = Path(path)
    b = find_binaries()
    size = path.stat().st_size
    if b.ffprobe:
        out = subprocess.run([b.ffprobe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                             capture_output=True, timeout=60)
        if out.returncode == 0:
            data = json.loads(out.stdout or b"{}")
            v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
            a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
            num, _, den = (v.get("avg_frame_rate") or "0/1").partition("/")
            fps = float(num) / float(den or 1) if float(den or 1) else 0.0
            return ProbeResult(float(data.get("format", {}).get("duration") or 0), int(v.get("width") or 0),
                               int(v.get("height") or 0), round(fps, 3), v.get("codec_name", ""),
                               a.get("codec_name") if a else None, v.get("pix_fmt", ""), size)
    # Fallback: parse `ffmpeg -i` banner (bundled build ships without ffprobe)
    proc = subprocess.run([b.ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, timeout=60)
    text = proc.stderr.decode("utf-8", "replace")
    dur = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", text)
    duration = int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3)) if dur else 0.0
    vline = re.search(r"Stream #\S+.*?Video:\s*(\w+)[^\n]*", text)
    w = h = 0
    fps = 0.0
    vcodec = pix = ""
    if vline:
        line = vline.group(0)
        vcodec = vline.group(1)
        m = re.search(r"\b(\d{2,5})x(\d{2,5})\b", line)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
        m = re.search(r"([\d.]+)\s*fps", line)
        if m:
            fps = float(m.group(1))
        m = re.search(r"Video:\s*\w+[^,]*,\s*(\w+)", line)
        pix = m.group(1) if m else ""
    aline = re.search(r"Stream #\S+.*?Audio:\s*(\w+)", text)
    return ProbeResult(duration, w, h, fps, vcodec, aline.group(1) if aline else None, pix, size)


def capabilities() -> dict:
    try:
        b = find_binaries()
    except FFmpegError as exc:
        return {"ok": False, "error": str(exc)}
    out = subprocess.run([b.ffmpeg, "-hide_banner", "-filters"], capture_output=True, timeout=30).stdout.decode("utf-8", "replace")
    enc = subprocess.run([b.ffmpeg, "-hide_banner", "-encoders"], capture_output=True, timeout=30).stdout.decode("utf-8", "replace")
    names = {ln.split()[1] for ln in out.splitlines() if len(ln.split()) > 2}
    missing = [f for f in REQUIRED_FILTERS if f not in names]
    encoders = {"libx264": " libx264 " in enc, "aac": " aac " in enc, "h264_nvenc": "h264_nvenc" in enc}
    ok = not missing and encoders["libx264"] and encoders["aac"]
    return {"ok": ok, "path": b.ffmpeg, "source": b.source, "version": b.version, "ffprobe": bool(b.ffprobe),
            "missing_filters": missing, "encoders": encoders,
            "error": None if ok else f"Missing filters {missing} or encoders {encoders}"}
