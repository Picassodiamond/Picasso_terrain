"""FastAPI application factory."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .db import AppDB
from .deps import Slots, available_memory_mb
from .routers import alignments, auth_routes, catalogue, collab, constraints, contours, data, design_road, designs, export, jobs, projects, sections, tin
from .services import ServiceError


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title=settings.app_title, version=settings.version,
                  description="Picasso LandMesh - terrain modelling API (TIN, contours, alignments, sections).",
                  docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json")
    app.state.settings = settings
    app.state.db = AppDB(settings.app_db_path)
    app.state.heavy = Slots(settings.max_concurrent_heavy)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=settings.cors_origins != ["*"],
            allow_methods=["*"], allow_headers=["*"],
        )

    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError):
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})

    api_prefix = "/api"
    for r in (auth_routes.router, projects.router, data.router, constraints.router, tin.router, contours.router,
              alignments.router, sections.router, export.router, jobs.router, collab.router, designs.router, catalogue.router,
              design_road.router):
        app.include_router(r, prefix=api_prefix)

    @app.get("/api/health", tags=["meta"])
    def health():
        """Liveness plus load: job queue depth, heavy-request slots, free memory, and a `busy` flag
        the UI turns into a banner so users know to expect a wait."""
        q = app.state.db.queue_counts()
        slots: Slots = app.state.heavy
        avail = available_memory_mb()
        busy = q["pending"] >= settings.busy_queue_threshold or slots.in_use >= slots.capacity
        return {"status": "ok", "version": settings.version, "auth_enabled": settings.auth_enabled,
                "guest_enabled": settings.auth_enabled and settings.guest_enabled, "job_mode": settings.job_mode,
                "queue": q, "load": {"heavy_in_use": slots.in_use, "heavy_capacity": slots.capacity,
                                     "available_mb": None if avail is None else round(avail)},
                "busy": busy}

    # static frontend (Vite build) - API routes take precedence because they are registered first
    dist = settings.web_dist
    if dist and Path(dist).exists():
        app.mount("/assets", StaticFiles(directory=Path(dist) / "assets"), name="assets")
        if (Path(dist) / "cesium").exists():
            app.mount("/cesium", StaticFiles(directory=Path(dist) / "cesium"), name="cesium")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            f = Path(dist) / full_path
            if full_path and f.is_file():
                return FileResponse(f)
            # never let the browser cache the shell, so new builds are picked up immediately
            return FileResponse(Path(dist) / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
