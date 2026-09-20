"""Design workspaces: the hand-off from the terrain module to road / canal / building design.

A design belongs to a project, names its module and is pinned to one immutable TIN run (the
terrain snapshot). Module engines add their own tables and routers; this router only manages the
records and the registry.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import current_user, require_editor
from ..db import AppDB
from ..deps import get_db, get_project, get_store
from ..gpkg import ProjectStore
from ..modules import MODULES, available_design_modules, module
from ..schemas import DesignIn, DesignOut, DesignPatch

router = APIRouter(tags=["designs"])


@router.get("/modules")
def list_modules(_: dict = Depends(current_user)):
    """Module registry: the terrain core plus available and planned design modules."""
    return MODULES


@router.get("/projects/{project_id}/designs", response_model=list[DesignOut])
def list_designs(project_id: str, module_id: str | None = None, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store),
                 _: dict = Depends(current_user)):
    return [DesignOut(**d) for d in store.designs(module_id)]


@router.post("/projects/{project_id}/designs", response_model=DesignOut, status_code=201)
def create_design(project_id: str, body: DesignIn, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store),
                  db: AppDB = Depends(get_db), user: dict = Depends(require_editor)):
    m = module(body.module)
    if m is None or m["kind"] != "design":
        raise HTTPException(status_code=400, detail=f"unknown design module {body.module!r}; available: {available_design_modules()}")
    if m["status"] != "available":
        raise HTTPException(status_code=400, detail=f"module {m['label']} is planned but not available yet")
    run_id = body.tin_run_id if body.tin_run_id is not None else store.latest_tin_run_id()
    if "tin_run" in m["requires"]:
        if run_id is None:
            raise HTTPException(status_code=400, detail="build a TIN first - a design is pinned to a terrain snapshot (TIN run)")
        if store.get_tin_run(int(run_id)) is None:
            raise HTTPException(status_code=404, detail="TIN run not found")
    if body.alignment_id is not None and store.get_alignment(body.alignment_id) is None:
        raise HTTPException(status_code=404, detail="alignment not found")
    name = body.name.strip() or f"{m['label']} {len(store.designs(body.module)) + 1}"
    did = store.add_design(body.module, name, run_id, body.alignment_id, body.settings)
    db.log(project_id, user, "design_created", "design", str(did), {"module": body.module, "tin_run_id": run_id})
    db.touch_project(project_id)
    return DesignOut(**store.get_design(did))  # type: ignore[arg-type]


@router.get("/projects/{project_id}/designs/{design_id}", response_model=DesignOut)
def get_design(project_id: str, design_id: int, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    d = store.get_design(design_id)
    if d is None:
        raise HTTPException(status_code=404, detail="design not found")
    return DesignOut(**d)


@router.patch("/projects/{project_id}/designs/{design_id}", response_model=DesignOut)
def patch_design(project_id: str, design_id: int, body: DesignPatch, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store),
                 db: AppDB = Depends(get_db), user: dict = Depends(require_editor)):
    d = store.get_design(design_id)
    if d is None:
        raise HTTPException(status_code=404, detail="design not found")
    if body.tin_run_id is not None and store.get_tin_run(body.tin_run_id) is None:
        raise HTTPException(status_code=404, detail="TIN run not found")
    fields = body.model_dump(exclude_none=True)
    if "settings" in fields:  # merge, like project settings
        fields["settings"] = {**d.get("settings", {}), **fields["settings"]}
    store.update_design(design_id, **fields)
    if body.tin_run_id is not None and body.tin_run_id != d.get("tin_run_id"):
        db.log(project_id, user, "design_rebased", "design", str(design_id), {"from": d.get("tin_run_id"), "to": body.tin_run_id})
    db.touch_project(project_id)
    return DesignOut(**store.get_design(design_id))  # type: ignore[arg-type]


@router.delete("/projects/{project_id}/designs/{design_id}", status_code=204)
def delete_design(project_id: str, design_id: int, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store),
                  db: AppDB = Depends(get_db), user: dict = Depends(require_editor)):
    if not store.delete_design(design_id):
        raise HTTPException(status_code=404, detail="design not found")
    db.log(project_id, user, "design_deleted", "design", str(design_id), {})
    db.touch_project(project_id)
    return None
