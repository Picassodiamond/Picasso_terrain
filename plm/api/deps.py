"""Common FastAPI dependencies: project access roles, guest quotas, admission control."""
from __future__ import annotations

import ctypes
import sys
import threading
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request

from .auth import current_user
from .config import Settings
from .db import AppDB
from .gpkg import ProjectStore
from .services import ServiceError

ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}


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


# ---------------------------------------------------------------- project roles
def project_role(db: AppDB, p: dict, user: dict) -> str | None:
    """owner | editor | viewer | None for this user on this project.

    admin -> owner everywhere; the project owner -> owner; explicit members -> their role;
    otherwise by visibility: public -> viewer; org -> members of the owner's organisation
    (or any account for legacy projects without an owner) edit; private -> nothing."""
    if user.get("role") == "admin":
        return "owner"
    if p.get("owner_id") and p.get("owner_id") == user.get("id"):
        return "owner"
    if user.get("authenticated"):
        r = db.member_role(p["id"], user["id"])
        if r:
            return r
    vis = (p.get("settings") or {}).get("visibility", "org")
    if vis == "public":
        return "viewer"
    if vis == "org" and user.get("authenticated"):
        owner = db.get_user(p["owner_id"]) if p.get("owner_id") else None
        if owner is None or (owner.get("organisation") or "") == (user.get("organisation") or ""):
            return "editor" if user.get("role") in ("editor", "admin") else "viewer"
    return None


def require_project(min_role: str):
    """Dependency factory: the caller must have at least `min_role` on the project in the path.
    Writes to an archived project are refused (423). Returns the user with `project_role` added."""

    def dep(request: Request, project_id: str, user: dict = Depends(current_user)) -> dict:
        db: AppDB = request.app.state.db
        p = db.get_project(project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="project not found")
        role = project_role(db, p, user)
        if role is None:
            if not user.get("authenticated"):
                raise HTTPException(status_code=401, detail="sign in to access this project")
            raise HTTPException(status_code=403, detail="you do not have access to this project")
        if ROLE_RANK[role] < ROLE_RANK[min_role]:
            raise HTTPException(status_code=403, detail=f"{min_role} role required on this project (you are {role})")
        if min_role != "viewer" and p.get("status") == "archived":
            raise HTTPException(status_code=423, detail="this project is archived (read-only); the owner can restore it")
        return {**user, "project_role": role}

    return dep


# ---------------------------------------------------------------- guest quotas
def guest_check(settings: Settings, db: AppDB, user: dict, *, new_project: bool = False, points_total: int | None = None,
                tin_runs: int | None = None, export: bool = False) -> None:
    """Refuse a guest action that exceeds the sandbox quotas, with a message that says what to do."""
    if not user.get("guest"):
        return
    hint = " Create a free account to keep your work and lift this limit."
    if export:
        raise HTTPException(status_code=403, detail="exports are available to signed-in users." + hint)
    if new_project and len(db.projects_owned_by(user["id"])) >= settings.guest_max_projects:
        raise HTTPException(status_code=403, detail=f"guest sandbox allows {settings.guest_max_projects} projects." + hint)
    if points_total is not None and points_total > settings.guest_max_points:
        raise HTTPException(status_code=403, detail=f"guest sandbox allows {settings.guest_max_points:,} points per project (this would make {points_total:,})." + hint)
    if tin_runs is not None and tin_runs >= settings.guest_max_tin_runs:
        raise HTTPException(status_code=403, detail=f"guest sandbox allows {settings.guest_max_tin_runs} TIN builds per project." + hint)


def no_guest(request: Request, user: dict = Depends(current_user)) -> None:
    """Router-level dependency: refuse guests (exports and other keep-your-work features)."""
    guest_check(request.app.state.settings, request.app.state.db, user, export=True)


# ---------------------------------------------------------------- admission control
class Slots:
    """Counting semaphore with a readable occupancy, for heavy synchronous work."""

    def __init__(self, capacity: int):
        self.capacity = max(1, int(capacity))
        self.in_use = 0
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        with self._lock:
            if self.in_use >= self.capacity:
                return False
            self.in_use += 1
            return True

    def release(self) -> None:
        with self._lock:
            self.in_use = max(0, self.in_use - 1)


def heavy(request: Request) -> Iterator[None]:
    """Take a heavy-request slot for the duration of the request, or answer 503 + Retry-After."""
    slots: Slots = request.app.state.heavy
    if not slots.acquire():
        raise HTTPException(status_code=503, detail="the server is busy with other heavy requests; please retry in a few seconds",
                            headers={"Retry-After": "10"})
    try:
        yield
    finally:
        slots.release()


def available_memory_mb() -> float | None:
    """Free physical memory in MB (Windows via GlobalMemoryStatusEx, Linux via /proc/meminfo), None if unknown."""
    try:
        if sys.platform == "win32":
            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = MS()
            ms.dwLength = ctypes.sizeof(MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                return ms.ullAvailPhys / 1e6
            return None
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1000.0
    except Exception:  # noqa: BLE001
        return None
    return None


def memory_guard(n_points: int) -> None:
    """Refuse a TIN build that would not fit in the memory currently available (about 1.2 kB per point at peak)."""
    avail = available_memory_mb()
    need = n_points * 1.2 / 1000.0
    if avail is not None and need > 0.8 * avail:
        raise HTTPException(status_code=507, detail=f"not enough free memory for {n_points:,} points right now (needs about {need:.0f} MB, "
                                                    f"{avail:.0f} MB free); wait for running jobs to finish or thin the survey")
