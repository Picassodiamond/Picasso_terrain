from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from ..auth import current_user
from ..deps import get_project, get_settings, get_store, raise_service
from ..gpkg import ProjectStore
from .. import services
from ..services import ServiceError

router = APIRouter(prefix="/projects/{project_id}", tags=["export"])


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "project"


def _ids(s: str | None) -> list[int] | None:
    if not s:
        return None
    return [int(x) for x in s.split(",") if x.strip()]


@router.get("/export.dxf")
def export_dxf(project_id: str, run_id: int | None = None, contour_sets: str | None = Query(None, description="comma separated ids"),
               points: bool = True, tin: bool = False, contours: bool = True, features: bool = True, boundary: bool = True,
               labels: bool = True, alignments: str | None = Query(None), section_set: int | None = None,
               chainage_interval: float = Query(20.0, gt=0), arc_smoothing: bool = False, text_height: float | None = Query(None, gt=0),
               point_block: bool = True, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), settings=Depends(get_settings), _: dict = Depends(current_user)):
    out = settings.project_dir(project_id) / f"{_safe(p['name'])}.dxf"
    try:
        services.export_dxf(store, out, run_id=run_id, contour_set_ids=_ids(contour_sets), include_points=points, include_tin=tin,
                            include_contours=contours, include_features=features, include_boundary=boundary, labels=labels,
                            alignment_ids=_ids(alignments), section_set_id=section_set, chainage_interval=chainage_interval,
                            arc_smoothing=arc_smoothing, text_height=text_height, point_block=point_block)
    except ServiceError as e:
        raise raise_service(e)
    return FileResponse(out, media_type="application/dxf", filename=out.name)


@router.get("/export.gpkg")
def export_gpkg(project_id: str, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    if not store.path.exists():
        raise HTTPException(status_code=404, detail="no data")
    return FileResponse(store.path, media_type="application/geopackage+sqlite3", filename=f"{_safe(p['name'])}.gpkg")


@router.get("/export.geojson")
def export_geojson(project_id: str, crs: str | None = None, contour_set: int | None = None, p: dict = Depends(get_project),
                   store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    """Everything in one FeatureCollection (points, lines, latest/selected contours, alignments)."""
    try:
        feats = []
        feats += services.points_geojson(store, p.get("crs"), crs)["features"]
        feats += services.lines_geojson(store, p.get("crs"), crs)["features"]
        sid = contour_set or ([s["id"] for s in store.contour_sets()] or [None])[-1]
        if sid:
            feats += services.contours_geojson(store, sid, p.get("crs"), crs)["features"]
        for row in store.alignments():
            al = services.alignment_from_dict(row)
            feats += [f for f in services.alignment_geojson(al, row["name"], p.get("crs"), crs)["features"] if f["geometry"]]
    except ServiceError as e:
        raise raise_service(e)
    fc = {"type": "FeatureCollection", "features": feats}
    return JSONResponse(fc, headers={"Content-Disposition": f'attachment; filename="{_safe(p["name"])}.geojson"'})
