from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException, Request

from ...engine import crs as crsmod
from ..auth import current_user, require_editor
from ..db import AppDB
from .. import services
from ..deps import get_db, get_project, get_settings, get_store
from ..gpkg import ProjectStore
from ..schemas import HelmertRequest, ProjectCreate, ProjectOut, ProjectUpdate

router = APIRouter(tags=["projects"])


def _out(p: dict, store: ProjectStore | None) -> ProjectOut:
    try:
        info = crsmod.describe(p.get("crs"))
    except Exception as e:  # noqa: BLE001
        info = {"spec": p.get("crs"), "error": str(e), "is_local": True}
    return ProjectOut(
        id=p["id"], name=p["name"], description=p.get("description") or "", crs=p.get("crs") or "local",
        crs_info=info, created=p["created"], updated=p["updated"], settings=p.get("settings") or {},
        summary=store.summary() if store else {},
    )


def _visible(p: dict, user: dict, mine: set[str] | None) -> bool:
    if mine is None or user.get("role") == "admin":
        return True
    visibility = (p.get("settings") or {}).get("visibility", "org")
    return visibility != "private" or p["id"] in mine or p.get("owner_id") == user["id"]


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
def list_projects(request: Request, db: AppDB = Depends(get_db), settings=Depends(get_settings), user: dict = Depends(current_user)):
    mine = db.project_ids_for_user(user["id"]) if user.get("authenticated") else None
    out = []
    for p in db.list_projects():
        if not _visible(p, user, mine):
            continue
        path = settings.project_gpkg(p["id"])
        store = ProjectStore.open(path) if path.exists() else None
        out.append(_out(p, store))
    return out


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, db: AppDB = Depends(get_db), settings=Depends(get_settings), user: dict = Depends(require_editor)):
    try:
        crsmod.resolve(body.crs)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid CRS: {e}")
    p = db.create_project(body.name, body.crs, body.description, owner_id=user["id"] if user.get("authenticated") else None,
                          settings=body.settings)
    store = ProjectStore.create(settings.project_gpkg(p["id"]), body.crs, body.name)
    if user.get("authenticated"):
        db.add_member(p["id"], user["id"], "owner")
    db.log(p["id"], user, "project_created", "project", p["id"], {"name": body.name, "crs": body.crs})
    return _out(p, store)


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project_route(project_id: str, request: Request, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store),
                      db: AppDB = Depends(get_db), user: dict = Depends(current_user)):
    mine = db.project_ids_for_user(user["id"]) if user.get("authenticated") else None
    if not _visible(p, user, mine):
        raise HTTPException(status_code=403, detail="you are not a member of this private project")
    return _out(p, store)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(project_id: str, body: ProjectUpdate, request: Request, db: AppDB = Depends(get_db),
                   p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), user: dict = Depends(require_editor)):
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
    return _out(p2, store)  # type: ignore[arg-type]


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, db: AppDB = Depends(get_db), settings=Depends(get_settings), p: dict = Depends(get_project),
                   user: dict = Depends(require_editor)):
    if user.get("authenticated") and user["role"] != "admin" and p.get("owner_id") != user["id"]:
        raise HTTPException(status_code=403, detail="only the owner or an admin can delete a project")
    db.delete_project(project_id)
    services.evict_tin(settings.project_gpkg(project_id))
    shutil.rmtree(settings.project_dir(project_id), ignore_errors=True)
    return None
