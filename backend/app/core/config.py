"""Process-level configuration (environment / .env).

Runtime-editable settings (budgets, ComfyUI URL, timezone, ...) live in the
`system_settings` table and are accessed through `app.services.settings_service`.
Only bootstrap values that are needed before the DB is available belong here.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTOCONTENT_",
        env_file=(PROJECT_ROOT / ".env",),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "production"  # production | development | test
    host: str = "127.0.0.1"
    port: int = 8000
    storage_dir: Path = PROJECT_ROOT / "storage"
    database_url: str | None = None  # default: sqlite in storage_dir
    workflows_dir: Path = PROJECT_ROOT / "workflows" / "comfyui"
    frontend_dist: Path = PROJECT_ROOT / "frontend" / "dist"

    # Security
    session_secret: str | None = None  # derived from master key if unset
    session_hours: int = 24 * 7
    cookie_secure: bool = False
    cors_origins: list[str] = Field(default_factory=list)
    public_base_url: str | None = None  # used for OAuth redirect URIs (Mode B / tunnels)
    master_key: str | None = None  # Fernet key; overrides OS keychain (headless Linux / Docker)
    keyring_service: str = "autocontent-ai"

    # Workers
    run_workers: bool = True  # run job workers inside the API process
    run_scheduler: bool = True
    default_lane_concurrency: int = 2
    gpu_lane_concurrency: int = 1
    tick_seconds: int = 60
    poll_seconds: float = 2.0
    lease_seconds: int = 300

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.storage_dir / 'autocontent.db').as_posix()}"

    def storage(self, *parts: str) -> Path:
        p = self.storage_dir.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.storage_dir = Path(s.storage_dir).resolve()
    for sub in ("projects", "generated_assets", "videos", "thumbnails", "logs", "exports", "uploads", "backups", "work"):
        (s.storage_dir / sub).mkdir(parents=True, exist_ok=True)
    return s
