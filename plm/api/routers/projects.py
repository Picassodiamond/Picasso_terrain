from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException, Request

from ...engine import crs as crsmod
from fastapi.responses import FileResponse

from .. import archive, services
from ..auth import current_user, require_editor
from ..db import AppDB
from ..deps import get_db, get_project, get_settings, get_store, guest_check, project_role, require_project
from ..gpkg import ProjectStore
from ..schemas import HelmertRequest, ProjectCreate, ProjectOut, ProjectUpdate

router = APIRouter(tags=["projects"])


def _out(p: dict, store: ProjectStore | None, role: str | None = None) -> ProjectOut:
    try:
        info = crsmod.describe(p.get("crs"))
    except Exception as e:  # noqa: BLE001
        info = {"spec": p.get("crs"), "error": str(e), "is_local": True}
    return ProjectOut(
        id=p["id"], name=p["name"], description=p.get("description") or "", crs=p.get("crs") or "local",
        crs_info=info, created=p["created"], updated=p["updated"], settings=p.get("settings") or {},
        summary=store.summary() if store else {}, owner_id=p.get("owner_id"), status=p.get("status") or "active", my_role=role,
    )


@router.get("/crs/presets")
def crs_presets():
    return [{"key": k, "name": v.name, "definition": v.definition, "description": v.description} for k, v in crsmod.PRESETS.items()]


@router.get("/crs/describe")
def crs_describe(spec: str):
    try:
        return crsmod.describe(spec)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid CRS: {e}")


@router.post("/crs/helmert")
def crs_helmert(body: HelmertRequest):
    try:
        h = crsmod.fit_helmert(body.source, body.target)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))
    return {"scale": h.scale, "rotation_deg": float(h.rotation * 180.0 / 3.141592653589793), "tx": h.tx, "ty": h.ty,
            "rms": h.rms, "proj": h.proj_string()}


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(request: Request, include_archived: bool = True, db: AppDB = Depends(get_db), settings=Depends(get_settings),
                  user: dict = Depends(current_user)):
    """Projects this user can see: own, member, organisation (org visibility), public, plus everything for admins."""
    out = []
    for p in db.list_projects():
        role = project_role(db, p, user)
        if role is None or (not include_archived and p.get("status") == "archived"):
            continue
        path = settings.project_gpkg(p["id"])
        store = ProjectStore.open(path) if path.exists() else None
        out.append(_out(p, store, role))
    return out


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, db: AppDB = Depends(get_db), settings=Depends(get_settings), user: dict = Depends(require_editor)):
    try:
        crsmod.resolve(body.crs)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid CRS: {e}")
    guest_check(settings, db, user, new_project=True)
    settings_in = dict(body.settings or {})
    if user.get("guest"):
        settings_in["visibility"] = "private"  # a sandbox belongs to its guest only
    owner = user["id"] if (user.get("authenticated") or user.get("guest")) else None
    p = db.create_project(body.name, body.crs, body.description, owner_id=owner, settings=settings_in)
    store = ProjectStore.create(settings.project_gpkg(p["id"]), body.crs, body.name)
    if user.get("authenticated"):
        db.add_member(p["id"], user["id"], "owner")
    db.log(p["id"], user, "project_created", "project", p["id"], {"name": body.name, "crs": body.crs})
    return _out(p, store, "owner")


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project_route(project_id: str, request: Request, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store),
                      db: AppDB = Depends(get_db), user: dict = Depends(require_project("viewer"))):
    return _out(p, store, user.get("project_role"))


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(project_id: str, body: ProjectUpdate, request: Request, db: AppDB = Depends(get_db),
                   p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), user: dict = Depends(require_project("editor"))):
    if body.settings and "visibility" in body.settings and user.get("project_role") != "owner":
        raise HTTPException(status_code=403, detail="only the owner can change who sees this project")
    if body.settings and body.settings.get("visibility") not in (None, "private", "org", "public"):
        raise HTTPException(status_code=400, detail="visibility must be private, org or public")
    if body.crs is not None:
        try:
            crsmod.resolve(body.crs)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"invalid CRS: {e}")
        store.set_crs(body.crs)
    settings_merged = None
    if body.settings is not None:
        settings_merged = dict(p.get("settings") or {})
        settings_merged.update(body.settings)
    p2 = db.update_project(project_id, name=body.name, description=body.description, crs=body.crs, settings=settings_merged)
    db.log(project_id, user, "project_updated", "project", project_id,
           {k: v for k, v in body.model_dump().items() if v is not None})
    return _out(p2, store, user.get("project_role"))  # type: ignore[arg-type]


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, db: AppDB = Depends(get_db), settings=Depends(get_settings), p: dict = Depends(get_project),
                   user: dict = Depends(current_user)):
    if project_role(db, p, user) != "owner":
        raise HTTPException(status_code=403, detail="only the owner or an admin can delete a project")
    db.delete_project(project_id)
    services.evict_tin(settings.project_gpkg(project_id))
    shutil.rmtree(settings.project_dir(project_id), ignore_errors=True)
    return None


# ---------------------------------------------------------------- archive / restore
@router.post("/projects/{project_id}/archive", response_model=ProjectOut)
def archive_project(project_id: str, db: AppDB = Depends(get_db), settings=Depends(get_settings), p: dict = Depends(get_project),
                    user: dict = Depends(current_user)):
    """Owner only: write a portable bundle (GeoPackage + metadata) to the archive store and make the project read-only."""
    if project_role(db, p, user) != "owner":
        raise HTTPException(status_code=403, detail="only the owner can archive a project")
    if user.get("guest"):
        raise HTTPException(status_code=403, detail="sign in to archive projects")
    path = archive.archive_project(settings, db, p, user)
    p2 = db.get_project(project_id)
    store = ProjectStore.open(settings.project_gpkg(project_id))
    out = _out(p2, store, "owner")  # type: ignore[arg-type]
    out.settings = {**out.settings, "archive_file": path.name}
    return out


@router.post("/projects/{project_id}/restore", response_model=ProjectOut)
def restore_project(project_id: str, db: AppDB = Depends(get_db), settings=Depends(get_settings), p: dict = Depends(get_project),
                    user: dict = Depends(current_user)):
    if project_role(db, p, user) != "owner":
        raise HTTPException(status_code=403, detail="only the owner can restore a project")
    archive.restore_project(settings, db, p, user)
    return _out(db.get_project(project_id), ProjectStore.open(settings.project_gpkg(project_id)), "owner")  # type: ignore[arg-type]


@router.get("/projects/{project_id}/archive.zip")
def download_archive(project_id: str, db: AppDB = Depends(get_db), settings=Depends(get_settings), p: dict = Depends(get_project),
                     user: dict = Depends(current_user)):
    if project_role(db, p, user) != "owner":
        raise HTTPException(status_code=403, detail="only the owner can download the archive")
    path = archive.latest_archive(settings, project_id) or archive.archive_project(settings, db, p, user, mark=False)
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.get("/archives")
def list_archives(settings=Depends(get_settings), user: dict = Depends(current_user), db: AppDB = Depends(get_db)):
    """Archive bundles on this server (admins see all, others their own projects)."""
    items = archive.list_archives(settings)
    if user.get("role") == "admin":
        return items
    return [a for a in items if (db.get_project(a["project_id"]) or {}).get("owner_id") == user.get("id")]
