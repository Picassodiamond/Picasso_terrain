from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from ..auth import current_user, require_editor
from ..db import AppDB
from ..deps import get_db, get_project, get_settings, get_store, raise_service
from ..gpkg import ProjectStore
from ..schemas import JobOut, SectionParams, SectionSetOut
from .. import services
from ..services import ServiceError
from .tin import start_job

router = APIRouter(prefix="/projects/{project_id}", tags=["sections"])


@router.post("/sections", response_model=JobOut, status_code=202)
def create_sections(project_id: str, body: SectionParams, background: BackgroundTasks, p: dict = Depends(get_project),
                    store: ProjectStore = Depends(get_store), db: AppDB = Depends(get_db), settings=Depends(get_settings), user: dict = Depends(require_editor)):
    if store.get_alignment(body.alignment_id) is None:
        raise HTTPException(status_code=404, detail="alignment not found")
    rid = body.run_id if body.run_id is not None else store.latest_tin_run_id()
    if rid is None:
        raise HTTPException(status_code=400, detail="build a TIN first")
    params = body.model_dump()
    params["run_id"] = rid
    sync = body.sync if body.sync is not None else True
    job = start_job("sections", params, project_id, sync, background, db, settings, user)
    if job["status"] == "error":
        raise HTTPException(status_code=400, detail=job.get("error"))
    return JobOut(**job)


@router.get("/sections", response_model=list[SectionSetOut])
def list_sets(project_id: str, alignment_id: int | None = None, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    return [SectionSetOut(**s) for s in store.section_sets(alignment_id)]


@router.get("/sections/{set_id}", response_model=SectionSetOut)
def get_set(project_id: str, set_id: int, full: bool = True, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    s = store.get_section_set(set_id, full=full)
    if s is None:
        raise HTTPException(status_code=404, detail="section set not found")
    return SectionSetOut(**s)


@router.delete("/sections/{set_id}", status_code=204)
def delete_set(project_id: str, set_id: int, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(require_editor)):
    if not store.delete_section_set(set_id):
        raise HTTPException(status_code=404, detail="section set not found")
    return None


@router.get("/sections/{set_id}/profile.csv")
def profile_csv(project_id: str, set_id: int, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    try:
        prof, _ = services.section_set_csvs(store, set_id)
    except ServiceError as e:
        raise raise_service(e)
    return PlainTextResponse(prof, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="Profile.csv"'})


@router.get("/sections/{set_id}/cross.csv")
def cross_csv(project_id: str, set_id: int, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    try:
        _, cross = services.section_set_csvs(store, set_id)
    except ServiceError as e:
        raise raise_service(e)
    return PlainTextResponse(cross, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="Cross.csv"'})


@router.get("/sections/{set_id}/lines.geojson")
def lines_geojson(project_id: str, set_id: int, crs: str | None = None, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    try:
        return JSONResponse(services.section_lines_geojson(store, set_id, p.get("crs"), crs))
    except ServiceError as e:
        raise raise_service(e)
