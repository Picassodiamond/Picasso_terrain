from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from ..auth import current_user, require_editor
from ..db import AppDB
from ..deps import get_db, get_project, get_settings, get_store, raise_service
from ..gpkg import ProjectStore
from ..schemas import ContourParams, ContourSetOut, ContourSetPatch, JobOut
from .. import services
from ..services import ServiceError
from .tin import start_job

router = APIRouter(prefix="/projects/{project_id}", tags=["contours"])


@router.post("/contours", response_model=JobOut, status_code=202)
def create_contours(project_id: str, body: ContourParams, background: BackgroundTasks, p: dict = Depends(get_project),
                    store: ProjectStore = Depends(get_store), db: AppDB = Depends(get_db), settings=Depends(get_settings), user: dict = Depends(require_editor)):
    rid = body.run_id if body.run_id is not None else store.latest_tin_run_id()
    if rid is None:
        raise HTTPException(status_code=400, detail="build a TIN first")
    run = store.get_tin_run(rid)
    if run is None:
        raise HTTPException(status_code=404, detail="TIN run not found")
    sync = body.sync if body.sync is not None else run["n_triangles"] <= settings.sync_point_limit * 2
    params = body.model_dump()
    params["run_id"] = rid
    job = start_job("contours", params, project_id, sync, background, db, settings, user)
    if job["status"] == "error":
        raise HTTPException(status_code=400, detail=job.get("error"))
    return JobOut(**job)


@router.get("/contours", response_model=list[ContourSetOut])
def list_sets(project_id: str, run_id: int | None = None, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    return [ContourSetOut(**s) for s in store.contour_sets(run_id)]


# NOTE: the ".geojson" routes are declared before "/contours/{set_id}" because Starlette matches
# path parameters greedily ("1.geojson" would otherwise be parsed as set_id and fail validation).
@router.get("/contours/{set_id}.geojson")
def set_geojson(project_id: str, set_id: int, crs: str | None = None, major_only: bool = False, ndigits: int = Query(6, ge=0, le=10),
                p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    if store.get_contour_set(set_id) is None:
        raise HTTPException(status_code=404, detail="contour set not found")
    try:
        return JSONResponse(services.contours_geojson(store, set_id, p.get("crs"), crs, major_only, ndigits))
    except ServiceError as e:
        raise raise_service(e)


@router.get("/contours/{set_id}/labels.geojson")
def labels_geojson(project_id: str, set_id: int, crs: str | None = None, every: float | None = Query(None, gt=0),
                   p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    try:
        return JSONResponse(services.contour_labels_geojson(store, set_id, p.get("crs"), crs, every))
    except ServiceError as e:
        raise raise_service(e)


@router.get("/contours/{set_id}", response_model=ContourSetOut)
def get_set(project_id: str, set_id: int, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    s = store.get_contour_set(set_id)
    if s is None:
        raise HTTPException(status_code=404, detail="contour set not found")
    return ContourSetOut(**s)


@router.patch("/contours/{set_id}", response_model=ContourSetOut)
def patch_set(project_id: str, set_id: int, body: ContourSetPatch, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(require_editor)):
    if store.get_contour_set(set_id) is None:
        raise HTTPException(status_code=404, detail="contour set not found")
    store.update_contour_set(set_id, style=body.style.model_dump() if body.style else None, name=body.name)
    return ContourSetOut(**store.get_contour_set(set_id))  # type: ignore[arg-type]


@router.delete("/contours/{set_id}", status_code=204)
def delete_set(project_id: str, set_id: int, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(require_editor)):
    if not store.delete_contour_set(set_id):
        raise HTTPException(status_code=404, detail="contour set not found")
    return None
