"""GeoJSON import/export.

Import: Point/MultiPoint -> survey points (Z from the coordinate or from a z/elev/rl/elevation
property), LineString/MultiLineString -> feature lines, Polygon/MultiPolygon -> boundary/void
candidates. A property named `kind` (feature|boundary|void|contour) or `layer` is preserved.
Export: points, lines, contours as FeatureCollections (coordinates in the project CRS unless the
caller transforms them).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from shapely.geometry import Polygon, shape

from ..contour import ContourLine
from ..points import PointSet

_Z_PROPS = ("z", "elev", "elevation", "rl", "height", "h", "level")
_ID_PROPS = ("id", "no", "ptno", "ptnum", "name", "number", "point")
_REM_PROPS = ("remark", "remarks", "desc", "description", "code", "note")


@dataclass
class GeoJsonData:
    points: PointSet = field(default_factory=PointSet.empty)
    lines: list[dict[str, Any]] = field(default_factory=list)     # {coords (k,3), layer, kind, props}
    polygons: list[dict[str, Any]] = field(default_factory=list)  # {geometry Polygon, layer, kind, props}

    def lines_of_kind(self, kind: str) -> list[np.ndarray]:
        return [ln["coords"] for ln in self.lines if ln["kind"] == kind]

    def polygons_of_kind(self, kind: str) -> list[Polygon]:
        return [p["geometry"] for p in self.polygons if p["kind"] == kind]


def _prop(props: dict, names: Iterable[str], default=None):
    lower = {str(k).lower(): v for k, v in props.items()}
    for n in names:
        if n in lower and lower[n] not in (None, ""):
            return lower[n]
    return default


def _kind(props: dict, default: str) -> str:
    k = _prop(props, ("kind", "type", "role"), None)
    layer = str(_prop(props, ("layer",), "")).upper()
    if isinstance(k, str) and k.lower() in ("feature", "boundary", "void", "contour", "point"):
        return k.lower()
    if layer in ("BOUNDARY",):
        return "boundary"
    if layer in ("VOID", "VOIDS", "HOLE"):
        return "void"
    if layer in ("CONTOUR", "INDEX_CONTOUR", "CONTOURS"):
        return "contour"
    return default


def _coords3(coords, props: dict) -> np.ndarray:
    c = np.asarray(coords, dtype=np.float64)
    if c.ndim == 1:
        c = c.reshape(1, -1)
    if c.shape[1] == 2:
        z = _prop(props, _Z_PROPS, 0.0)
        c = np.column_stack([c, np.full(len(c), float(z))])
    return c[:, :3]


def read_geojson(source: str | Path | dict) -> GeoJsonData:
    if isinstance(source, dict):
        gj = source
    else:
        s = str(source)
        gj = json.loads(Path(s).read_text(encoding="utf-8-sig")) if "{" not in s else json.loads(s)
    feats = gj["features"] if gj.get("type") == "FeatureCollection" else [gj]
    xs, ys, zs, ids, rems, lays = [], [], [], [], [], []
    out = GeoJsonData()
    for ft in feats:
        geom = ft.get("geometry") if ft.get("type") == "Feature" else ft
        props = ft.get("properties") or {}
        if not geom:
            continue
        gtype = geom.get("type")
        layer = str(_prop(props, ("layer",), ""))
        if gtype in ("Point", "MultiPoint"):
            pts = [geom["coordinates"]] if gtype == "Point" else geom["coordinates"]
            for p in pts:
                c = _coords3(p, props)[0]
                xs.append(c[0])
                ys.append(c[1])
                zs.append(c[2])
                ids.append(str(_prop(props, _ID_PROPS, "")))
                rems.append(str(_prop(props, _REM_PROPS, "")))
                lays.append(layer)
        elif gtype in ("LineString", "MultiLineString"):
            parts = [geom["coordinates"]] if gtype == "LineString" else geom["coordinates"]
            kind = _kind(props, "feature")
            for part in parts:
                out.lines.append({"coords": _coords3(part, props), "layer": layer, "kind": kind, "props": props})
        elif gtype in ("Polygon", "MultiPolygon"):
            kind = _kind(props, "boundary")
            g = shape(geom)
            polys = list(g.geoms) if gtype == "MultiPolygon" else [g]
            for p in polys:
                out.polygons.append({"geometry": p, "layer": layer, "kind": kind, "props": props})
    if xs:
        out.points = PointSet.from_arrays(xs, ys, zs, ids=ids, remarks=rems, layers=lays)
    return out


# ---------------------------------------------------------------------------- export
def _fc(features: list[dict], crs: str | None = None) -> dict:
    fc: dict[str, Any] = {"type": "FeatureCollection", "features": features}
    if crs:
        fc["crs"] = {"type": "name", "properties": {"name": crs}}
    return fc


def _round(a: np.ndarray, nd: int) -> list:
    return np.round(np.asarray(a, dtype=float), nd).tolist()


def points_to_geojson(points: PointSet, crs: str | None = None, ndigits: int = 3) -> dict:
    feats = []
    for i in range(len(points)):
        feats.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": _round(points.xyz[i], ndigits)},
                "properties": {
                    "id": points.ids[i],
                    "z": round(float(points.z[i]), ndigits),
                    "remark": points.remarks[i],
                    "layer": points.layers[i],
                },
            }
        )
    return _fc(feats, crs)


def features_to_geojson(
    lines: Sequence[np.ndarray], kind: str = "feature", layer: str = "", crs: str | None = None, ndigits: int = 3
) -> dict:
    feats = [
        {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": _round(np.asarray(c)[:, :3] if np.asarray(c).shape[1] >= 3 else c, ndigits)},
            "properties": {"kind": kind, "layer": layer},
        }
        for c in lines
    ]
    return _fc(feats, crs)


def contours_to_geojson(
    contours: Sequence[ContourLine], crs: str | None = None, ndigits: int = 3, with_z: bool = True
) -> dict:
    feats = []
    for i, c in enumerate(contours):
        coords = c.coords
        if with_z:
            coords = np.column_stack([coords, np.full(len(coords), c.level)])
        feats.append(
            {
                "type": "Feature",
                "id": i,
                "geometry": {"type": "LineString", "coordinates": _round(coords, ndigits)},
                "properties": {
                    "kind": "contour",
                    "level": c.level,
                    "major": c.is_major,
                    "closed": c.closed,
                    "layer": "Index_Contour" if c.is_major else "Contour",
                },
            }
        )
    return _fc(feats, crs)


def write_geojson(data: dict, target: str | Path) -> None:
    Path(target).write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
