"""FastAPI application: API + built dashboard + embedded workers/scheduler (Mode A default)."""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal, get_engine
from app.core.logging_setup import setup_logging
from app.core.net import use_system_trust
from app.models import ComfyWorkflow

log = logging.getLogger("autocontent")
use_system_trust()


def run_migrations() -> None:
    """Apply Alembic migrations (creates the schema on first run)."""
    from alembic import command
    from alembic.config import Config

    here = Path(__file__).resolve().parents[1]
    cfg = Config(str(here / "alembic.ini"))
    cfg.set_main_option("script_location", str(here / "migrations"))
    cfg.set_main_option("sqlalchemy.url", get_settings().db_url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


def seed_workflows() -> int:
    """Import bundled ComfyUI workflows (workflows/comfyui/*.json) once; never overwrites user edits."""
    s = get_settings()
    n = 0
    with SessionLocal() as db:
        existing = {w.name for w in db.scalars(select(ComfyWorkflow))}
        for f in sorted(Path(s.workflows_dir).glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("Skipping invalid workflow file %s", f.name)
                continue
            if d.get("name") in existing or "workflow" not in d:
                continue
            db.add(ComfyWorkflow(name=d["name"], description=d.get("description", ""), kind=d.get("kind", "txt2img"),
                                 workflow=d["workflow"], mappings=d.get("mappings", {}), defaults=d.get("defaults", {}),
                                 output_type=d.get("output_type", "image"), is_default=bool(d.get("is_default")),
                                 enabled=bool(d.get("enabled", True)), timeout_seconds=int(d.get("timeout_seconds", 600))))
            n += 1
        db.commit()
    return n


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    s = get_settings()
    get_engine()
    run_migrations()
    seeded = seed_workflows()
    if seeded:
        log.info("Imported %d bundled ComfyUI workflow(s)", seeded)
    pool = sched = None
    if s.run_workers:
        from app.workers.handlers import HANDLERS
        from app.workers.runner import WorkerPool

        pool = WorkerPool(HANDLERS)
        pool.start()
    if s.run_scheduler:
        from app.workers.runner import start_scheduler

        sched = start_scheduler(pool)
    app.state.pool = pool
    log.info("AutoContent AI ready on http://%s:%s", s.host, s.port)
    try:
        yield
    finally:
        if sched:
            sched.shutdown(wait=False)
        if pool:
            pool.stop()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="AutoContent AI", version="1.0.0", lifespan=lifespan,
                  docs_url="/api/docs" if s.env == "development" else None, openapi_url="/api/openapi.json")
    if s.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def security(request: Request, call_next):
        # CSRF defence: state-changing API calls must carry a custom header (cannot be set cross-site without CORS)
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/") \
                and request.headers.get("x-requested-with") != "autocontent":
            return JSONResponse({"detail": "Missing X-Requested-With header"}, status_code=403)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.url.path.startswith(("/api/docs", "/api/openapi")):
            resp.headers.setdefault("Content-Security-Policy",
                                    "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
                                    "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        return resp

    from app.api import (accounts_routes, applications_routes, auth_routes, content_routes, ops_routes, system_routes)

    for r in (auth_routes.router, system_routes.router, applications_routes.router, accounts_routes.router,
              content_routes.router, ops_routes.router, content_routes.media_router):
        app.include_router(r)

    dist = Path(s.frontend_dist)

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if dist.exists():
            target = (dist / full_path).resolve()
            if full_path and target.is_file() and target.is_relative_to(dist.resolve()):
                return FileResponse(target)
            index = dist / "index.html"
            if index.exists():
                return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse({"detail": "Dashboard not built. Run scripts/build-frontend or use the Vite dev server (npm run dev)."},
                            status_code=404)

    return app


app = create_app()
