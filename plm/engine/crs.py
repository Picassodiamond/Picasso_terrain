"""Coordinate reference system presets and transforms (PyProj).

Legacy data are plain grid metres with no CRS ("local"). A project may assign a CRS to enable
the map overlay. Nepal presets: Modified UTM zones (Everest 1830, 1937 adjustment) and WGS84 UTM.

NOTE: the MUTM datum shift (`towgs84`) below is the commonly published approximation. Confirm the
values with the Survey Department of Nepal or with local control points before relying on
sub-metre map alignment; the Helmert helper lets a project be georeferenced from control points.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_EVEREST = "+a=6377276.345 +rf=300.8017"
_NEPAL_SHIFT = "+towgs84=293.17,726.18,245.36,0,0,0,0"


def _mutm(lon0: int) -> str:
    return (f"+proj=tmerc +lat_0=0 +lon_0={lon0} +k=0.9999 +x_0=500000 +y_0=0 "
            f"{_EVEREST} {_NEPAL_SHIFT} +units=m +no_defs")


@dataclass(frozen=True)
class CrsPreset:
    key: str
    name: str
    definition: str | None   # PROJ string / EPSG code; None = local grid
    description: str = ""


PRESETS: dict[str, CrsPreset] = {
    "local": CrsPreset("local", "Local grid (no georeference, legacy behaviour)", None,
                       "Plain metres. All tools work; map overlay disabled."),
    "MUTM81": CrsPreset("MUTM81", "Nepal MUTM 81 (Everest 1830)", _mutm(81), "Western Nepal, CM 81E, k=0.9999"),
    "MUTM84": CrsPreset("MUTM84", "Nepal MUTM 84 (Everest 1830)", _mutm(84), "Central Nepal, CM 84E, k=0.9999"),
    "MUTM87": CrsPreset("MUTM87", "Nepal MUTM 87 (Everest 1830)", _mutm(87), "Eastern Nepal, CM 87E, k=0.9999"),
    "UTM44N": CrsPreset("UTM44N", "UTM zone 44N (WGS84)", "EPSG:32644"),
    "UTM45N": CrsPreset("UTM45N", "UTM zone 45N (WGS84)", "EPSG:32645"),
    "WGS84": CrsPreset("WGS84", "WGS84 geographic (lon/lat)", "EPSG:4326"),
}


def resolve(spec: str | None):
    """Return a pyproj.CRS or None for local/empty. Accepts preset keys, EPSG codes, PROJ/WKT."""
    if spec is None:
        return None
    s = str(spec).strip()
    if not s or s.lower() in ("local", "none"):
        return None
    if s in PRESETS:
        d = PRESETS[s].definition
        if d is None:
            return None
        s = d
    from pyproj import CRS

    return CRS.from_user_input(s)


def describe(spec: str | None) -> dict:
    crs = resolve(spec)
    if crs is None:
        return {"spec": spec or "local", "name": PRESETS["local"].name, "is_local": True, "epsg": None}
    try:
        proj4 = crs.to_proj4()
    except Exception:  # noqa: BLE001
        proj4 = None
    name = PRESETS[spec].name if spec in PRESETS else crs.name
    return {
        "spec": spec,
        "name": name,
        "is_local": False,
        "epsg": crs.to_epsg(),
        "units": (crs.axis_info[0].unit_name if crs.axis_info else None),
        "is_geographic": crs.is_geographic,
        "proj4": proj4,
    }


def transformer(src: str | None, dst: str | None):
    """pyproj Transformer with always_xy=True, or None when either side is local."""
    a, b = resolve(src), resolve(dst)
    if a is None or b is None:
        return None
    from pyproj import Transformer

    return Transformer.from_crs(a, b, always_xy=True)


def transform_xy(src: str | None, dst: str | None, x, y):
    tr = transformer(src, dst)
    if tr is None:
        raise ValueError("cannot transform: project has no coordinate reference system")
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ox, oy = tr.transform(x, y)
    return np.asarray(ox), np.asarray(oy)


def to_wgs84(spec: str | None, x, y):
    return transform_xy(spec, "EPSG:4326", x, y)


# ------------------------------------------------------------------ control-point georeferencing
@dataclass
class Helmert2D:
    """Similarity transform  X' = s*R(theta)*X + t  fitted from control points (least squares)."""

    scale: float
    rotation: float  # radians
    tx: float
    ty: float
    rms: float

    def apply(self, x, y):
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        c, s = np.cos(self.rotation) * self.scale, np.sin(self.rotation) * self.scale
        return c * x - s * y + self.tx, s * x + c * y + self.ty

    def proj_string(self) -> str:
        """Equivalent PROJ pipeline step for an affine transform (for documentation/export)."""
        c, s = np.cos(self.rotation) * self.scale, np.sin(self.rotation) * self.scale
        return f"+proj=affine +xoff={self.tx} +yoff={self.ty} +s11={c} +s12={-s} +s21={s} +s22={c}"


def fit_helmert(src_xy: np.ndarray, dst_xy: np.ndarray) -> Helmert2D:
    """Fit a 4-parameter similarity transform from >= 2 control points."""
    src = np.asarray(src_xy, float)
    dst = np.asarray(dst_xy, float)
    if len(src) < 2 or len(src) != len(dst):
        raise ValueError("need at least two matching control points")
    # unknowns a, b, tx, ty with x' = a x - b y + tx, y' = b x + a y + ty
    A = np.zeros((2 * len(src), 4))
    L = np.zeros(2 * len(src))
    A[0::2, 0], A[0::2, 1], A[0::2, 2] = src[:, 0], -src[:, 1], 1.0
    A[1::2, 0], A[1::2, 1], A[1::2, 3] = src[:, 1], src[:, 0], 1.0
    L[0::2], L[1::2] = dst[:, 0], dst[:, 1]
    p, *_ = np.linalg.lstsq(A, L, rcond=None)
    a, b, tx, ty = p
    scale = float(np.hypot(a, b))
    rot = float(np.arctan2(b, a))
    h = Helmert2D(scale, rot, float(tx), float(ty), 0.0)
    px, py = h.apply(src[:, 0], src[:, 1])
    h.rms = float(np.sqrt(np.mean((px - dst[:, 0]) ** 2 + (py - dst[:, 1]) ** 2)))
    return h
