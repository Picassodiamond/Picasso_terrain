"""Contour generation on a TIN (port of legacy `contour.frm`, corrected and vectorised).

Contours are derived from the validated TIN only. For each level every TIN edge is tested with a
single consistent rule - a node is *below* when z < level, so nodes exactly on the level count as
above - which guarantees that every triangle has either zero or exactly two crossing edges. No
level nudging is needed, contours pass cleanly through vertices and along flat triangle edges,
and segments are chained by the identity of the edge / vertex they cross rather than by
coordinates, so pieces join exactly and nothing is duplicated.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

import numpy as np
import shapely
from shapely.geometry import LineString

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


# --------------------------------------------------------------------------------------
# Segment extraction and chaining
# --------------------------------------------------------------------------------------
def _level_segments(tin: TIN, level: float):
    """Contour segments for one level.

    Returns (ka, kb, pts) or None: ka/kb are crossing keys per segment (edge index >= 0, or
    -(node+1) when the contour passes exactly through a node) and `pts` maps key -> (x, y).
    Zero-length segments (both crossings at the same node) and duplicates (a flat edge on the
    level seen from both neighbours) are removed here.
    """
    z = tin.nodes[:, 2]
    e = tin.edges
    z0, z1 = z[e[:, 0]], z[e[:, 1]]
    cross = (z0 < level) != (z1 < level)
    if not cross.any():
        return None
    ce = np.flatnonzero(cross)
    t = (level - z0[ce]) / (z1[ce] - z0[ce])
    t = np.clip(t, 0.0, 1.0)
    p0 = tin.nodes[e[ce, 0], :2]
    p1 = tin.nodes[e[ce, 1], :2]
    pt = p0 + t[:, None] * (p1 - p0)
    key = ce.astype(np.int64)
    at0 = t <= 0.0
    at1 = t >= 1.0
    key[at0] = -(e[ce[at0], 0] + 1)
    key[at1] = -(e[ce[at1], 1] + 1)
    pt[at0] = p0[at0]
    pt[at1] = p1[at1]
    edge_key = np.zeros(tin.n_edges, dtype=np.int64)
    edge_key[ce] = key
    edge_pt = np.zeros((tin.n_edges, 2))
    edge_pt[ce] = pt

    tc = cross[tin.tri_edges]  # (m, 3)
    two = tc.sum(axis=1) == 2  # always 0 or 2 with the consistent rule above
    if not two.any():
        return None
    te = tin.tri_edges[two]
    order = np.argsort(~tc[two], axis=1, kind="stable")[:, :2]  # crossing edges first
    rows = np.arange(len(te))
    ea = te[rows, order[:, 0]]
    eb = te[rows, order[:, 1]]
    ka, kb = edge_key[ea], edge_key[eb]
    valid = ka != kb
    ka, kb, ea, eb = ka[valid], kb[valid], ea[valid], eb[valid]
    if not len(ka):
        return None
    pair = np.sort(np.stack([ka, kb], axis=1), axis=1)
    _, first = np.unique(pair, axis=0, return_index=True)
    first = np.sort(first)
    ka, kb, ea, eb = ka[first], kb[first], ea[first], eb[first]
    pts: dict[int, np.ndarray] = {}
    for k, ei in zip(np.concatenate([ka, kb]), np.concatenate([ea, eb])):
        pts[int(k)] = edge_pt[ei]
    return ka, kb, pts


def _chain(ka: np.ndarray, kb: np.ndarray, pts: dict[int, np.ndarray]) -> list[tuple[np.ndarray, bool]]:
    """Chain segments sharing crossing keys into polylines. Junctions (a contour crossing itself
    at a saddle vertex, degree > 2) end a chain so every output line is unambiguous."""
    n = len(ka)
    adj: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        adj[int(ka[i])].append(i)
        adj[int(kb[i])].append(i)
    used = np.zeros(n, dtype=bool)
    out: list[tuple[np.ndarray, bool]] = []

    def walk(seg: int, key: int) -> list[int]:
        keys = [key]
        cur_key, cur_seg = key, seg
        while True:
            used[cur_seg] = True
            nxt = int(kb[cur_seg]) if int(ka[cur_seg]) == cur_key else int(ka[cur_seg])
            keys.append(nxt)
            nbrs = adj[nxt]
            if len(nbrs) != 2:
                break
            nseg = nbrs[0] if nbrs[0] != cur_seg else nbrs[1]
            if used[nseg]:
                break
            cur_key, cur_seg = nxt, nseg
        return keys

    # open chains start at ends and junctions
    for key, segs in adj.items():
        if len(segs) == 2:
            continue
        for seg in segs:
            if not used[seg]:
                ks = walk(seg, key)
                out.append((np.array([pts[k] for k in ks]), False))
    # what remains are closed loops
    for seg in range(n):
        if not used[seg]:
            ks = walk(seg, int(ka[seg]))
            closed = ks[0] == ks[-1]
            out.append((np.array([pts[k] for k in ks]), closed))
    return out


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


def _clip_line(coords: np.ndarray, clip) -> list[np.ndarray]:
    g = shapely.intersection(LineString(coords), clip)
    if g.is_empty:
        return []
    parts = list(g.geoms) if hasattr(g, "geoms") else [g]
    return [np.asarray(p.coords, dtype=np.float64)[:, :2] for p in parts if p.geom_type == "LineString" and len(p.coords) >= 2]


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
    clip=None,
) -> list[ContourLine]:
    """Generate contour polylines from a TIN.

    interval / major_every / base   level spacing, index-contour cadence and datum
    levels        explicit list of levels (overrides interval/base for level selection)
    min_spacing   drop vertices closer than this along a contour (legacy `min_dist`)
    smoothing     'none' | 'chaikin'   ('arc' bulge smoothing is applied only by the DXF exporter)
    min_length    discard contour pieces shorter than this
    clip          optional shapely (Multi)Polygon; contours are cut to it (the TIN itself is
                  already limited by its constraints, so this is for presentation only)
    """
    zmin, zmax = tin.z_range()
    lv = np.asarray(levels, dtype=np.float64) if levels is not None else levels_for(zmin, zmax, interval, base)
    out: list[ContourLine] = []
    for level in lv:
        lvl = float(level)
        seg = _level_segments(tin, lvl)
        if seg is None:
            continue
        major = is_major_level(lvl, interval, major_every, base)
        for c, closed in _chain(*seg):
            pieces = _clip_line(c, clip) if clip is not None else [c]
            for pc in pieces:
                if len(pc) < 2:
                    continue
                cl = closed and bool(np.allclose(pc[0], pc[-1]))
                if float(np.hypot(*np.diff(pc, axis=0).T).sum()) < min_length:
                    continue
                pc = _thin(pc, min_spacing, cl)
                if smoothing == "chaikin":
                    pc = chaikin(pc, smooth_iterations, cl)
                out.append(ContourLine(level=lvl, is_major=major, coords=pc, closed=cl))
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
