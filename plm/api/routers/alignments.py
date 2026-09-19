from __future__ import annotations

import json

import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

from ...engine.alignment import IP, HorizontalAlignment
from ...engine.io.dxf_io import read_dxf
from ..auth import current_user, require_editor
from ..db import AppDB
from ..deps import get_db, get_project, get_store, raise_service
from ..gpkg import ProjectStore
from ..schemas import AlignmentIn, AlignmentOut
from .. import services
from ..services import ServiceError

router = APIRouter(prefix="/projects/{project_id}", tags=["alignments"])


def _decorate(out: dict, db: AppDB, project_id: str, alignment_id: int) -> dict:
    out["lock"] = db.get_lock(project_id, "alignment", alignment_id)
    vs = db.alignment_versions(project_id, alignment_id)
    out["version"] = vs[0]["version"] if vs else None
    return out


def _check_lock(db: AppDB, project_id: str, alignment_id: int, user: dict) -> None:
    lock = db.get_lock(project_id, "alignment", alignment_id)
    if lock and lock["user_id"] != user["id"]:
        raise HTTPException(status_code=409,
                            detail=f"alignment is being edited by {lock['username']} (lock expires {lock['expires']})")


@router.get("/alignments", response_model=list[AlignmentOut])
def list_alignments(project_id: str, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store),
                    db: AppDB = Depends(get_db), _: dict = Depends(current_user)):
    out = []
    for row in store.alignments():
        try:
            data = services.alignment_out(row, services.alignment_from_dict(row))
        except ValueError:
            continue
        out.append(AlignmentOut(**_decorate(data, db, project_id, row["id"])))
    return out


@router.post("/alignments", response_model=AlignmentOut, status_code=201)
def create_alignment(project_id: str, body: AlignmentIn, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store),
                     db: AppDB = Depends(get_db), user: dict = Depends(require_editor)):
    try:
        out = services.save_alignment(store, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.add_alignment_version(project_id, out["id"], body.model_dump(), user, "created")
    db.log(project_id, user, "alignment_created", "alignment", out["id"], {"name": body.name, "ips": len(body.ips)})
    db.touch_project(project_id)
    return AlignmentOut(**_decorate(out, db, project_id, out["id"]))


@router.post("/alignments/import", response_model=AlignmentOut, status_code=201)
async def import_alignment(project_id: str, file: UploadFile = File(...), name: str | None = Form(None),
                           start_chainage: float = Form(0.0), layer: str | None = Form(None),
                           p: dict = Depends(get_project), store: ProjectStore = Depends(get_store),
                           db: AppDB = Depends(get_db), user: dict = Depends(require_editor)):
    """Import a legacy `*_aln.csv` (count,startCh / N,X,Y,R), a GeoJSON LineString (optional
    per-vertex `radii` property) or a DXF LWPOLYLINE on layer H_ALIGN (default)."""
    data = await file.read()
    fname = file.filename or "alignment"
    ext = fname.lower().rsplit(".", 1)[-1]
    try:
        if ext in ("csv", "swr"):
            al = HorizontalAlignment.from_aln_csv(data.decode("utf-8-sig"))
            if start_chainage:
                al = HorizontalAlignment(al.ips, start_chainage)
        elif ext in ("geojson", "json"):
            gj = json.loads(data.decode("utf-8-sig"))
            feats = gj["features"] if gj.get("type") == "FeatureCollection" else [gj]
            ft = next(f for f in feats if (f.get("geometry") or {}).get("type") == "LineString")
            coords = np.asarray(ft["geometry"]["coordinates"], float)[:, :2]
            radii = (ft.get("properties") or {}).get("radii") or [0.0] * len(coords)
            al = HorizontalAlignment([IP(x, y, float(r)) for (x, y), r in zip(coords, radii)], start_chainage)
        elif ext == "dxf":
            import ezdxf

            tmp = store.path.parent / "_align.dxf"
            tmp.write_bytes(data)
            try:
                doc = ezdxf.readfile(str(tmp))
                want = (layer or "H_ALIGN").upper()
                pl = next((e for e in doc.modelspace().query("LWPOLYLINE") if e.dxf.layer.upper() == want), None)
                if pl is not None:
                    pts = list(pl.get_points("xyb"))
                    coords = np.array([[x, y] for x, y, _ in pts])
                    bulges = [b for _, _, b in pts][:-1]
                    al = HorizontalAlignment.from_polyline(coords, bulges, start_chainage)
                else:
                    d = read_dxf(tmp, line_layers=[layer or "H_ALIGN"])
                    if not d.lines:
                        raise HTTPException(status_code=400, detail="no polyline found on the alignment layer")
                    al = HorizontalAlignment.from_polyline(d.lines[0]["coords"][:, :2], None, start_chainage)
            finally:
                tmp.unlink(missing_ok=True)
        else:
            raise HTTPException(status_code=415, detail="unsupported alignment file type")
    except (ValueError, KeyError, StopIteration, UnicodeDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"could not read alignment: {e}")
    body = {"name": name or fname.rsplit(".", 1)[0], "start_chainage": al.start_chainage,
            "ips": [{"x": q.x, "y": q.y, "radius": q.radius, "label": q.label} for q in al.ips], "style": {}}
    out = services.save_alignment(store, body)
    db.add_alignment_version(project_id, out["id"], body, user, f"imported from {fname}")
    db.log(project_id, user, "alignment_imported", "alignment", out["id"], {"file": fname})
    db.touch_project(project_id)
    return AlignmentOut(**_decorate(out, db, project_id, out["id"]))


# ".csv" route before "/alignments/{alignment_id}" (greedy path parameter matching)
@router.get("/alignments/{alignment_id}.csv")
def alignment_csv(project_id: str, alignment_id: int, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store),
                  _: dict = Depends(current_user)):
    try:
        row, al = services.get_alignment(store, alignment_id)
    except ServiceError as e:
        raise raise_service(e)
    return PlainTextResponse(al.to_aln_csv(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{row["name"]}_aln.csv"'})


@router.get("/alignments/{alignment_id}", response_model=AlignmentOut)
def get_alignment(project_id: str, alignment_id: int, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store),
                  db: AppDB = Depends(get_db), _: dict = Depends(current_user)):
    try:
        row, al = services.get_alignment(store, alignment_id)
    except ServiceError as e:
        raise raise_service(e)
    return AlignmentOut(**_decorate(services.alignment_out(row, al), db, project_id, alignment_id))


@router.put("/alignments/{alignment_id}", response_model=AlignmentOut)
def update_alignment(project_id: str, alignment_id: int, body: AlignmentIn, note: str = "", p: dict = Depends(get_project),
                     store: ProjectStore = Depends(get_store), db: AppDB = Depends(get_db), user: dict = Depends(require_editor)):
    if store.get_alignment(alignment_id) is None:
        raise HTTPException(status_code=404, detail="alignment not found")
    _check_lock(db, project_id, alignment_id, user)
    try:
        out = services.save_alignment(store, body.model_dump(), alignment_id=alignment_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    v = db.add_alignment_version(project_id, alignment_id, body.model_dump(), user, note or "edited")
    if user.get("authenticated") and db.get_lock(project_id, "alignment", alignment_id):
        db.acquire_lock(project_id, "alignment", alignment_id, user)  # renew the editing turn
    db.log(project_id, user, "alignment_edited", "alignment", alignment_id, {"version": v["version"], "note": note, "ips": len(body.ips)})
    db.touch_project(project_id)
    return AlignmentOut(**_decorate(out, db, project_id, alignment_id))


@router.delete("/alignments/{alignment_id}", status_code=204)
def delete_alignment(project_id: str, alignment_id: int, p: dict = Depends(get_project), store: ProjectStore = Depends(get_store),
                     db: AppDB = Depends(get_db), user: dict = Depends(require_editor)):
    _check_lock(db, project_id, alignment_id, user)
    if not store.delete_alignment(alignment_id):
        raise HTTPException(status_code=404, detail="alignment not found")
    db.release_lock(project_id, "alignment", alignment_id, force=True)
    db.log(project_id, user, "alignment_deleted", "alignment", alignment_id)
    return None


@router.get("/alignments/{alignment_id}/geometry.geojson")
def alignment_geojson(project_id: str, alignment_id: int, crs: str | None = None, chainage_interval: float = Query(20.0, gt=0),
                      tick_length: float = Query(5.0, ge=0), p: dict = Depends(get_project), store: ProjectStore = Depends(get_store),
                      _: dict = Depends(current_user)):
    try:
        row, al = services.get_alignment(store, alignment_id)
        return JSONResponse(services.alignment_geojson(al, row["name"], p.get("crs"), crs, chainage_interval, tick_length))
    except ServiceError as e:
        raise raise_service(e)


@router.post("/alignments/preview", response_model=AlignmentOut)
def preview_alignment(project_id: str, body: AlignmentIn, p: dict = Depends(get_project), _: dict = Depends(current_user)):
    """Compute geometry without saving (live editing in the browser)."""
    try:
        al = services.alignment_from_dict(body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return AlignmentOut(**services.alignment_out({"id": 0, "name": body.name, "style": body.style}, al))
