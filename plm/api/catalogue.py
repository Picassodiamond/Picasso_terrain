"""Asset catalogue: every TIN run and every design, with a WGS84 footprint, so terrain models and
designs collected over the years can be found by place, date and tag and cloned into new projects.

Entries are recorded when a TIN job finishes and when a design is created or rebased. Visibility
follows the project (owner, members, organisation, public), so an organisation only sees its own
assets unless a project is made public.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import shapely

from ..engine import crs as crsmod
from . import services
from .config import Settings
from .db import AppDB
from .deps import project_role
from .gpkg import ProjectStore


def footprint_wgs84(store: ProjectStore, run_id: int, crs_spec: str | None) -> tuple[dict | None, float | None, float | None, list | None]:
    """(GeoJSON polygon in lon/lat or None for local grids, centroid lon, centroid lat, project-CRS bounds)."""
    _, tin = services.load_tin(store, run_id)
    hull = tin.hull()
    b = list(tin.bounds())
    if hull.is_empty:
        return None, None, None, b
    if hull.geom_type == "MultiPolygon":
        hull = max(hull.geoms, key=lambda g: g.area)
    tol = max(b[2] - b[0], b[3] - b[1]) * 0.005
    hull = hull.simplify(tol, preserve_topology=True)
    try:
        if crsmod.resolve(crs_spec) is None:
            return None, None, None, b
        ring = np.asarray(hull.exterior.coords)[:, :2]
        lon, lat = crsmod.transform_xy(crs_spec, "EPSG:4326", ring[:, 0], ring[:, 1])
        coords = [[round(float(x), 6), round(float(y), 6)] for x, y in zip(lon, lat)]
        c = shapely.Polygon(coords).centroid
        return {"type": "Polygon", "coordinates": [coords]}, float(c.x), float(c.y), b
    except Exception:  # noqa: BLE001 - unknown CRS: keep the entry without a map footprint
        return None, None, None, b


def _spacing(points: int, bounds: list | None) -> float | None:
    if not points or not bounds:
        return None
    area = max(bounds[2] - bounds[0], 1e-9) * max(bounds[3] - bounds[1], 1e-9)
    return round(float(np.sqrt(area / points)), 2)


def record_tin(db: AppDB, project: dict, store: ProjectStore, run_id: int) -> dict | None:
    run = store.get_tin_run(run_id)
    if run is None:
        return None
    fp, lon, lat, bounds = footprint_wgs84(store, run_id, project.get("crs"))
    n_pts = int(run.get("stats", {}).get("input_points") or store.count_points())
    return db.catalogue_upsert({
        "project_id": project["id"], "kind": "tin", "ref_id": run_id,
        "name": f"{project['name']} - TIN {run_id}{(' - ' + run['name']) if run.get('name') else ''}", "module": "terrain",
        "crs": project.get("crs"), "footprint": fp, "bounds": bounds, "lon": lon, "lat": lat,
        "points": n_pts, "triangles": int(run["n_triangles"]), "z_min": run["z_range"][0], "z_max": run["z_range"][1],
        "spacing": _spacing(n_pts, bounds), "tags": list((project.get("settings") or {}).get("tags") or []),
    })


def record_design(db: AppDB, project: dict, store: ProjectStore, design: dict) -> dict | None:
    run_id = design.get("tin_run_id")
    fp = lon = lat = bounds = None
    tri = None
    if run_id is not None and store.get_tin_run(int(run_id)) is not None:
        try:
            fp, lon, lat, bounds = footprint_wgs84(store, int(run_id), project.get("crs"))
            tri = int(store.get_tin_run(int(run_id))["n_triangles"])  # type: ignore[index]
        except Exception:  # noqa: BLE001
            pass
    return db.catalogue_upsert({
        "project_id": project["id"], "kind": "design", "ref_id": design["id"],
        "name": f"{project['name']} - {design['name']}", "module": design["module"], "crs": project.get("crs"),
        "footprint": fp, "bounds": bounds, "lon": lon, "lat": lat, "points": None, "triangles": tri, "z_min": None, "z_max": None,
        "spacing": None, "tags": list((project.get("settings") or {}).get("tags") or []) + [design["module"]],
    })


def list_items(db: AppDB, user: dict, kind: str | None = None, q: str | None = None, bbox: list[float] | None = None,
               tag: str | None = None) -> list[dict]:
    """Catalogue entries the user may see, newest first. bbox = [west, south, east, north] in lon/lat."""
    out = []
    roles: dict[str, str | None] = {}
    projects: dict[str, dict | None] = {}
    ql = (q or "").strip().lower()
    for it in db.catalogue_items(kind):
        pid = it["project_id"]
        if pid not in projects:
            projects[pid] = db.get_project(pid)
            roles[pid] = project_role(db, projects[pid], user) if projects[pid] else None
        p = projects[pid]
        if p is None or roles[pid] is None:
            continue
        if ql and ql not in (it.get("name") or "").lower() and not any(ql in str(t).lower() for t in it.get("tags") or []) and ql not in (p.get("description") or "").lower():
            continue
        if tag and tag not in (it.get("tags") or []):
            continue
        if bbox and (it.get("lon") is None or not (bbox[0] <= it["lon"] <= bbox[2] and bbox[1] <= it["lat"] <= bbox[3])):
            continue
        out.append(it | {"project_name": p["name"], "project_status": p.get("status", "active"), "my_role": roles[pid], "description": p.get("description") or ""})
    return out


def clone_into_project(settings: Settings, db: AppDB, item: dict, user: dict, name: str | None = None) -> dict:
    """Copy the asset's project file into a brand-new project owned by the caller."""
    src = db.get_project(item["project_id"])
    if src is None:
        raise services.ServiceError("source project no longer exists", 404)
    src_path = settings.project_gpkg(src["id"])
    if not src_path.exists():
        raise services.ServiceError("source project file is missing", 404)
    new_name = (name or f"{src['name']} (copy)").strip()
    owner = user["id"] if (user.get("authenticated") or user.get("guest")) else None
    p = db.create_project(new_name, src.get("crs") or "local", f"Cloned from catalogue: {item.get('name', '')}", owner_id=owner,
                          settings={"visibility": "private", "cloned_from": {"project_id": src["id"], "kind": item["kind"], "ref_id": item["ref_id"]},
                                    "tags": list(item.get("tags") or [])})
    dst_path = settings.project_gpkg(p["id"])
    Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dst_path)
    for suffix in ("-wal", "-shm"):  # never copy half-written journal files
        Path(str(dst_path) + suffix).unlink(missing_ok=True)
    if user.get("authenticated"):
        db.add_member(p["id"], user["id"], "owner")
    db.log(p["id"], user, "project_cloned", "project", src["id"], {"from": src["name"], "kind": item["kind"], "ref_id": item["ref_id"]})
    # the copy gets its own catalogue entries
    store = ProjectStore.open(dst_path)
    for run in store.tin_runs():
        try:
            record_tin(db, db.get_project(p["id"]), store, int(run["id"]))  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            pass
    return db.get_project(p["id"])  # type: ignore[return-value]
