"""Path safety helpers: confine file access to configured roots."""
from __future__ import annotations

import re
from pathlib import Path

from app.core.config import get_settings


class PathNotAllowed(ValueError):
    pass


def resolve_within(base: Path, candidate: str | Path) -> Path:
    """Resolve `candidate` (relative to base, or absolute) and ensure it stays inside base."""
    base = Path(base).resolve()
    p = Path(candidate)
    p = (base / p).resolve() if not p.is_absolute() else p.resolve()
    if not p.is_relative_to(base):
        raise PathNotAllowed(f"Path escapes {base.name}")
    return p


def storage_path(rel: str | Path) -> Path:
    return resolve_within(get_settings().storage_dir, rel)


def to_storage_rel(p: Path) -> str:
    return Path(p).resolve().relative_to(get_settings().storage_dir).as_posix()


def check_allowed_project_path(path: str, allowed_roots: list[str]) -> Path:
    """A project source path must exist and be inside one of the allowed roots."""
    if not path:
        raise PathNotAllowed("Path is required")
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise PathNotAllowed("Project paths must be absolute")
    p = p.resolve()
    if not allowed_roots:
        raise PathNotAllowed("No allowed project roots configured (Settings → Security → Allowed project folders)")
    for root in allowed_roots:
        try:
            r = Path(root).expanduser().resolve()
        except OSError:
            continue
        if p == r or p.is_relative_to(r):
            if not p.exists():
                raise PathNotAllowed(f"Path does not exist: {p}")
            return p
    raise PathNotAllowed(f"{p} is outside the allowed project folders")


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str, default: str = "file") -> str:
    name = Path(name).name  # drop any directory component
    name = _SAFE_NAME.sub("_", name).strip("._")
    return name[:120] or default


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:80] or "app"
