"""Common FastAPI dependencies."""
from __future__ import annotations

from fastapi import HTTPException, Request

from .config import Settings
from .db import AppDB
from .gpkg import ProjectStore
from .services import ServiceError


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> AppDB:
    return request.app.state.db


def get_project(request: Request, project_id: str) -> dict:
    db: AppDB = request.app.state.db
    p = db.get_project(project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="project not found")
    return p


def get_store(request: Request, project_id: str) -> ProjectStore:
    settings: Settings = request.app.state.settings
    p = get_project(request, project_id)
    path = settings.project_gpkg(p["id"])
    if not path.exists():
        return ProjectStore.create(path, p.get("crs"), p["name"])
    return ProjectStore.open(path)


def raise_service(e: ServiceError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=str(e))
