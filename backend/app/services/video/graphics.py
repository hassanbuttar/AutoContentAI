"""Pillow-based graphics: frame composition, title cards, overlay text and caption PNGs.

Rendering text with Pillow (instead of libass/drawtext) keeps the pipeline independent of FFmpeg
build options and font configuration."""
from __future__ import annotations

import re
import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

FONT_CANDIDATES_BOLD = [
    "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/seguisb.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
FONT_CANDIDATES_REGULAR = [
    "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


@lru_cache(maxsize=64)
def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    for p in (FONT_CANDIDATES_BOLD if bold else FONT_CANDIDATES_REGULAR):
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def hex_rgb(h: str, default=(79, 70, 229)) -> tuple[int, int, int]:
    h = (h or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}", h):
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    named = {"indigo": (79, 70, 229), "cyan": (34, 211, 238), "black": (0, 0, 0), "white": (255, 255, 255)}
    return named.get(h.lower(), default)


def brand_pair(colors: list[str] | None) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    cs = [c for c in (colors or []) if c]
    a = hex_rgb(cs[0]) if cs else (79, 70, 229)
    b = hex_rgb(cs[1]) if len(cs) > 1 else (17, 24, 39)
    return a, b


def gradient(w: int, h: int, top: tuple, bottom: tuple) -> Image.Image:
    base = Image.new("RGB", (1, 256))
    for y in range(256):
        t = y / 255
        base.putpixel((0, y), tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    return base.resize((w, h), Image.BICUBIC)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=fnt) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def text_png(text: str, out: Path, *, width: int, size: int, color: str = "#FFFFFF", stroke: str = "#000000",
             stroke_w: int | None = None, box: tuple[int, int, int, int] | None = None, bold: bool = True,
             max_lines: int = 4, line_gap: float = 1.18) -> tuple[int, int]:
    """Render centered, wrapped text to a transparent PNG. Returns (w, h)."""
    fnt = font(size, bold)
    tmp = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(tmp)
    pad = int(size * 0.45)
    lines = wrap(d, text, fnt, width - 2 * pad)[:max_lines]
    line_h = int(size * line_gap)
    h = line_h * len(lines) + 2 * pad
    img = Image.new("RGBA", (width, max(h, 1)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if box:
        widest = max((d.textlength(ln, font=fnt) for ln in lines), default=0)
        bw = int(widest + 2 * pad)
        x0 = (width - bw) // 2
        d.rounded_rectangle([x0, 0, x0 + bw, h], radius=int(size * 0.35), fill=box)
    sw = stroke_w if stroke_w is not None else max(2, size // 14)
    y = pad
    for ln in lines:
        tw = d.textlength(ln, font=fnt)
        d.text(((width - tw) / 2, y), ln, font=fnt, fill=hex_rgb(color, (255, 255, 255)),
               stroke_width=0 if box else sw, stroke_fill=hex_rgb(stroke, (0, 0, 0)))
        y += line_h
    img.save(out)
    return img.size


def cover(img: Image.Image, w: int, h: int) -> Image.Image:
    return ImageOps.fit(img.convert("RGB"), (w, h), Image.LANCZOS, centering=(0.5, 0.45))


def compose_screenshot(src: Path, w: int, h: int, accent: tuple[int, int, int]) -> Image.Image:
    """Screenshot on a blurred, tinted copy of itself with rounded corners and a shadow (works for any aspect)."""
    shot = Image.open(src).convert("RGB")
    bg = cover(shot, w, h).filter(ImageFilter.GaussianBlur(40))
    tint = Image.new("RGB", (w, h), accent)
    bg = Image.blend(bg, tint, 0.35)
    bg = Image.blend(bg, Image.new("RGB", (w, h), (0, 0, 0)), 0.25)
    max_w, max_h = int(w * 0.9), int(h * 0.62)
    fg = ImageOps.contain(shot, (max_w, max_h), Image.LANCZOS)
    radius = max(12, fg.width // 28)
    mask = Image.new("L", fg.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, fg.width, fg.height], radius=radius, fill=255)
    x, y = (w - fg.width) // 2, int(h * 0.20) + (max_h - fg.height) // 2
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([x + 10, y + 24, x + fg.width + 10, y + fg.height + 24], radius=radius, fill=(0, 0, 0, 140))
    shadow = shadow.filter(ImageFilter.GaussianBlur(24))
    out = bg.convert("RGBA")
    out.alpha_composite(shadow)
    out.paste(fg, (x, y), mask)
    return out.convert("RGB")


def title_card(w: int, h: int, colors: list[str] | None, title: str, subtitle: str = "", logo: Path | None = None) -> Image.Image:
    a, b = brand_pair(colors)
    img = gradient(w, h, a, b).convert("RGBA")
    d = ImageDraw.Draw(img)
    # soft decorative circles
    deco = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dd = ImageDraw.Draw(deco)
    dd.ellipse([-w * 0.3, h * 0.05, w * 0.7, h * 0.05 + w], fill=(255, 255, 255, 22))
    dd.ellipse([w * 0.45, h * 0.62, w * 1.4, h * 0.62 + w * 0.95], fill=(255, 255, 255, 16))
    img.alpha_composite(deco.filter(ImageFilter.GaussianBlur(6)))
    y = int(h * 0.30)
    if logo and Path(logo).exists():
        lg = Image.open(logo).convert("RGBA")
        lg = ImageOps.contain(lg, (int(w * 0.36), int(w * 0.36)))
        img.alpha_composite(lg, ((w - lg.width) // 2, int(h * 0.14)))
        y = int(h * 0.14) + lg.height + int(h * 0.05)
    tf = font(int(w * 0.085), True)
    for ln in wrap(d, title, tf, int(w * 0.84))[:5]:
        tw = d.textlength(ln, font=tf)
        d.text(((w - tw) / 2, y), ln, font=tf, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 90))
        y += int(tf.size * 1.2)
    if subtitle:
        sf = font(int(w * 0.045), False)
        y += int(h * 0.02)
        for ln in wrap(d, subtitle, sf, int(w * 0.8))[:3]:
            tw = d.textlength(ln, font=sf)
            d.text(((w - tw) / 2, y), ln, font=sf, fill=(235, 238, 255))
            y += int(sf.size * 1.3)
    return img.convert("RGB")


def caption_chunks(narration: str, duration: float, max_words: int = 4, lead: float = 0.15) -> list[tuple[str, float, float]]:
    """Split narration into short chunks timed proportionally to character length."""
    words = narration.split()
    if not words or duration <= 0:
        return []
    chunks: list[str] = []
    cur: list[str] = []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or re.search(r"[.!?,;:]$", w):
            chunks.append(" ".join(cur))
            cur = []
    if cur:
        chunks.append(" ".join(cur))
    total_chars = sum(len(c) + 2 for c in chunks)
    usable = max(0.5, duration - lead - 0.1)
    t = lead
    out = []
    for c in chunks:
        d = usable * (len(c) + 2) / total_chars
        out.append((c, round(t, 3), round(t + d, 3)))
        t += d
    return out


def logo_png(src: Path, out: Path, size: int) -> Path:
    lg = Image.open(src).convert("RGBA")
    lg = ImageOps.contain(lg, (size, size))
    lg.save(out)
    return out


_ = sys
