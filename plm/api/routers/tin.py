from __future__ import annotations

import numpy as np
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from ..auth import current_user, require_editor
from ..db import AppDB
from ..deps import get_db, get_project, get_settings, get_store, raise_service
from ..gpkg import ProjectStore
from ..jobs import execute_job
from ..schemas import JobOut, ProfileRequest, TinParams, TinRunOut
from .. import services
from ..services import ServiceError

router = APIRouter(prefix="/projects/{project_id}", tags=["tin"])


def start_job(kind: str, params: dict, project_id: str, sync: bool, background: BackgroundTasks, db: AppDB, settings,
              user: dict | None = None) -> dict:
    job = db.create_job(project_id, kind, params)
    keys = ("interval", "major_every", "left", "right", "alignment_id", "run_id", "boundary_mode")
    db.log(project_id, user, f"{kind}_started", "job", job["id"], {k: v for k, v in params.items() if k in keys})
    if sync:
        out = execute_job(job["id"], settings, db) or job
        db.log(project_id, user, f"{kind}_{out['status']}", "job", job["id"], {"result_id": (out.get("result") or {}).get("id")})
        return out
    background.add_task(execute_job, job["id"], settings)
    return job


@router.post("/tin", response_model=JobOut, status_code=202)
def create_tin(project_id: str, body: TinParams, background: BackgroundTasks, p: dict = Depends(get_project),
               store: ProjectStore = Depends(get_store), db: AppDB = Depends(get_db), settings=Depends(get_settings), user: dict = Depends(require_editor)):
    n = store.count_points()
    if n < 3:
        raise HTTPException(status_code=400, detail="import at least three points first")
    sync = body.sync if body.sync is not None else n <= settings.sync_point_limit
    job = start_job("tin", body.model_dump(), project_id, sync, background, db, settings, user)
    if job["status"] == "error":
        raise HTTPException(status_code=400, detail=job.get("error"))
    return JobOut(**job)


@router.get("/tin", response_model=list[TinRunOut])
def list_runs(project_id: str, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    return [TinRunOut(**{k: v for k, v in r.items() if k != "issues"} | {"issues": r["issues"][:50]}) for r in store.tin_runs()]


@router.get("/tin/{run_id}", response_model=TinRunOut)
def get_run(project_id: str, run_id: int, issues_limit: int = Query(500, ge=0), p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    r = store.get_tin_run(run_id)
    if r is None:
        raise HTTPException(status_code=404, detail="TIN run not found")
    r["issues"] = r["issues"][:issues_limit]
    return TinRunOut(**r)


@router.delete("/tin/{run_id}", status_code=204)
def delete_run(project_id: str, run_id: int, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(require_editor)):
    if not store.delete_tin_run(run_id):
        raise HTTPException(status_code=404, detail="TIN run not found")
    return None


@router.get("/tin/{run_id}/mesh.bin")
def mesh_bin(project_id: str, run_id: int, crs: str | None = None, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    try:
        _, tin = services.load_tin(store, run_id)
        data = services.mesh_binary(tin, p.get("crs"), crs)
    except ServiceError as e:
        raise raise_service(e)
    return Response(content=data, media_type="application/octet-stream", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/tin/{run_id}/mesh.json")
def mesh_json(project_id: str, run_id: int, crs: str | None = None, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    try:
        _, tin = services.load_tin(store, run_id)
        return JSONResponse(services.mesh_json(tin, p.get("crs"), crs))
    except ServiceError as e:
        raise raise_service(e)


@router.get("/tin/{run_id}/hull.geojson")
def hull(project_id: str, run_id: int, crs: str | None = None, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    try:
        _, tin = services.load_tin(store, run_id)
        return JSONResponse(services.hull_geojson(tin, p.get("crs"), crs))
    except ServiceError as e:
        raise raise_service(e)


@router.get("/tin/{run_id}/issues.geojson")
def issues_geojson(project_id: str, run_id: int, crs: str | None = None, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    r = store.get_tin_run(run_id)
    if r is None:
        raise HTTPException(status_code=404, detail="TIN run not found")
    feats = []
    if r["issues"]:
        xy = np.array([[i["x"], i["y"]] for i in r["issues"]])
        try:
            txy = services._transform(xy, p.get("crs"), crs)
        except ServiceError as e:
            raise raise_service(e)
        for i, (x, y) in zip(r["issues"], txy):
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(x), float(y)]},
                          "properties": {"kind": i["kind"], "message": i["message"]}})
    return {"type": "FeatureCollection", "features": feats}


@router.get("/tin/{run_id}/elevation")
def elevation(project_id: str, run_id: int, x: float, y: float, crs: str | None = None, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    """Spot height at (x, y). Pass crs=EPSG:4326 to give lon/lat."""
    try:
        _, tin = services.load_tin(store, run_id)
        if crs and crs != p.get("crs"):
            xy = services._transform(np.array([[x, y]]), crs, p.get("crs"))
            x, y = float(xy[0, 0]), float(xy[0, 1])
        z = tin.elevation_at([x], [y])[0]
    except ServiceError as e:
        raise raise_service(e)
    return {"x": x, "y": y, "z": None if np.isnan(z) else float(z), "inside": not bool(np.isnan(z))}


@router.post("/tin/{run_id}/profile")
def adhoc_profile(project_id: str, run_id: int, body: ProfileRequest, crs: str | None = None, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), _: dict = Depends(current_user)):
    """Ground line along an arbitrary polyline (quick profile tool)."""
    try:
        _, tin = services.load_tin(store, run_id)
        coords = np.asarray(body.coords, float)[:, :2]
        if crs and crs != p.get("crs"):
            coords = services._transform(coords, crs, p.get("crs"))
        s = tin.sample_line(coords)
    except ServiceError as e:
        raise raise_service(e)
    return {"distance": np.round(s.distance, 4).tolist(), "z": [None if np.isnan(v) else round(float(v), 4) for v in s.z],
            "xy": np.round(s.xy, 4).tolist(), "length": float(s.distance[-1]) if len(s) else 0.0}
