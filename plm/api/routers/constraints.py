"""Constraint workflow: automatic detection, review (accept / reject) and listing.

Semi-automatic mode in the UI is detect -> show suggestions -> accept; automatic mode does the
same inside the TIN job when the user has not supplied a boundary.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from ..auth import current_user, require_editor
from ..db import AppDB
from ..deps import get_db, get_project, get_store, raise_service
from ..gpkg import ProjectStore
from ..schemas import AcceptConstraintsIn, DetectParams
from .. import services
from ..services import ServiceError

router = APIRouter(prefix="/projects/{project_id}", tags=["constraints"])


@router.post("/constraints/detect")
def detect(project_id: str, body: DetectParams | None = None, crs: str | None = None, p: dict = Depends(get_project),
           store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    """Suggest boundary / gap constraints from the survey points (nothing is stored).

    Returns a GeoJSON FeatureCollection of closed LineStrings with kind, reason, confidence and
    stats per feature, plus detection statistics."""
    try:
        return JSONResponse(services.detect_constraints(store, (body or DetectParams()).model_dump(), p.get("crs"), crs))
    except ServiceError as e:
        raise raise_service(e)


@router.post("/constraints/accept")
def accept(project_id: str, body: AcceptConstraintsIn, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store),
           db: AppDB = Depends(get_db), user: dict = Depends(require_editor)):
    """Store reviewed constraints as project lines (boundary / void / feature) so the TIN honours them."""
    try:
        out = services.accept_constraints(store, body.model_dump())
    except ServiceError as e:
        raise raise_service(e)
    db.log(project_id, user, "constraints_accepted", "lines", "", {"added": out["added"]})
    db.touch_project(project_id)
    return out


@router.get("/constraints")
def list_constraints(project_id: str, crs: str | None = None, source: str | None = Query(None), p: dict = Depends(get_project),
                     store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    """All constraint lines with their kind and source (auto / accepted / drawn / imported file)."""
    try:
        fc = services.lines_geojson(store, p.get("crs"), crs)
    except ServiceError as e:
        raise raise_service(e)
    if source:
        fc["features"] = [f for f in fc["features"] if f["properties"].get("source") == source]
    return JSONResponse(fc)


@router.delete("/constraints/auto")
def delete_auto(project_id: str, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store),
                db: AppDB = Depends(get_db), _: dict = Depends(require_editor)):
    """Remove constraints that were added automatically (source = auto)."""
    n = store.delete_lines(source="auto")
    db.touch_project(project_id)
    return {"deleted": n}
