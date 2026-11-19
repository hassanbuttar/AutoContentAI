from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.core.config import get_settings
from app.core.paths import safe_filename
from app.services.publishing.base import PlatformAdapter, PublishContext, PublishResult
from app.services.publishing.instagram import InstagramAdapter
from app.services.publishing.tiktok import TikTokAdapter
from app.services.publishing.youtube import YouTubeAdapter


class ExportAdapter(PlatformAdapter):
    """Manual workflow: writes the MP4 plus caption/metadata files for upload by hand.
    Used for 'export' accounts and for platforms/accounts where direct publishing is unavailable."""
    platform = "export"
    display_name = "Manual export"
    max_duration = 600.0

    def capabilities(self, s: dict) -> dict:
        return {"direct_publish": False, "oauth": False, "notes": ["Files are written to storage/exports for manual upload."]}

    def publish(self, ctx: PublishContext) -> PublishResult:
        folder = Path(ctx.state["export_dir"])
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / "video.mp4"
        shutil.copy2(ctx.video_path, dest)
        (folder / "caption.txt").write_text(ctx.post.caption_with_tags(5000), encoding="utf-8")
        (folder / "metadata.json").write_text(json.dumps({"title": ctx.post.title, "description": ctx.post.description,
                                                          "hashtags": ctx.post.hashtags, "tags": ctx.post.tags,
                                                          "ai_generated_visuals": ctx.post.ai_generated_visuals}, indent=2),
                                              encoding="utf-8")
        return PublishResult("exported", None, None, detail=str(folder))


ADAPTERS: dict[str, type[PlatformAdapter]] = {"youtube": YouTubeAdapter, "instagram": InstagramAdapter,
                                              "tiktok": TikTokAdapter, "export": ExportAdapter}
PLATFORMS = list(ADAPTERS)
_override: dict[str, PlatformAdapter] = {}  # tests inject adapters with mock HTTP transports


def get_adapter(platform: str) -> PlatformAdapter:
    if platform in _override:
        return _override[platform]
    cls = ADAPTERS.get(platform)
    if cls is None:
        raise ValueError(f"Unsupported platform {platform}")
    return cls()


def export_dir_for(platform: str, pub_id: int, label: str) -> Path:
    return get_settings().storage("exports", platform) / safe_filename(f"{pub_id}_{label}")
