"""Contour generation on a TIN (port of legacy `contour.frm`, corrected and vectorised)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge

from .tin import TIN


@dataclass
class ContourLine:
    level: float
    is_major: bool
    coords: np.ndarray  # (k, 2)
    closed: bool

    @property
    def geometry(self) -> LineString:
        return LineString(self.coords)

    @property
    def length(self) -> float:
        return float(np.hypot(*np.diff(self.coords, axis=0).T).sum())


@dataclass
class ContourLabel:
    level: float
    x: float
    y: float
    angle_deg: float  # counter-clockwise from +X, text reads left-to-right
    text: str


def levels_for(zmin: float, zmax: float, interval: float, base: float = 0.0) -> np.ndarray:
    """Contour levels base + k*interval strictly covering (zmin, zmax)."""
    if interval <= 0:
        raise ValueError("interval must be > 0")
    k0 = int(np.floor((zmin - base) / interval))
    k1 = int(np.ceil((zmax - base) / interval))
    lv = base + np.arange(k0, k1 + 1) * interval
    lv = lv[(lv > zmin) & (lv < zmax)]
    return np.round(lv, 9)


def is_major_level(level: float, interval: float, major_every: int, base: float = 0.0) -> bool:
    if major_every <= 0:
        return False
    major = interval * major_every
    r = (level - base) / major
    return abs(r - round(r)) < 1e-6


def _segments_for_level(tin: TIN, level: float) -> np.ndarray | None:
    """Return (s, 2, 2) array of contour segments for one level, or None."""
    z = tin.nodes[:, 2]
    e = tin.edges
    z0, z1 = z[e[:, 0]], z[e[:, 1]]
    below0 = z0 < level
    below1 = z1 < level
    cross = below0 != below1
    if not cross.any():
        return None
    # crossing point on every crossing edge (NaN elsewhere)
    pt = np.full((len(e), 2), np.nan)
    ce = np.flatnonzero(cross)
    t = (level - z0[ce]) / (z1[ce] - z0[ce])
    p0 = tin.nodes[e[ce, 0], :2]
    p1 = tin.nodes[e[ce, 1], :2]
    pt[ce] = p0 + t[:, None] * (p1 - p0)
    # triangles with exactly two crossing edges
    tc = cross[tin.tri_edges]  # (m, 3)
    two = tc.sum(axis=1) == 2
    if not two.any():
        return None
    te = tin.tri_edges[two]
    tcm = tc[two]
    # pick the two crossing edges per triangle
    order = np.argsort(~tcm, axis=1, kind="stable")[:, :2]  # True first
    ea = te[np.arange(len(te)), order[:, 0]]
    eb = te[np.arange(len(te)), order[:, 1]]
    seg = np.stack([pt[ea], pt[eb]], axis=1)
    return seg


def _thin(coords: np.ndarray, min_spacing: float, closed: bool) -> np.ndarray:
    if min_spacing <= 0 or len(coords) <= 2:
        return coords
    keep = [0]
    acc = 0.0
    for i in range(1, len(coords)):
        acc += float(np.hypot(*(coords[i] - coords[i - 1])))
        if acc >= min_spacing:
            keep.append(i)
            acc = 0.0
    if keep[-1] != len(coords) - 1:
        if closed:
            keep[-1] = len(coords) - 1
        else:
            keep.append(len(coords) - 1)
    out = coords[keep]
    return out if len(out) >= (3 if closed else 2) else coords


def chaikin(coords: np.ndarray, iterations: int = 2, closed: bool = False) -> np.ndarray:
    """Chaikin corner cutting; endpoints kept for open lines, ring stays closed."""
    c = np.asarray(coords, dtype=np.float64)
    for _ in range(max(0, iterations)):
        if closed:
            ring = c[:-1] if np.allclose(c[0], c[-1]) else c
            nxt = np.roll(ring, -1, axis=0)
            q = 0.75 * ring + 0.25 * nxt
            r = 0.25 * ring + 0.75 * nxt
            c = np.empty((2 * len(ring) + 1, 2))
            c[0:-1:2] = q
            c[1:-1:2] = r
            c[-1] = c[0]
        else:
            if len(c) < 3:
                return c
            q = 0.75 * c[:-1] + 0.25 * c[1:]
            r = 0.25 * c[:-1] + 0.75 * c[1:]
            mid = np.empty((2 * (len(c) - 1), 2))
            mid[0::2] = q
            mid[1::2] = r
            c = np.vstack([c[:1], mid, c[-1:]])
    return c


def contour_tin(
    tin: TIN,
    interval: float,
    major_every: int = 5,
    base: float = 0.0,
    levels: Sequence[float] | None = None,
    min_spacing: float = 0.0,
    smoothing: str = "none",
    smooth_iterations: int = 2,
    min_length: float = 0.0,
) -> list[ContourLine]:
    """Generate contour polylines from a TIN.

    interval / major_every / base   level spacing, index-contour cadence and datum
    levels        explicit list of levels (overrides interval/base for level selection)
    min_spacing   drop vertices closer than this along a contour (legacy `min_dist`)
    smoothing     'none' | 'chaikin'   ('arc' bulge smoothing is applied only by the DXF exporter)
    min_length    discard contour pieces shorter than this
    """
    zmin, zmax = tin.z_range()
    lv = np.asarray(levels, dtype=np.float64) if levels is not None else levels_for(zmin, zmax, interval, base)
    eps = max(1e-9, 1e-6 * interval)
    z = tin.nodes[:, 2]
    out: list[ContourLine] = []
    for level in lv:
        lvl = float(level)
        # Legacy nudged node elevations by 1.5 mm when they coincided with a level; here the
        # level itself is shifted by a tiny epsilon so every triangle has 0 or 2 crossings.
        guard = 0
        while np.any(np.abs(z - lvl) < eps) and guard < 10:
            lvl += eps
            guard += 1
        seg = _segments_for_level(tin, lvl)
        if seg is None:
            continue
        merged = linemerge(MultiLineString(list(seg)))
        parts = list(merged.geoms) if isinstance(merged, MultiLineString) else [merged]
        major = is_major_level(float(level), interval, major_every, base)
        for g in parts:
            if g.is_empty or g.length < min_length:
                continue
            c = np.asarray(g.coords, dtype=np.float64)[:, :2]
            closed = bool(np.allclose(c[0], c[-1]))
            c = _thin(c, min_spacing, closed)
            if smoothing == "chaikin":
                c = chaikin(c, smooth_iterations, closed)
            out.append(ContourLine(level=float(level), is_major=major, coords=c, closed=closed))
    out.sort(key=lambda c: (c.level, -c.length))
    return out


def contour_labels(
    lines: Sequence[ContourLine],
    every_m: float = 100.0,
    fmt: str = "{z:.2f}",
    prefix: str = "",
    suffix: str = "",
    major_only: bool = False,
    start_offset: float | None = None,
) -> list[ContourLabel]:
    """Label positions along contours, every `every_m` metres, text rotated along the line."""
    labels: list[ContourLabel] = []
    for cl in lines:
        if major_only and not cl.is_major:
            continue
        L = cl.length
        if L <= 0:
            continue
        geom = cl.geometry
        first = every_m / 2 if start_offset is None else start_offset
        d = first if L > first else L / 2
        while d <= L:
            p = geom.interpolate(d)
            p0 = geom.interpolate(max(0.0, d - 0.5))
            p1 = geom.interpolate(min(L, d + 0.5))
            ang = float(np.degrees(np.arctan2(p1.y - p0.y, p1.x - p0.x)))
            if ang > 90:
                ang -= 180
            elif ang < -90:
                ang += 180
            labels.append(ContourLabel(cl.level, float(p.x), float(p.y), ang,
                                       f"{prefix}{fmt.format(z=cl.level)}{suffix}"))
            d += every_m
            if every_m <= 0:
                break
    return labels
