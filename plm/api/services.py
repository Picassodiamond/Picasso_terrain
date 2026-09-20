"""Orchestration between HTTP layer, project store and the geometry engine."""
from __future__ import annotations

import io
import json
import math
import os
import struct
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from shapely.geometry import Polygon

from ..engine import REJECT_REASONS, Constraint, build_tin, contour_labels, contour_tin, suggest_constraints
from ..engine import crs as crsmod
from ..engine.alignment import IP, HorizontalAlignment, format_chainage
from ..engine.contour import ContourLine
from ..engine.io.csv_io import read_points_csv, write_points_csv
from ..engine.io.dxf_export import DxfStyle, write_dxf
from ..engine.io.dxf_io import read_dxf
from ..engine.io.geojson_io import read_geojson
from ..engine.points import PointSet
from ..engine.sections import (
    cross_sections_to_csv,
    generate_cross_sections,
    generate_profile,
    profile_summary,
    profile_to_csv,
)
from ..engine.tin import TIN
from .gpkg import ProjectStore


class ServiceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ coordinate output helpers
def _transform(xy: np.ndarray, src: str | None, dst: str | None) -> np.ndarray:
    """Transform (k,2|3) coordinates when dst is given and differs from src."""
    if not dst or dst == src or dst.lower() == "project":
        return xy
    xy = np.asarray(xy, float)
    try:
        x, y = crsmod.transform_xy(src, dst, xy[:, 0], xy[:, 1])
    except ValueError as e:
        raise ServiceError(str(e), 400)
    out = xy.copy()
    out[:, 0], out[:, 1] = x, y
    return out


def _rounded(arr, nd: int) -> list:
    a = np.asarray(arr, float)
    a = np.round(a, nd)
    return [[None if (isinstance(v, float) and math.isnan(v)) else v for v in row] for row in a.tolist()] if a.ndim == 2 else a.tolist()


def _fc(features: list[dict], crs: str | None) -> dict:
    fc: dict[str, Any] = {"type": "FeatureCollection", "features": features}
    if crs and crs.lower() != "local":
        fc["crs"] = {"type": "name", "properties": {"name": crs}}
    return fc


# ------------------------------------------------------------------ import
_CSV_EXT = {".csv", ".txt", ".xyz", ".pts", ".prn", ".dat", ".tsv"}


def import_upload(store: ProjectStore, filename: str, data: bytes, *, kind: str = "auto",
                  layer: str | None = None, delimiter: str | None = "auto",
                  mapping: dict | None = None, has_header: bool | None = None,
                  point_layers: Sequence[str] | None = None, feature_layers: Sequence[str] | None = None,
                  boundary_layers: Sequence[str] | None = None, void_layers: Sequence[str] | None = None,
                  replace: bool = False) -> dict:
    ext = Path(filename).suffix.lower()
    warnings: list[str] = []
    points_added = 0
    lines_added: dict[str, int] = {}
    fmt = ext.lstrip(".") or "csv"

    if replace:
        store.delete_points()
        store.delete_lines()

    if ext in (".geojson", ".json"):
        gj = read_geojson(json.loads(data.decode("utf-8-sig")))
        if len(gj.points):
            ps = gj.points
            if layer:
                ps.layers = [layer] * len(ps)
            points_added = store.add_points(ps, source=filename)
        for k in ("feature", "boundary", "void", "contour"):
            lines = [ln["coords"] for ln in gj.lines if ln["kind"] == k]
            names = [ln["props"].get("name", "") for ln in gj.lines if ln["kind"] == k]
            if lines:
                lines_added[k] = store.add_lines(lines, k, layer=layer or k.capitalize(), source=filename, names=names)
        for k in ("boundary", "void"):
            polys = gj.polygons_of_kind(k)
            if polys:
                coords = [np.asarray(p.exterior.coords) for p in polys]
                lines_added[k] = lines_added.get(k, 0) + store.add_lines(coords, k, layer=layer or k.capitalize(), source=filename)
    elif ext == ".dxf":
        tmp = store.path.parent / f"_upload{ext}"
        tmp.write_bytes(data)
        try:
            d = read_dxf(tmp, point_layers=point_layers)
        finally:
            tmp.unlink(missing_ok=True)
        if len(d.points):
            points_added = store.add_points(d.points, source=filename)
        fl = {s.upper() for s in (feature_layers or [])}
        bl = {s.upper() for s in (boundary_layers or [])} or {"BOUNDARY"}
        vl = {s.upper() for s in (void_layers or [])} or {"VOID", "VOIDS"}
        groups: dict[str, list] = {}
        for ln in d.lines:
            lay = ln["layer"].upper()
            if lay in bl:
                k = "boundary"
            elif lay in vl:
                k = "void"
            elif ln["kind"] == "contour":
                k = "contour"
            elif fl and lay in fl:
                k = "feature"
            elif not fl and ln["kind"] == "feature":
                k = "feature"
            else:
                continue
            groups.setdefault((k, ln["layer"]), []).append(ln["coords"])
        for (k, lay), coords in groups.items():
            lines_added[k] = lines_added.get(k, 0) + store.add_lines(coords, k, layer=lay, source=filename)
        if d.skipped:
            warnings.append(f"skipped entity types: {d.skipped}")
    elif ext in _CSV_EXT or kind == "csv":
        text = data.decode("utf-8-sig", errors="replace")
        ps = read_points_csv(text, delimiter=delimiter, mapping=mapping, has_header=has_header)
        if not len(ps):
            raise ServiceError("no points could be parsed from the file", 400)
        if layer:
            ps.layers = [layer] * len(ps)
        else:
            ps.layers = ["Points" if not lay else lay for lay in ps.layers]
        points_added = store.add_points(ps, source=filename)
    elif ext == ".xlsx":
        ps = _read_xlsx_points(data, mapping)
        if layer:
            ps.layers = [layer] * len(ps)
        points_added = store.add_points(ps, source=filename)
    else:
        raise ServiceError(f"unsupported file type {ext!r}", 415)

    return {"filename": filename, "format": fmt, "points_added": points_added,
            "lines_added": lines_added, "warnings": warnings, "summary": store.summary()}


def count_points_in_upload(filename: str, data: bytes, *, delimiter=None, mapping=None, has_header=None) -> int:
    """How many points an upload would add (used for guest quotas before writing anything)."""
    ext = Path(filename).suffix.lower()
    try:
        if ext in (".geojson", ".json"):
            gj = read_geojson(data.decode("utf-8", errors="replace"))
            return len(gj.points)
        if ext == ".dxf":
            return max(0, data.count(b"\nPOINT\n") + data.count(b"\nINSERT\n"))
        if ext == ".xlsx":
            return len(_read_xlsx_points(data, mapping))
        return len(read_points_csv(data.decode("utf-8", errors="replace"), delimiter=delimiter, mapping=mapping, has_header=has_header))
    except Exception:  # noqa: BLE001 - the real import reports parse errors properly
        return 0


def _read_xlsx_points(data: bytes, mapping: dict | None) -> PointSet:
    try:
        import openpyxl
    except ImportError as e:  # pragma: no cover
        raise ServiceError("openpyxl is not installed on the server", 500) from e
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    rows = []
    for r in ws.iter_rows(values_only=True):
        if r is None or all(v is None for v in r):
            continue
        rows.append(",".join("" if v is None else str(v) for v in r))
    return read_points_csv("\n".join(rows), delimiter=",", mapping=mapping)


# ------------------------------------------------------------------ TIN
_STORE_KIND = {"boundary": "boundary", "hole": "void", "void": "void", "breakline": "feature", "feature": "feature", "contour": "contour"}
_ENGINE_KIND = {"boundary": "boundary", "void": "hole", "feature": "breakline", "contour": "breakline"}


def _boundary_geoms(store: ProjectStore, kind: str) -> list[Polygon]:
    out = []
    for ln in store.lines(kind=kind):
        c = ln["coords"][:, :2]
        if len(c) >= 3:
            p = Polygon(c)
            if not p.is_valid:
                p = p.buffer(0)
            if not p.is_empty:
                out.append(p)
    return out


def _stored_constraints(store: ProjectStore, params: dict) -> list[Constraint]:
    """Project lines -> engine constraints, honouring the use_* switches and layer filters."""
    items: list[Constraint] = []
    if params.get("use_features", True):
        for ln in store.lines(kind="feature", layers=params.get("feature_layers") or None):
            items.append(Constraint("breakline", ln["coords"], ln["name"], ln.get("source", ""), ln["fid"]))
        for ln in store.lines(kind="contour"):
            items.append(Constraint("breakline", ln["coords"], ln["name"], ln.get("source", ""), ln["fid"]))
    if params.get("use_boundary", True):
        for ln in store.lines(kind="boundary"):
            items.append(Constraint("boundary", ln["coords"], ln["name"], ln.get("source", ""), ln["fid"]))
    if params.get("use_voids", True):
        for ln in store.lines(kind="void"):
            items.append(Constraint("hole", ln["coords"], ln["name"], ln.get("source", ""), ln["fid"]))
    return items


def _detect(store: ProjectStore, pts: PointSet, detect: dict | None):
    d = dict(detect or {})
    d.pop("point_layers", None)
    return suggest_constraints(pts.xyz, edge_factor=float(d.get("edge_factor") or 3.0), max_edge=d.get("max_edge"),
                               min_hole_area=d.get("min_hole_area"), min_hole_triangles=int(d.get("min_hole_triangles") or 3),
                               detect_holes=bool(d.get("detect_holes", True)))


def run_tin(store: ProjectStore, params: dict, progress=None) -> dict:
    """points -> (automatic constraints) -> constrained TIN -> validated triangles.

    constraint_mode
      auto    when no boundary from the user exists (drawn / imported / accepted), detect the data
              limit and gaps, store them as lines with source 'auto' (replacing older auto lines)
              and use them; otherwise identical to manual
      semi    the stored constraints, i.e. whatever the user accepted after reviewing suggestions
      manual  the stored constraints only
    """
    _, xyz = store.points_xyz(layers=params.get("point_layers") or None)
    pts = PointSet(xyz)
    if len(pts) < 3:
        raise ServiceError("at least three points are required (import points first)", 400)
    mode = params.get("constraint_mode", "auto")
    items = _stored_constraints(store, params)
    detection: dict | None = None
    if mode == "auto" and params.get("use_boundary", True):
        user_boundary = any(c.kind == "boundary" and c.source != "auto" for c in items)
        if not user_boundary:
            if progress:
                progress(0.05, "detecting constraints")
            det = _detect(store, pts, params.get("detect"))
            detection = dict(det.stats)
            store.delete_lines(source="auto")
            items = [c for c in items if c.source != "auto"]
            for sug in det.suggestions:
                if sug.kind == "hole" and not params.get("use_voids", True):
                    continue
                store.add_lines([sug.coords], _STORE_KIND[sug.kind], layer="Auto", source="auto", names=[sug.reason[:120]])
                items.append(sug.to_constraint(source="auto", name=sug.reason[:120]))
    if progress:
        progress(0.1, "triangulating")
    res = build_tin(
        pts,
        constraints=items,
        dedupe_tol=float(params.get("dedupe_tol", 0.001)),
        drop_zero_z=bool(params.get("drop_zero_z", False)),
        boundary_mode=params.get("boundary_mode", "inside"),
        max_edge_length=params.get("max_edge_length"),
        max_edge_factor=params.get("max_edge_factor"),
        min_angle_deg=float(params.get("min_angle_deg") or 0.0),
        keep_rejected=bool(params.get("keep_rejected", True)),
    )
    if progress:
        progress(0.8, "saving")
    stats = dict(res.stats)
    if detection is not None:
        stats["detection"] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in detection.items()}
    stats["constraint_mode"] = mode
    stored = {k: v for k, v in params.items() if k != "sync"}
    run_id = store.save_tin(res.tin, stored, stats, res.issues, res.node_source, name=params.get("name", ""), rejected=res.rejected)
    run = store.get_tin_run(run_id)
    assert run is not None
    return run


def detect_constraints(store: ProjectStore, params: dict, project_crs: str | None, out_crs: str | None) -> dict:
    """Suggestions as GeoJSON (closed LineStrings) - nothing is stored."""
    _, xyz = store.points_xyz(layers=params.get("point_layers") or None)
    pts = PointSet(xyz)
    if len(pts) < 3:
        raise ServiceError("at least three points are required (import points first)", 400)
    det = _detect(store, pts, params)
    feats = []
    for i, sug in enumerate(det.suggestions):
        xy = _transform(sug.coords[:, :2], project_crs, out_crs)
        coords = np.column_stack([xy, sug.coords[:, 2]])
        feats.append({"type": "Feature", "id": i,
                      "geometry": {"type": "LineString", "coordinates": _rounded(coords, 6)},
                      "properties": {"index": i, "kind": sug.kind, "reason": sug.reason, "confidence": round(float(sug.confidence), 3),
                                     "stats": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in sug.stats.items()},
                                     "closed": True}})
    fc = _fc(feats, out_crs or project_crs)
    fc["stats"] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in det.stats.items()}
    return fc


def accept_constraints(store: ProjectStore, body: dict) -> dict:
    """Store reviewed constraints as project lines."""
    feats = body.get("features") or []
    if body.get("replace_auto"):
        store.delete_lines(source="auto")
    added = 0
    for f in feats:
        kind = _STORE_KIND.get(f.get("kind", "feature"))
        if kind is None:
            raise ServiceError(f"unknown constraint kind {f.get('kind')!r}", 400)
        coords = np.asarray(f.get("coords") or [], float)
        if coords.ndim != 2 or len(coords) < 2:
            raise ServiceError("coords must be a list of at least two [x, y(, z)]", 400)
        if kind in ("boundary", "void") and not np.allclose(coords[0, :2], coords[-1, :2]):
            coords = np.vstack([coords, coords[:1]])
        added += store.add_lines([coords], kind, layer=f.get("layer") or kind.capitalize(), source=str(body.get("source") or "accepted"),
                                 names=[f.get("name", "")])
    return {"added": added, "summary": store.line_summary()}


def rejected_geojson(store: ProjectStore, run_id: int, project_crs: str | None, out_crs: str | None, limit: int = 50000) -> dict:
    try:
        xyz, reason = store.load_rejected(run_id)
    except KeyError:
        raise ServiceError(f"TIN run {run_id} not found", 404)
    n = min(len(reason), limit)
    feats = []
    if n:
        flat = _transform(xyz[:n].reshape(-1, 3)[:, :2], project_crs, out_crs).reshape(n, 3, 2)
        for i in range(n):
            ring = np.column_stack([flat[i], xyz[i, :, 2]])
            ring = np.vstack([ring, ring[:1]])
            feats.append({"type": "Feature", "id": i, "geometry": {"type": "Polygon", "coordinates": [_rounded(ring, 4)]},
                          "properties": {"reason": REJECT_REASONS.get(int(reason[i]), "unknown")}})
    fc = _fc(feats, out_crs or project_crs)
    fc["total"] = int(len(reason))
    fc["counts"] = {name: int((reason == code).sum()) for code, name in REJECT_REASONS.items() if (reason == code).any()}
    return fc


# ------------------------------------------------------------------ TIN cache
# A TIN run is immutable once saved, so the last few loaded TINs are kept per process (their
# topology and grid index are expensive to rebuild for every spot height / profile request).
# Bounded by PLM_TIN_CACHE entries (default 2); evicted on delete.
TIN_CACHE_SIZE = int(os.environ.get("PLM_TIN_CACHE", "2"))
_TIN_CACHE: "OrderedDict[tuple[str, int], TIN]" = OrderedDict()
_TIN_LOCK = threading.Lock()


def evict_tin(store_path, run_id: int | None = None) -> None:
    """Drop cached TINs of a project (one run, or all runs when run_id is None)."""
    key_path = str(store_path)
    with _TIN_LOCK:
        for k in [k for k in _TIN_CACHE if k[0] == key_path and (run_id is None or k[1] == int(run_id))]:
            _TIN_CACHE.pop(k, None)


def tin_cache_info() -> dict:
    with _TIN_LOCK:
        return {"size": len(_TIN_CACHE), "limit": TIN_CACHE_SIZE, "keys": [f"{Path(k[0]).parent.name}:{k[1]}" for k in _TIN_CACHE]}


def load_tin(store: ProjectStore, run_id: int | None) -> tuple[int, TIN]:
    rid = run_id if run_id is not None else store.latest_tin_run_id()
    if rid is None:
        raise ServiceError("no TIN has been built for this project yet", 404)
    key = (str(store.path), int(rid))
    with _TIN_LOCK:
        tin = _TIN_CACHE.get(key)
        if tin is not None:
            _TIN_CACHE.move_to_end(key)
            return int(rid), tin
    try:
        tin, _ = store.load_tin(int(rid))
    except KeyError:
        raise ServiceError(f"TIN run {rid} not found", 404)
    if TIN_CACHE_SIZE > 0:
        with _TIN_LOCK:
            _TIN_CACHE[key] = tin
            _TIN_CACHE.move_to_end(key)
            while len(_TIN_CACHE) > TIN_CACHE_SIZE:
                _TIN_CACHE.popitem(last=False)
    return int(rid), tin


def _mesh_bytes(nodes: np.ndarray, triangles: np.ndarray, project_crs: str | None, out_crs: str | None) -> bytes:
    """Binary mesh: header + positions + indices.

    header: b'PLMM', uint32 version(1), uint32 n_nodes, uint32 n_tris, uint32 dtype(0=f32 relative,
    1=f64 absolute), float64 origin[3]; then positions (n*3) and uint32 indices (m*3).
    """
    if out_crs and out_crs.lower() not in ("project", "") and out_crs != project_crs:
        xy = _transform(nodes[:, :2], project_crs, out_crs)
        pos = np.column_stack([xy, nodes[:, 2]]).astype(np.float64)
        dtype = 1
        origin = np.zeros(3)
    else:
        origin = nodes.min(axis=0) if len(nodes) else np.zeros(3)
        pos = (nodes - origin).astype(np.float32)
        dtype = 0
    head = b"PLMM" + struct.pack("<IIII", 1, len(nodes), len(triangles), dtype) + struct.pack("<3d", *origin)
    return head + pos.tobytes() + np.ascontiguousarray(triangles, dtype=np.uint32).tobytes()


def mesh_binary(tin: TIN, project_crs: str | None, out_crs: str | None) -> bytes:
    return _mesh_bytes(tin.nodes, tin.triangles, project_crs, out_crs)


# ------------------------------------------------------------------ mesh tiles
TILE_TRIANGLES = int(os.environ.get("PLM_TILE_TRIANGLES", "100000"))


def tin_tiles(tin: TIN) -> dict:
    """Split the triangles of a big TIN into an N x N grid of tiles (by centroid) so the browser can
    load them progressively and Cesium can frustum-cull them. Cached on the TIN object."""
    cached = getattr(tin, "_tiles", None)
    if cached is not None:
        return cached
    m = tin.n_triangles
    n = max(1, int(np.ceil(np.sqrt(m / TILE_TRIANGLES))))
    x0, y0, x1, y1 = tin.bounds()
    w = max(x1 - x0, 1e-9)
    h = max(y1 - y0, 1e-9)
    c = tin.centroids()
    ti = np.clip(((c[:, 0] - x0) / w * n).astype(np.int64), 0, n - 1)
    tj = np.clip(((c[:, 1] - y0) / h * n).astype(np.int64), 0, n - 1)
    tile_id = tj * n + ti
    tiles = []
    for tid in np.unique(tile_id):
        mask = tile_id == tid
        p = tin.nodes[tin.triangles[mask], :2].reshape(-1, 2)
        tiles.append({"i": int(tid % n), "j": int(tid // n), "triangles": int(mask.sum()),
                      "bounds": [float(p[:, 0].min()), float(p[:, 1].min()), float(p[:, 0].max()), float(p[:, 1].max())]})
    out = {"n": n, "tile_triangles": TILE_TRIANGLES, "n_triangles": m, "n_nodes": tin.n_nodes,
           "bounds": [x0, y0, x1, y1], "z_range": list(tin.z_range()), "tiles": tiles, "_ids": tile_id}
    tin._tiles = out  # type: ignore[attr-defined]
    return out


def tile_mesh_binary(tin: TIN, i: int, j: int, project_crs: str | None, out_crs: str | None) -> bytes:
    info = tin_tiles(tin)
    n = info["n"]
    if not (0 <= i < n and 0 <= j < n):
        raise ServiceError("tile out of range", 404)
    mask = info["_ids"] == j * n + i
    if not mask.any():
        raise ServiceError("empty tile", 404)
    tris = tin.triangles[mask]
    used, inv = np.unique(tris.reshape(-1), return_inverse=True)
    return _mesh_bytes(tin.nodes[used], np.asarray(inv).reshape(tris.shape), project_crs, out_crs)


def tiles_public(info: dict) -> dict:
    return {k: v for k, v in info.items() if not k.startswith("_")}


# ------------------------------------------------------------------ binary points
def points_binary(store: ProjectStore, project_crs: str | None, out_crs: str | None, layers=None) -> bytes:
    """Binary points: b'PLMP', uint32 version(1), uint32 n, uint32 dtype(0=f32 relative, 1=f64 absolute),
    float64 origin[3]; then positions (n*3) and uint32 fids (n). 16 bytes per point instead of
    about 230 in GeoJSON; ids / remarks are fetched on click via /points/{fid}."""
    fids, xyz = store.points_xyz(layers=layers)
    if out_crs and out_crs.lower() not in ("project", "") and out_crs != project_crs:
        xy = _transform(xyz[:, :2], project_crs, out_crs)
        pos = np.column_stack([xy, xyz[:, 2]]).astype(np.float64)
        dtype = 1
        origin = np.zeros(3)
    else:
        origin = xyz.min(axis=0) if len(xyz) else np.zeros(3)
        pos = (xyz - origin).astype(np.float32)
        dtype = 0
    head = b"PLMP" + struct.pack("<III", 1, len(fids), dtype) + struct.pack("<3d", *origin)
    return head + pos.tobytes() + fids.astype(np.uint32).tobytes()


def mesh_json(tin: TIN, project_crs: str | None, out_crs: str | None) -> dict:
    nodes = tin.nodes
    if out_crs and out_crs != project_crs and out_crs.lower() != "project":
        xy = _transform(nodes[:, :2], project_crs, out_crs)
        pos = np.column_stack([xy, nodes[:, 2]])
    else:
        pos = nodes
    return {"n_nodes": tin.n_nodes, "n_triangles": tin.n_triangles, "positions": _rounded(pos, 6),
            "indices": tin.triangles.astype(int).tolist(), "bounds": list(tin.bounds()), "z_range": list(tin.z_range())}


def hull_geojson(tin: TIN, project_crs: str | None, out_crs: str | None) -> dict:
    hull = tin.hull()
    geoms = list(hull.geoms) if hasattr(hull, "geoms") else [hull]
    feats = []
    for g in geoms:
        rings = [np.asarray(g.exterior.coords)] + [np.asarray(r.coords) for r in g.interiors]
        rings = [_rounded(_transform(r[:, :2], project_crs, out_crs), 6) for r in rings]
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": rings}, "properties": {}})
    return _fc(feats, out_crs or project_crs)


def points_geojson(store: ProjectStore, project_crs: str | None, out_crs: str | None, layers=None, limit: int | None = None) -> dict:
    ps, fids = store.points(layers=layers, with_fid=True)
    n = len(ps) if limit is None else min(len(ps), limit)
    xy = _transform(ps.xyz[:n, :2], project_crs, out_crs)
    feats = []
    for i in range(n):
        feats.append({
            "type": "Feature", "id": int(fids[i]),
            "geometry": {"type": "Point", "coordinates": [round(float(xy[i, 0]), 8), round(float(xy[i, 1]), 8), round(float(ps.z[i]), 3)]},
            "properties": {"fid": int(fids[i]), "id": ps.ids[i], "z": round(float(ps.z[i]), 3), "remark": ps.remarks[i],
                           "layer": ps.layers[i], "x": round(float(ps.x[i]), 3), "y": round(float(ps.y[i]), 3)},
        })
    return _fc(feats, out_crs or project_crs)


def lines_geojson(store: ProjectStore, project_crs: str | None, out_crs: str | None, kind: str | None = None) -> dict:
    feats = []
    for ln in store.lines(kind=kind):
        c = ln["coords"]
        xy = _transform(c[:, :2], project_crs, out_crs)
        coords = np.column_stack([xy, c[:, 2]])
        feats.append({"type": "Feature", "id": ln["fid"],
                      "geometry": {"type": "LineString", "coordinates": _rounded(coords, 6)},
                      "properties": {"fid": ln["fid"], "kind": ln["kind"], "layer": ln["layer"], "name": ln["name"], "closed": ln["closed"],
                                     "source": ln.get("source", "")}})
    return _fc(feats, out_crs or project_crs)


# ------------------------------------------------------------------ contours
def make_contours(store: ProjectStore, params: dict, progress=None) -> dict:
    run_id, tin = load_tin(store, params.get("run_id"))
    if progress:
        progress(0.1, "contouring")
    lines = contour_tin(
        tin, float(params["interval"]), int(params.get("major_every", 5)), base=float(params.get("base", 0.0)),
        min_spacing=float(params.get("min_spacing", 0.0)), smoothing=params.get("smoothing", "none"),
        smooth_iterations=int(params.get("smooth_iterations", 2)), min_length=float(params.get("min_length", 0.0)),
    )
    if progress:
        progress(0.8, "saving")
    style = params.get("style") or {}
    stored = {k: v for k, v in params.items() if k not in ("sync", "style")}
    stored["run_id"] = run_id
    set_id = store.save_contours(run_id, stored, style, lines, name=params.get("name", ""))
    s = store.get_contour_set(set_id)
    assert s is not None
    return s


def contours_geojson(store: ProjectStore, set_id: int, project_crs: str | None, out_crs: str | None,
                     major_only: bool = False, ndigits: int = 6) -> dict:
    lines = store.load_contours(set_id, major_only=major_only)
    feats = []
    for i, cl in enumerate(lines):
        xy = _transform(cl.coords, project_crs, out_crs)
        coords = np.column_stack([xy, np.full(len(xy), cl.level)])
        feats.append({"type": "Feature", "id": i,
                      "geometry": {"type": "LineString", "coordinates": _rounded(coords, ndigits)},
                      "properties": {"level": cl.level, "major": cl.is_major, "closed": cl.closed,
                                     "label": f"{cl.level:g}"}})
    return _fc(feats, out_crs or project_crs)


def contour_labels_geojson(store: ProjectStore, set_id: int, project_crs: str | None, out_crs: str | None,
                           every: float | None = None) -> dict:
    s = store.get_contour_set(set_id)
    if s is None:
        raise ServiceError("contour set not found", 404)
    st = s.get("style") or {}
    lines = store.load_contours(set_id)
    labels = contour_labels(
        lines, every_m=float(every or st.get("label_every", 100.0)), fmt=st.get("label_format", "{z:.2f}"),
        prefix=st.get("label_prefix", ""), suffix=st.get("label_suffix", ""), major_only=bool(st.get("label_major_only", True)),
    )
    feats = []
    if labels:
        xy = np.array([[lb.x, lb.y] for lb in labels])
        txy = _transform(xy, project_crs, out_crs)
        for lb, (x, y) in zip(labels, txy):
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(float(x), 8), round(float(y), 8), lb.level]},
                          "properties": {"level": lb.level, "text": lb.text, "angle": round(lb.angle_deg, 2)}})
    return _fc(feats, out_crs or project_crs)


# ------------------------------------------------------------------ alignments
def alignment_from_dict(d: dict) -> HorizontalAlignment:
    ips = [IP(float(p["x"]), float(p["y"]), float(p.get("radius", 0) or 0), str(p.get("label", "")), float(p.get("transition", 0) or 0)) for p in d["ips"]]
    return HorizontalAlignment(ips, float(d.get("start_chainage", 0.0)), float(d.get("min_radius", 4.0)))


def alignment_out(store_row: dict, al: HorizontalAlignment) -> dict:
    return {
        "id": store_row["id"], "name": store_row["name"], "start_chainage": al.start_chainage,
        "end_chainage": al.end_chainage, "length": al.length, "valid": al.is_valid,
        "ips": [{"x": p.x, "y": p.y, "radius": p.radius, "label": p.label, "transition": p.transition} for p in al.ips],
        "geometry": [g.__dict__ | {"bc": list(g.bc), "ec": list(g.ec), "centre": list(g.centre) if g.centre else None} for g in al.geometry],
        "elements": [{"kind": e.kind, "start_chainage": e.start_chainage, "end_chainage": e.end_chainage,
                      "start": list(e.start), "end": list(e.end), "radius": e.radius,
                      "centre": list(e.centre) if e.centre else None, "deflection": e.deflection,
                      "length": e.length} for e in al.elements],
        "key_points": al.key_points(),
        "issues": [i.__dict__ for i in al.issues],
        "style": store_row.get("style") or {},
        "created": store_row.get("created"), "updated": store_row.get("updated"),
    }


def save_alignment(store: ProjectStore, body: dict, alignment_id: int | None = None) -> dict:
    al = alignment_from_dict(body)
    for k, p in enumerate(al.ips):
        if not p.label:
            p.label = str(k)
    dense, _ = al.densify(1.0, 2.0)
    aid = store.save_alignment(
        body.get("name", "Alignment"), al.start_chainage,
        [{"x": p.x, "y": p.y, "radius": p.radius, "label": p.label, "transition": p.transition} for p in al.ips],
        body.get("style") or {}, al.length, dense, al.end_chainage, alignment_id=alignment_id,
    )
    row = store.get_alignment(aid)
    assert row is not None
    return alignment_out(row, al)


def get_alignment(store: ProjectStore, alignment_id: int) -> tuple[dict, HorizontalAlignment]:
    row = store.get_alignment(alignment_id)
    if row is None:
        raise ServiceError("alignment not found", 404)
    return row, alignment_from_dict(row)


def alignment_geojson(al: HorizontalAlignment, name: str, project_crs: str | None, out_crs: str | None,
                      chainage_interval: float = 20.0, tick_length: float = 5.0) -> dict:
    dense, ch = al.densify(1.0, 1.0)
    dxy = _transform(dense, project_crs, out_crs)
    feats = [{"type": "Feature", "geometry": {"type": "LineString", "coordinates": _rounded(dxy, 8)},
              "properties": {"kind": "centreline", "name": name, "length": al.length,
                             "chainages": [round(float(c), 3) for c in ch]}}]
    for el in al.elements:
        feats.append({"type": "Feature", "geometry": None,
                      "properties": {"kind": "element", "element": el.kind, "start_chainage": el.start_chainage,
                                     "end_chainage": el.end_chainage, "radius": el.radius, "length": el.length}})
    kp = al.key_points()
    if kp:
        xy = _transform(np.array([[k["x"], k["y"]] for k in kp]), project_crs, out_crs)
        for k, (x, y) in zip(kp, xy):
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(float(x), 8), round(float(y), 8)]},
                          "properties": {"kind": k["kind"], "index": k["index"], "label": k.get("label", ""),
                                         "chainage": round(k["chainage"], 3), "chainage_label": format_chainage(k["chainage"]),
                                         "radius": k.get("radius"), "valid": k.get("valid", True)}})
    marks = al.chainage_marks(chainage_interval, tick_length)
    if marks:
        m1 = _transform(np.array([[m["x"], m["y"]] for m in marks]), project_crs, out_crs)
        m2 = _transform(np.array([[m["x2"], m["y2"]] for m in marks]), project_crs, out_crs)
        for m, a, b in zip(marks, m1, m2):
            feats.append({"type": "Feature",
                          "geometry": {"type": "LineString", "coordinates": [[round(float(a[0]), 8), round(float(a[1]), 8)], [round(float(b[0]), 8), round(float(b[1]), 8)]]},
                          "properties": {"kind": "chainage", "chainage": round(m["chainage"], 3), "label": m["label"], "angle": round(m["angle_deg"], 2)}})
    return _fc(feats, out_crs or project_crs)


# ------------------------------------------------------------------ sections
def _nan_to_none(a) -> list:
    return [None if (v is None or (isinstance(v, float) and math.isnan(v))) else round(float(v), 4) for v in a]


def run_sections(store: ProjectStore, params: dict, progress=None) -> dict:
    row, al = get_alignment(store, int(params["alignment_id"]))
    run_id, tin = load_tin(store, params.get("run_id"))
    if progress:
        progress(0.1, "profile")
    interval = float(params.get("interval", 20.0))
    prof_int = float(params.get("profile_interval") or interval)
    prof = generate_profile(tin, al, prof_int, bool(params.get("include_curve_points", True)),
                            params.get("extra_chainages") or (), bool(params.get("include_edge_crossings", True)))
    if progress:
        progress(0.4, "cross-sections")
    xs = generate_cross_sections(tin, al, interval, float(params.get("left", 15.0)), float(params.get("right", 15.0)),
                                 bool(params.get("include_curve_points", True)), params.get("extra_chainages") or (),
                                 bool(params.get("include_edge_crossings", True)))
    profile = [{"chainage": round(p.chainage, 4), "x": round(p.x, 4), "y": round(p.y, 4),
                "z": None if math.isnan(p.z) else round(p.z, 4), "source": p.source} for p in prof]
    sections = []
    for s in xs:
        sections.append({"chainage": round(s.chainage, 4), "label": s.label, "centre": [round(s.centre[0], 4), round(s.centre[1], 4)],
                         "direction": round(s.direction, 8), "left": s.left, "right": s.right,
                         "offset": [round(float(o), 4) for o in s.offset], "z": _nan_to_none(s.z),
                         "xy": _rounded(s.xy, 4), "source": s.source})
    summary = profile_summary(prof) | {"sections": len(xs), "alignment_length": al.length,
                                       "outside_sections": sum(1 for s in xs if np.all(np.isnan(s.z)))}
    stored = {k: v for k, v in params.items() if k != "sync"} | {"run_id": run_id}
    if progress:
        progress(0.9, "saving")
    set_id = store.save_section_set(int(params["alignment_id"]), run_id, stored, profile, sections, summary, name=params.get("name", ""))
    out = store.get_section_set(set_id, full=True)
    assert out is not None
    return out


def section_set_csvs(store: ProjectStore, set_id: int) -> tuple[str, str]:
    s = store.get_section_set(set_id, full=True)
    if s is None:
        raise ServiceError("section set not found", 404)
    prof_lines = ["Chainage,RL,Remarks"]
    for p in s["profile"]:
        if p["z"] is not None:
            prof_lines.append(f"{p['chainage']:.3f},{p['z']:.3f},")
    cross_lines = ["Chainage,PD,RL,Remarks"]
    for sec in s["sections"]:
        first = True
        for off, z in zip(sec["offset"], sec["z"]):
            if z is None:
                continue
            ch_txt = f"{sec['chainage']:.3f}" if first else ""
            cross_lines.append(f"{ch_txt},{off:.2f},{z:.3f},")
            first = False
    return "\n".join(prof_lines) + "\n", "\n".join(cross_lines) + "\n"


def section_lines_geojson(store: ProjectStore, set_id: int, project_crs: str | None, out_crs: str | None) -> dict:
    s = store.get_section_set(set_id, full=True)
    if s is None:
        raise ServiceError("section set not found", 404)
    feats = []
    for sec in s["sections"]:
        xy = np.asarray(sec["xy"], float)
        txy = _transform(xy, project_crs, out_crs)
        zs = sec["z"]
        coords = [[round(float(x), 8), round(float(y), 8), (0.0 if z is None else z)] for (x, y), z in zip(txy, zs)]
        feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                      "properties": {"chainage": sec["chainage"], "label": sec["label"], "left": sec["left"], "right": sec["right"],
                                     "outside": all(z is None for z in zs)}})
    return _fc(feats, out_crs or project_crs)


# ------------------------------------------------------------------ export
def export_dxf(store: ProjectStore, out_path: Path, *, run_id: int | None = None, contour_set_ids: Sequence[int] | None = None,
               include_points: bool = True, include_tin: bool = False, include_contours: bool = True,
               include_features: bool = True, include_boundary: bool = True, labels: bool = True,
               alignment_ids: Sequence[int] | None = None, section_set_id: int | None = None,
               chainage_interval: float = 20.0, arc_smoothing: bool = False, text_height: float | None = None,
               point_block: bool = True) -> Path:
    points = store.points() if include_points else None
    tin = None
    if include_tin:
        try:
            _, tin = load_tin(store, run_id)
        except ServiceError:
            tin = None
    contours: list[ContourLine] = []
    labels_out = []
    th = text_height
    if include_contours:
        sets = list(contour_set_ids) if contour_set_ids else [s["id"] for s in store.contour_sets()][-1:]
        for sid in sets:
            s = store.get_contour_set(sid)
            if not s:
                continue
            lines = store.load_contours(sid)
            contours.extend(lines)
            st = s.get("style") or {}
            if labels and st.get("show_labels", True):
                labels_out.extend(contour_labels(lines, every_m=float(st.get("label_every", 100.0)), fmt=st.get("label_format", "{z:.2f}"),
                                                 prefix=st.get("label_prefix", ""), suffix=st.get("label_suffix", ""),
                                                 major_only=bool(st.get("label_major_only", True))))
            if th is None:
                th = float(st.get("text_height", 1.0))
    features = [ln["coords"] for ln in store.lines(kind="feature")] if include_features else []
    boundary = [ln["coords"] for ln in store.lines(kind="boundary")] if include_boundary else []
    extra_pl: list[tuple[str, np.ndarray, bool]] = []
    extra_txt: list[tuple[str, str, float, float, float, float]] = []
    th = th or 1.0
    for aid in alignment_ids or []:
        row = store.get_alignment(aid)
        if not row:
            continue
        al = alignment_from_dict(row)
        # centreline with arcs as bulges
        extra_pl.append(("H_ALIGN", np.array([[x, y, 0.0] for x, y, _ in al.polyline_with_bulges()]), False))
        # IP markers + labels
        for g in al.geometry:
            extra_txt.append(("IPN", f"IP-{g.label}", g.x, g.y + th * 1.2, th, 0.0))
            if g.curve_length > 0:
                extra_pl.append(("IPLN", np.array([[g.bc[0], g.bc[1], 0], [g.x, g.y, 0], [g.ec[0], g.ec[1], 0]]), False))
        for m in al.chainage_marks(chainage_interval, tick_length=th * 5):
            extra_pl.append(("Chainage", np.array([[m["x"], m["y"], 0], [m["x2"], m["y2"], 0]]), False))
            extra_txt.append(("Chainage", m["label"], m["x2"], m["y2"], th, m["angle_deg"]))
    if section_set_id is not None:
        s = store.get_section_set(section_set_id, full=True)
        if s:
            for sec in s["sections"]:
                xy = np.asarray(sec["xy"], float)
                extra_pl.append(("Cross_Section", np.column_stack([xy[[0, -1]], np.zeros(2)]), False))
                extra_txt.append(("Cross_Section", sec["label"], sec["xy"][-1][0], sec["xy"][-1][1], th * 0.75, 0.0))
    write_dxf(
        out_path, points=points, tin=tin, contours=contours, contour_labels=labels_out,
        feature_lines=features, boundary_lines=boundary, extra_polylines=extra_pl, extra_texts=extra_txt,
        style=DxfStyle(arc_smoothing=arc_smoothing, text_height=th, point_block=point_block),
    )
    _write_bulge_alignments(out_path, store, alignment_ids or [])
    return out_path


def _write_bulge_alignments(path: Path, store: ProjectStore, alignment_ids: Sequence[int]) -> None:
    """Re-open the DXF and replace the straight H_ALIGN polylines by proper bulge polylines."""
    if not alignment_ids:
        return
    import ezdxf

    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    for e in list(msp.query("LWPOLYLINE[layer=='H_ALIGN']")) + list(msp.query("POLYLINE[layer=='H_ALIGN']")):
        msp.delete_entity(e)
    if "H_ALIGN" not in doc.layers:
        doc.layers.add("H_ALIGN", color=1)
    for aid in alignment_ids:
        row = store.get_alignment(aid)
        if not row:
            continue
        al = alignment_from_dict(row)
        pts = [(x, y, 0.0, 0.0, b) for x, y, b in al.polyline_with_bulges()]
        msp.add_lwpolyline(pts, format="xyseb", dxfattribs={"layer": "H_ALIGN"})
    doc.saveas(str(path))


def export_points_csv(store: ProjectStore, fmt: str = "id,x,y,z,remark", delimiter: str = ",") -> str:
    buf = io.StringIO()
    write_points_csv(store.points(), buf, fmt=fmt, delimiter=delimiter)
    return buf.getvalue()


def export_alignment_csv(store: ProjectStore, alignment_id: int) -> str:
    _, al = get_alignment(store, alignment_id)
    return al.to_aln_csv()


def profile_csv_from_engine(points) -> str:  # kept for symmetry / tests
    return profile_to_csv(points)


def cross_csv_from_engine(sections) -> str:
    return cross_sections_to_csv(sections)
