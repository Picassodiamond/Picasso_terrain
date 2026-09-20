"""Import survey data and read/delete points and lines."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from ..db import AppDB
from ..deps import get_db, get_project, get_settings, get_store, guest_check, raise_service, require_project
from ..gpkg import ProjectStore
from ..schemas import ImportResult
from .. import services
from ..services import ServiceError

router = APIRouter(prefix="/projects/{project_id}", tags=["data"])


@router.post("/import", response_model=ImportResult)
async def import_file(
    project_id: str, request: Request,
    file: UploadFile = File(...),
    kind: str = Form("auto"), layer: str | None = Form(None),
    delimiter: str | None = Form("auto"), mapping: str | None = Form(None), has_header: str | None = Form(None),
    point_layers: str | None = Form(None), feature_layers: str | None = Form(None),
    boundary_layers: str | None = Form(None), void_layers: str | None = Form(None),
    replace: bool = Form(False),
    p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), db: AppDB = Depends(get_db),
    settings=Depends(get_settings), user: dict = Depends(require_project("editor")),
):
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"file larger than {settings.max_upload_mb} MB")
    if delimiter == "tab":
        delimiter = "\t"
    elif delimiter in ("space", "whitespace"):
        delimiter = None

    def _list(s: str | None):
        return [x.strip() for x in s.split(",") if x.strip()] if s else None

    hh = None if has_header in (None, "", "auto") else has_header.lower() in ("1", "true", "yes")
    if user.get("guest"):
        # count what this upload would add before writing anything
        guest_check(settings, db, user, points_total=store.count_points() + services.count_points_in_upload(file.filename or "upload", data, delimiter=delimiter,
                                                                                                       mapping=json.loads(mapping) if mapping else None, has_header=hh))
    try:
        res = services.import_upload(
            store, file.filename or "upload", data, kind=kind, layer=layer, delimiter=delimiter,
            mapping=json.loads(mapping) if mapping else None, has_header=hh,
            point_layers=_list(point_layers), feature_layers=_list(feature_layers),
            boundary_layers=_list(boundary_layers), void_layers=_list(void_layers), replace=replace,
        )
    except ServiceError as e:
        raise raise_service(e)
    except (ValueError, UnicodeDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"could not read file: {e}")
    db.log(project_id, user, 'import', 'file', file.filename or '', {'points': res['points_added'], 'lines': res['lines_added']})
    db.touch_project(project_id)
    return ImportResult(**res)


@router.get("/points.geojson")
def points_geojson(project_id: str, crs: str | None = Query(None), layer: str | None = None, limit: int | None = Query(None, ge=1),
                   p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(require_project("viewer"))):
    try:
        return JSONResponse(services.points_geojson(store, p.get("crs"), crs, layers=[layer] if layer else None, limit=limit))
    except ServiceError as e:
        raise raise_service(e)


@router.get("/points.bin")
def points_bin(project_id: str, crs: str | None = Query(None), layer: str | None = None, p: dict = Depends(get_project),
               store: ProjectStore = Depends(get_store), _: dict = Depends(require_project("viewer"))):
    """Compact binary positions + fids for the map (16 bytes per point)."""
    from fastapi.responses import Response

    try:
        data = services.points_binary(store, p.get("crs"), crs, layers=[layer] if layer else None)
    except ServiceError as e:
        raise raise_service(e)
    return Response(content=data, media_type="application/octet-stream")


@router.get("/points/nearest")
def nearest_point(project_id: str, x: float, y: float, radius: float = Query(5.0, gt=0), p: dict = Depends(get_project),
                  store: ProjectStore = Depends(get_store), _: dict = Depends(require_project("viewer"))):
    """Closest survey point to (x, y) within radius metres (used for clicking in big point clouds)."""
    r = store.nearest_point(x, y, radius)
    if r is None:
        raise HTTPException(status_code=404, detail="no point within radius")
    return r


@router.get("/points.csv")
def points_csv(project_id: str, fmt: str = "id,x,y,z,remark", p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(require_project("viewer"))):
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(services.export_points_csv(store, fmt=fmt), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{p["name"]}_points.csv"'})


@router.get("/points/layers")
def point_layers(project_id: str, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(require_project("viewer"))):
    return store.point_layers()


@router.delete("/points")
def delete_points(project_id: str, fids: str | None = Query(None, description="comma separated fids"), layer: str | None = None,
                  all: bool = Query(False), p: dict = Depends(get_project), store: ProjectStore = Depends(get_store),
                  db: AppDB = Depends(get_db), _: dict = Depends(require_project("editor"))):
    if fids:
        n = store.delete_points(fids=[int(x) for x in fids.split(",") if x.strip()])
    elif layer:
        n = store.delete_points(layer=layer)
    elif all:
        n = store.delete_points()
    else:
        raise HTTPException(status_code=400, detail="give fids, layer or all=true")
    db.touch_project(project_id)
    return {"deleted": n}


@router.get("/lines.geojson")
def lines_geojson(project_id: str, crs: str | None = None, kind: str | None = None, p: dict = Depends(get_project),
                  store: ProjectStore = Depends(get_store), _: dict = Depends(require_project("viewer"))):
    try:
        return JSONResponse(services.lines_geojson(store, p.get("crs"), crs, kind=kind))
    except ServiceError as e:
        raise raise_service(e)


@router.get("/lines/summary")
def lines_summary(project_id: str, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(require_project("viewer"))):
    return store.line_summary()


@router.patch("/lines/{fid}")
def update_line(project_id: str, fid: int, kind: str | None = None, layer: str | None = None, name: str | None = None,
                p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(require_project("editor"))):
    if kind is not None:
        kind = {"hole": "void", "breakline": "feature"}.get(kind, kind)
        if kind not in ("feature", "boundary", "void", "contour"):
            raise HTTPException(status_code=400, detail="kind must be feature|boundary|void|contour (or hole/breakline)")
    store.update_line(fid, kind=kind, layer=layer, name=name)
    return {"ok": True}


@router.delete("/lines")
def delete_lines(project_id: str, fids: str | None = Query(None), kind: str | None = None, source: str | None = None, all: bool = Query(False),
                 p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), db: AppDB = Depends(get_db), _: dict = Depends(require_project("editor"))):
    if fids:
        n = store.delete_lines(fids=[int(x) for x in fids.split(",") if x.strip()])
    elif kind or source:
        n = store.delete_lines(kind=kind, source=source)
    elif all:
        n = store.delete_lines()
    else:
        raise HTTPException(status_code=400, detail="give fids, kind or all=true")
    db.touch_project(project_id)
    return {"deleted": n}


@router.post("/lines")
def add_line(project_id: str, body: dict, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store),
             db: AppDB = Depends(get_db), _: dict = Depends(require_project("editor"))):
    """Add a feature/boundary/void line drawn in the browser: {kind, layer, name, coords: [[x,y,z],...]}."""
    import numpy as np

    kind = {"hole": "void", "breakline": "feature"}.get(body.get("kind", "feature"), body.get("kind", "feature"))
    if kind not in ("feature", "boundary", "void", "contour"):
        raise HTTPException(status_code=400, detail="kind must be feature|boundary|void|contour (or hole/breakline)")
    coords = np.asarray(body.get("coords") or [], float)
    if coords.ndim != 2 or len(coords) < 2:
        raise HTTPException(status_code=400, detail="coords must be a list of at least two [x,y(,z)]")
    n = store.add_lines([coords], kind, layer=body.get("layer") or kind.capitalize(), source=str(body.get("source") or "drawn"),
                        names=[body.get("name", "")])
    db.touch_project(project_id)
    return {"added": n, "summary": store.line_summary()}


# declared last: '/points/{fid}' would otherwise swallow '/points/layers' and '/points/nearest'
@router.get("/points/{fid}")
def point_detail(project_id: str, fid: int, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(require_project("viewer"))):
    r = store.point(fid)
    if r is None:
        raise HTTPException(status_code=404, detail="point not found")
    return r
