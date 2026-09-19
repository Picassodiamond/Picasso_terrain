"""DXF import: survey points, feature/boundary lines and existing contours by layer.

Understands the legacy drawing conventions:
  * block INSERTs named POINTS (attributes PTNUM / DESC / ELEV) on layer Points-Blk
  * POINT entities (XYZ)
  * POLYLINE (3D), LWPOLYLINE (with elevation), LINE
Layer names decide the role: BOUNDARY -> boundary, VOID -> void, CONTOUR/INDEX_CONTOUR -> contour,
everything else -> feature line (the caller can restrict to chosen layers).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from ..points import PointSet

_BOUNDARY_LAYERS = {"BOUNDARY"}
_VOID_LAYERS = {"VOID", "VOIDS", "HOLE"}
_CONTOUR_LAYERS = {"CONTOUR", "INDEX_CONTOUR", "CONTOURS", "CONT"}


@dataclass
class DxfData:
    points: PointSet = field(default_factory=PointSet.empty)
    lines: list[dict[str, Any]] = field(default_factory=list)  # {coords (k,3), layer, kind, closed}
    layers: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def lines_of_kind(self, kind: str) -> list[np.ndarray]:
        return [ln["coords"] for ln in self.lines if ln["kind"] == kind]

    def lines_on_layers(self, layers: Iterable[str]) -> list[np.ndarray]:
        want = {ln.upper() for ln in layers}
        return [ln["coords"] for ln in self.lines if ln["layer"].upper() in want]


def _kind_for_layer(layer: str) -> str:
    u = layer.upper()
    if u in _BOUNDARY_LAYERS:
        return "boundary"
    if u in _VOID_LAYERS:
        return "void"
    if u in _CONTOUR_LAYERS:
        return "contour"
    return "feature"


def _bulge_to_arc(p0, p1, bulge: float, max_seg_len: float) -> np.ndarray:
    """Densify an LWPOLYLINE bulge segment into chord vertices (excluding p0, including p1)."""
    p0 = np.asarray(p0[:2], float)
    p1 = np.asarray(p1[:2], float)
    chord = p1 - p0
    L = float(np.hypot(*chord))
    if bulge == 0 or L == 0:
        return p1.reshape(1, 2)
    theta = 4.0 * np.arctan(bulge)  # included angle, sign = direction
    r = L / (2.0 * np.sin(abs(theta) / 2.0))
    mid = (p0 + p1) / 2.0
    # sagitta direction: left of chord for positive bulge (CCW)
    nrm = np.array([-chord[1], chord[0]]) / L
    d = r * np.cos(abs(theta) / 2.0)
    centre = mid - np.sign(bulge) * nrm * d if abs(theta) <= np.pi else mid + np.sign(bulge) * nrm * (-d)
    a0 = np.arctan2(*(p0 - centre)[::-1])
    arc_len = r * abs(theta)
    n = max(2, int(np.ceil(arc_len / max(max_seg_len, 1e-9))))
    ang = a0 + np.sign(bulge) * np.linspace(0, abs(theta), n + 1)[1:]
    pts = centre + r * np.column_stack([np.cos(ang), np.sin(ang)])
    pts[-1] = p1
    return pts


def read_dxf(
    path: str | Path,
    *,
    point_layers: Iterable[str] | None = None,
    line_layers: Iterable[str] | None = None,
    arc_segment_length: float = 0.5,
    point_block_names: Iterable[str] = ("POINTS",),
) -> DxfData:
    import ezdxf

    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    out = DxfData(layers=[ly.dxf.name for ly in doc.layers])
    pl_filter = {s.upper() for s in point_layers} if point_layers else None
    ln_filter = {s.upper() for s in line_layers} if line_layers else None
    blk_names = {b.upper() for b in point_block_names}

    xs, ys, zs, ids, rems, lays = [], [], [], [], [], []
    seen_xy: set[tuple[float, float, float]] = set()

    def add_point(x, y, z, pid="", rem="", layer=""):
        key = (round(float(x), 6), round(float(y), 6), round(float(z), 6))
        if key in seen_xy:
            return
        seen_xy.add(key)
        xs.append(float(x))
        ys.append(float(y))
        zs.append(float(z))
        ids.append(str(pid))
        rems.append(str(rem))
        lays.append(layer)

    # blocks first (they carry point number / remark); bare POINTs at the same XYZ are then skipped
    for ins in msp.query("INSERT"):
        if ins.dxf.name.upper() not in blk_names:
            continue
        layer = ins.dxf.layer
        if pl_filter and layer.upper() not in pl_filter:
            continue
        attrs = {a.dxf.tag.upper(): a.dxf.text for a in ins.attribs}
        p = ins.dxf.insert
        z = p.z
        if "ELEV" in attrs:
            try:
                z = float(attrs["ELEV"])
            except ValueError:
                pass
        add_point(p.x, p.y, z, attrs.get("PTNUM", ""), attrs.get("DESC", ""), layer)

    for pt in msp.query("POINT"):
        layer = pt.dxf.layer
        if pl_filter and layer.upper() not in pl_filter:
            continue
        p = pt.dxf.location
        add_point(p.x, p.y, p.z, "", "", layer)

    for e in msp:
        t = e.dxftype()
        layer = e.dxf.layer
        if ln_filter and layer.upper() not in ln_filter:
            continue
        kind = _kind_for_layer(layer)
        if t == "LINE":
            a, b = e.dxf.start, e.dxf.end
            out.lines.append({"coords": np.array([[a.x, a.y, a.z], [b.x, b.y, b.z]]), "layer": layer, "kind": kind, "closed": False})
        elif t == "LWPOLYLINE":
            elev = float(e.dxf.elevation) if e.dxf.hasattr("elevation") else 0.0
            pts = list(e.get_points("xyb"))
            coords = [np.array([pts[0][0], pts[0][1]])]
            for i in range(len(pts) - 1):
                x0, y0, b = pts[i]
                x1, y1, _ = pts[i + 1]
                coords.extend(_bulge_to_arc((x0, y0), (x1, y1), b, arc_segment_length))
            if e.closed:
                x0, y0, b = pts[-1]
                x1, y1, _ = pts[0]
                coords.extend(_bulge_to_arc((x0, y0), (x1, y1), b, arc_segment_length))
            c = np.asarray(coords, float)
            c3 = np.column_stack([c, np.full(len(c), elev)])
            out.lines.append({"coords": c3, "layer": layer, "kind": kind, "closed": bool(e.closed)})
        elif t == "POLYLINE":
            try:
                c = np.array([[v.dxf.location.x, v.dxf.location.y, v.dxf.location.z] for v in e.vertices], float)
            except Exception:
                out.skipped[t] = out.skipped.get(t, 0) + 1
                continue
            if len(c) < 2:
                continue
            if e.is_closed:
                c = np.vstack([c, c[:1]])
            out.lines.append({"coords": c, "layer": layer, "kind": kind, "closed": bool(e.is_closed)})
        elif t in ("INSERT", "POINT", "TEXT", "MTEXT", "ATTRIB", "3DFACE", "CIRCLE", "ARC", "HATCH", "DIMENSION"):
            continue
        else:
            out.skipped[t] = out.skipped.get(t, 0) + 1

    if xs:
        out.points = PointSet.from_arrays(xs, ys, zs, ids=ids, remarks=rems, layers=lays)
    return out
