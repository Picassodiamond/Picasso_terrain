"""Constraint geometry - the core primitive behind boundaries, holes, islands, breaklines and
clipping - plus automatic detection of constraints from the point cloud.

A *constraint* is a polyline whose segments must appear as edges of the TIN (constrained Delaunay
triangulation). Closed constraints (rings) additionally partition the plane: a triangle is kept or
rejected by the *innermost* ring that contains its centroid, so boundaries, holes and islands may
nest to any depth (boundary > hole > island > hole ...). Open constraints are breaklines (ridges,
valleys, road edges, drains, cliffs): the surface is forced to follow them but nothing is clipped.

Kinds
-----
boundary   closed; the surface exists inside it (an island is simply a boundary nested in a hole)
hole       closed; the surface is removed inside it (legacy name: void)
breakline  open; edges only (legacy name: feature line; digitised contours are breaklines too)

Detection (`suggest_constraints`) works on the unconstrained Delaunay triangulation of the points:
long triangles are peeled from the outside to find the data limit (concave hull, chi-shape rule),
and connected groups of long interior triangles are reported as gaps (buildings, ponds, unsurveyed
areas). Suggestions carry a reason and a confidence so a UI can offer accept / reject / edit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np
import shapely
from shapely.geometry import Polygon

KINDS = ("boundary", "hole", "breakline")
_ALIASES = {"void": "hole", "feature": "breakline", "contour": "breakline", "island": "boundary",
            "exclusion": "hole", "outer": "boundary"}


def normalise_kind(kind: str) -> str:
    k = (kind or "breakline").strip().lower()
    k = _ALIASES.get(k, k)
    if k not in KINDS:
        raise ValueError(f"unknown constraint kind {kind!r}; expected one of {KINDS} (or void/feature)")
    return k


@dataclass
class Constraint:
    """One constraint polyline. `coords` is (k, 3); Z may be NaN (unknown, interpolated later)."""

    kind: str
    coords: np.ndarray
    name: str = ""
    source: str = ""  # 'auto' (detected) | 'drawn' | file name | ...
    id: int | None = None  # storage id when persisted

    def __post_init__(self) -> None:
        self.kind = normalise_kind(self.kind)
        c = np.asarray(self.coords, dtype=np.float64)
        if c.ndim != 2 or c.shape[1] < 2:
            raise ValueError("constraint coords must be (k, 2) or (k, 3)")
        if c.shape[1] == 2:
            c = np.column_stack([c, np.full(len(c), np.nan)])
        self.coords = np.ascontiguousarray(c[:, :3])

    @property
    def closed(self) -> bool:
        return self.kind != "breakline"

    def ring_xy(self) -> np.ndarray:
        """Ring vertices (k, 2) without the repeated closing vertex."""
        c = self.coords[:, :2]
        if len(c) > 1 and np.allclose(c[0], c[-1]):
            c = c[:-1]
        return c

    def polygon(self) -> Polygon:
        if not self.closed:
            raise ValueError("open constraints have no polygon")
        return Polygon(self.ring_xy())


@dataclass
class ConstraintIssue:
    kind: str  # 'invalid_ring' | 'dropped_constraint' | 'self_intersection'
    x: float
    y: float
    message: str


def _drop_repeated_vertices(c: np.ndarray, tol: float) -> np.ndarray:
    if len(c) < 2:
        return c
    d = np.hypot(*np.diff(c[:, :2], axis=0).T)
    keep = np.r_[True, d > tol]
    return c[keep]


@dataclass
class ConstraintSet:
    """Validated constraints ready for triangulation and classification."""

    rings: list[Constraint] = field(default_factory=list)
    breaklines: list[Constraint] = field(default_factory=list)
    issues: list[ConstraintIssue] = field(default_factory=list)
    _polys: list[Polygon] | None = None

    # -- construction ------------------------------------------------------------------
    @classmethod
    def build(cls, items: Iterable[Constraint | dict], *, tol: float = 1e-6) -> "ConstraintSet":
        """Normalise a mixed list of constraints: drop repeated vertices, close rings, reject
        degenerate input (reported as issues, never raised)."""
        cs = cls()
        for it in items:
            c = it if isinstance(it, Constraint) else Constraint(**it)
            coords = _drop_repeated_vertices(c.coords, tol)
            if c.closed:
                if len(coords) > 1 and np.allclose(coords[0, :2], coords[-1, :2]):
                    coords = coords[:-1]
                if len(coords) < 3:
                    x, y = (coords[0, :2] if len(coords) else (np.nan, np.nan))
                    cs.issues.append(ConstraintIssue("dropped_constraint", float(x), float(y),
                                                     f"{c.kind} '{c.name}' has fewer than three distinct vertices"))
                    continue
                ring = np.vstack([coords, coords[:1]])
                poly = Polygon(ring[:, :2])
                if poly.area <= 0:
                    cs.issues.append(ConstraintIssue("dropped_constraint", float(ring[0, 0]), float(ring[0, 1]),
                                                     f"{c.kind} '{c.name}' is collinear (zero area)"))
                    continue
                if not poly.is_valid:
                    p = shapely.get_coordinates(shapely.node(shapely.LineString(ring[:, :2])))[0]
                    cs.issues.append(ConstraintIssue("self_intersection", float(p[0]), float(p[1]),
                                                     f"{c.kind} '{c.name}' is self-intersecting; inside/outside is ambiguous there"))
                cs.rings.append(Constraint(c.kind, ring, c.name, c.source, c.id))
            else:
                if len(coords) < 2:
                    x, y = (coords[0, :2] if len(coords) else (np.nan, np.nan))
                    cs.issues.append(ConstraintIssue("dropped_constraint", float(x), float(y),
                                                     f"breakline '{c.name}' has fewer than two distinct vertices"))
                    continue
                cs.breaklines.append(Constraint(c.kind, coords, c.name, c.source, c.id))
        return cs

    @classmethod
    def from_legacy(cls, feature_lines: Sequence = (), boundary=None, voids: Sequence = (), *, tol: float = 1e-6) -> "ConstraintSet":
        """Adapter for the historical build_tin arguments."""
        items: list[Constraint] = []
        for c in feature_lines:
            items.append(Constraint("breakline", np.asarray(c, dtype=np.float64)))
        for kind, geoms in (("boundary", _polygons(boundary)), ("hole", _polygons(list(voids)) if len(voids) else [])):
            for p in geoms:
                items.append(Constraint(kind, np.asarray(p.exterior.coords, dtype=np.float64)[:, :2]))
                for r in p.interiors:  # a polygon with holes = ring + nested holes
                    items.append(Constraint("hole" if kind == "boundary" else "boundary", np.asarray(r.coords, dtype=np.float64)[:, :2]))
        return cls.build(items, tol=tol)

    # -- properties --------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.rings) + len(self.breaklines)

    @property
    def all(self) -> list[Constraint]:
        return self.rings + self.breaklines

    @property
    def has_boundary(self) -> bool:
        return any(r.kind == "boundary" for r in self.rings)

    def polygons(self) -> list[Polygon]:
        if self._polys is None:
            out = []
            for r in self.rings:
                p = Polygon(r.ring_xy())
                if not p.is_valid:
                    p = shapely.make_valid(p)
                    if p.geom_type != "Polygon":
                        p = max((g for g in getattr(p, "geoms", [p]) if g.geom_type == "Polygon"), key=lambda g: g.area, default=Polygon())
                out.append(p)
            self._polys = out
        return self._polys

    # -- triangulation input -----------------------------------------------------------
    def segments(self, base_index: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Stack constraint vertices; return (vertices (k,3), segments (s,2) indexing the stacked
        vertices offset by base_index, owner (k,) index into `self.all`)."""
        verts, segs, owner = [], [], []
        off = base_index
        for ci, c in enumerate(self.all):
            if c.closed:
                v = c.ring_xy()
                v = np.column_stack([v, c.coords[: len(v), 2]])
                k = len(v)
                s = np.column_stack([np.arange(off, off + k), np.r_[np.arange(off + 1, off + k), off]])
            else:
                v = c.coords
                k = len(v)
                s = np.column_stack([np.arange(off, off + k - 1), np.arange(off + 1, off + k)])
            verts.append(v)
            segs.append(s)
            owner.append(np.full(k, ci, dtype=np.int64))
            off += k
        if not verts:
            return np.zeros((0, 3)), np.zeros((0, 2), dtype=np.int64), np.zeros(0, dtype=np.int64)
        return np.vstack(verts), np.vstack(segs).astype(np.int64), np.concatenate(owner)

    # -- classification ----------------------------------------------------------------
    def classify(self, xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Keep/reject points (triangle centroids) by the innermost containing ring.

        Returns (keep (n,) bool, reason (n,) int8) with reason 0 keep, 1 outside every boundary,
        2 inside a hole.
        """
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        n = len(xy)
        keep = np.ones(n, dtype=bool)
        reason = np.zeros(n, dtype=np.int8)
        if not self.rings or n == 0:
            return keep, reason
        polys = self.polygons()
        areas = np.array([p.area for p in polys])
        order = np.argsort(-areas)  # largest first; later (smaller) assignments override
        innermost = -np.ones(n, dtype=np.int64)
        tree = shapely.STRtree(shapely.points(xy[:, 0], xy[:, 1]))
        for i in order:
            hit = tree.query(polys[i], predicate="contains")
            innermost[hit] = i
        kinds = np.array([r.kind for r in self.rings])
        none = innermost < 0
        if self.has_boundary:
            keep[none] = False
            reason[none] = 1
        inside = ~none
        k = kinds[innermost[inside]]
        hole = k == "hole"
        idx = np.flatnonzero(inside)
        keep[idx[hole]] = False
        reason[idx[hole]] = 2
        return keep, reason


def _polygons(geom) -> list[Polygon]:
    """Accept shapely (Multi)Polygon, coordinate arrays or lists thereof."""
    from shapely.geometry import LineString, MultiPolygon
    from shapely.geometry.base import BaseGeometry

    if geom is None:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    if isinstance(geom, LineString):
        return [Polygon(geom.coords)]
    if isinstance(geom, np.ndarray):
        return [Polygon(np.asarray(geom, dtype=np.float64)[:, :2])]
    if isinstance(geom, (list, tuple)):
        if len(geom) == 0:
            return []
        first = geom[0]
        if isinstance(first, BaseGeometry) or (isinstance(first, (list, tuple, np.ndarray)) and np.ndim(first) == 2):
            out: list[Polygon] = []
            for g in geom:
                out.extend(_polygons(g))
            return out
        return [Polygon(np.asarray(geom, dtype=np.float64)[:, :2])]
    raise TypeError(f"unsupported boundary geometry: {type(geom)!r}")


# --------------------------------------------------------------------------------------
# Ring assembly from edges
# --------------------------------------------------------------------------------------
def rings_from_edges(edges: np.ndarray) -> list[np.ndarray]:
    """Chain undirected edges (e, 2) of node indices into closed rings (arrays of node indices,
    first == last). Nodes with more than two edges (pinch points) split the walk; open chains
    are returned as well (first != last) so callers can decide what to do with them."""
    edges = np.asarray(edges, dtype=np.int64).reshape(-1, 2)
    adj: dict[int, list[int]] = {}
    for ei, (a, b) in enumerate(edges):
        adj.setdefault(int(a), []).append(ei)
        adj.setdefault(int(b), []).append(ei)
    used = np.zeros(len(edges), dtype=bool)
    rings: list[np.ndarray] = []
    for start_e in range(len(edges)):
        if used[start_e]:
            continue
        used[start_e] = True
        a, b = int(edges[start_e, 0]), int(edges[start_e, 1])
        chain = [a, b]
        cur = b
        while cur != a:
            nxt = None
            for ei in adj[cur]:
                if not used[ei]:
                    nxt = ei
                    break
            if nxt is None:
                break
            used[nxt] = True
            ea, eb = int(edges[nxt, 0]), int(edges[nxt, 1])
            cur = eb if ea == cur else ea
            chain.append(cur)
        rings.append(np.asarray(chain, dtype=np.int64))
    return rings


def _ring_area(xy: np.ndarray) -> float:
    x, y = xy[:, 0], xy[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


# --------------------------------------------------------------------------------------
# Automatic detection
# --------------------------------------------------------------------------------------
@dataclass
class Suggestion:
    kind: str
    coords: np.ndarray  # (k, 3) closed ring (first == last)
    reason: str
    confidence: float  # 0..1
    stats: dict = field(default_factory=dict)

    def to_constraint(self, source: str = "auto", name: str = "") -> Constraint:
        return Constraint(self.kind, self.coords, name or f"auto {self.kind}", source)


@dataclass
class DetectionResult:
    suggestions: list[Suggestion]
    stats: dict


def characteristic_edge_length(xy: np.ndarray) -> float:
    """Median nearest-neighbour distance of the points (robust spacing estimate)."""
    from scipy.spatial import cKDTree

    xy = np.asarray(xy, dtype=np.float64)
    if len(xy) < 2:
        return 0.0
    d, _ = cKDTree(xy).query(xy, k=2)
    return float(np.median(d[:, 1]))


def is_collinear(xy: np.ndarray, rel_tol: float = 1e-9) -> bool:
    """True when all points lie (numerically) on one straight line."""
    xy = np.asarray(xy, dtype=np.float64)
    if len(xy) < 3:
        return True
    c = xy - xy.mean(axis=0)
    s = np.linalg.svd(c, compute_uv=False)
    return bool(s[1] <= rel_tol * max(s[0], 1e-300))


def suggest_constraints(
    xyz: np.ndarray,
    *,
    edge_factor: float = 3.0,
    max_edge: float | None = None,
    min_hole_area: float | None = None,
    min_hole_triangles: int = 3,
    dedupe_tol: float = 0.001,
    detect_holes: bool = True,
) -> DetectionResult:
    """Detect a data-limit boundary and interior gaps from survey points.

    edge_factor   long-edge threshold as a multiple of the median Delaunay edge length
    max_edge      absolute threshold in metres (overrides edge_factor)
    min_hole_area minimum gap area to report (default 8 x median triangle area)
    """
    from .points import PointSet, dedupe
    from .tin import delaunay_tin

    xyz = np.asarray(xyz, dtype=np.float64).reshape(-1, 3)
    pts, _ = dedupe(PointSet(xyz), tol=dedupe_tol)
    stats: dict = {"points": int(len(pts))}
    tin = delaunay_tin(pts.xyz) if len(pts) >= 3 else None
    if tin is None:
        stats["message"] = "too few or collinear points"
        return DetectionResult([], stats)
    tri_area = np.abs(tin._signed_area2()) / 2.0

    elen = np.hypot(*(tin.nodes[tin.edges[:, 1], :2] - tin.nodes[tin.edges[:, 0], :2]).T)
    med = float(np.median(elen))
    T = float(max_edge) if max_edge else float(edge_factor) * med
    stats.update({"median_edge": med, "threshold": T, "triangles": tin.n_triangles})

    # 1. peel long triangles from the outside (chi-shape: keep the region regular) -----------
    keep = np.ones(tin.n_triangles, dtype=bool)
    tri_long = elen[tin.tri_edges]  # (m, 3) edge lengths
    peeled = 0
    for _ in range(10_000):
        et = tin.edge_tris
        k0 = np.where(et[:, 0] >= 0, keep[np.maximum(et[:, 0], 0)], False)
        k1 = np.where(et[:, 1] >= 0, keep[np.maximum(et[:, 1], 0)], False)
        bnd = k0 != k1  # exactly one kept neighbour -> boundary edge
        cand = np.flatnonzero(bnd & (elen > T))
        if not len(cand):
            break
        bnd_nodes = np.zeros(tin.n_nodes, dtype=bool)
        bnd_nodes[tin.edges[bnd].reshape(-1)] = True
        owner = np.where(k0[cand], et[cand, 0], et[cand, 1])
        # opposite vertex of the boundary edge inside its triangle must be interior (no pinch)
        tri_nodes = tin.triangles[owner]
        e_nodes = tin.edges[cand]
        is_opp = np.ones_like(tri_nodes, dtype=bool)
        for k in range(3):
            is_opp[:, k] = (tri_nodes[:, k] != e_nodes[:, 0]) & (tri_nodes[:, k] != e_nodes[:, 1])
        opp_node = tri_nodes[is_opp].reshape(-1)
        ok = ~bnd_nodes[opp_node]
        # remove the longest first within one sweep to reduce pinch races
        owner = np.unique(owner[ok])
        if not len(owner):
            break
        keep[owner] = False
        peeled += len(owner)
        if keep.sum() == 0:
            keep[:] = True  # threshold too aggressive: fall back to the convex hull
            peeled = 0
            break
    stats["peeled_triangles"] = int(peeled)

    suggestions: list[Suggestion] = []
    # boundary rings of the kept region ------------------------------------------------------
    et = tin.edge_tris
    k0 = np.where(et[:, 0] >= 0, keep[np.maximum(et[:, 0], 0)], False)
    k1 = np.where(et[:, 1] >= 0, keep[np.maximum(et[:, 1], 0)], False)
    bedges = tin.edges[k0 != k1]
    rings = [r for r in rings_from_edges(bedges) if len(r) >= 4 and r[0] == r[-1]]
    ring_polys = [Polygon(tin.nodes[r[:-1], :2]) for r in rings]
    ring_area = np.array([p.area for p in ring_polys]) if rings else np.zeros(0)
    for i, r in enumerate(rings):
        # a ring contained in a larger ring is an inner boundary => a hole
        contained = any(j != i and ring_area[j] > ring_area[i] and ring_polys[j].contains(ring_polys[i].representative_point()) for j in range(len(rings)))
        coords = tin.nodes[r]
        if contained:
            kind, reason, conf = "hole", "inner limit of the surveyed area (no points inside)", 0.75
        else:
            kind = "boundary"
            if peeled:
                reason = f"data limit: {peeled} long triangles (edge > {T:.1f} m, median {med:.1f} m) peeled from the convex hull"
                conf = 0.9
            else:
                reason = "convex hull of the points (no long edge triangles found)"
                conf = 0.6
        suggestions.append(Suggestion(kind, coords, reason, conf,
                                      {"vertices": int(len(r) - 1), "area": float(ring_area[i]), "threshold": T}))

    # 2. interior gaps: connected groups of long triangles inside the kept region --------------
    if detect_holes:
        med_area = float(np.median(tri_area))
        min_area = float(min_hole_area) if min_hole_area else 8.0 * med_area
        long_tri = keep & (tri_long.max(axis=1) > T)
        if long_tri.any():
            from scipy.sparse import coo_matrix
            from scipy.sparse.csgraph import connected_components

            idx = np.flatnonzero(long_tri)
            local = -np.ones(tin.n_triangles, dtype=np.int64)
            local[idx] = np.arange(len(idx))
            a, b = et[:, 0], et[:, 1]
            both = (a >= 0) & (b >= 0)
            both &= long_tri[np.maximum(a, 0)] & long_tri[np.maximum(b, 0)]
            la, lb = local[a[both]], local[b[both]]
            g = coo_matrix((np.ones(len(la)), (la, lb)), shape=(len(idx), len(idx)))
            ncomp, lab = connected_components(g, directed=False)
            for ci in range(ncomp):
                tris = idx[lab == ci]
                area = float(tri_area[tris].sum())
                if len(tris) < min_hole_triangles or area < min_area:
                    continue
                geom = shapely.unary_union(tin.triangle_geoms()[tris])
                polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
                for poly in polys:
                    if poly.area < min_area:
                        continue
                    ext = np.asarray(poly.exterior.coords)[:, :2]
                    # touching the outer limit means it is a bay of the boundary, not a gap
                    if not all(ring_polys[j].buffer(-1e-9).contains(poly) for j in range(len(rings)) if ring_polys[j].contains(poly.representative_point())):
                        continue
                    # map ring vertices back to nodes for Z
                    z = tin.elevation_at(ext[:, 0], ext[:, 1])
                    coords = np.column_stack([ext, z])
                    lmax = float(tri_long[tris].max())
                    conf = float(np.clip(0.5 + 0.25 * (lmax / T - 1.0), 0.4, 0.95))
                    suggestions.append(Suggestion(
                        "hole", coords,
                        f"gap with no survey points: {len(tris)} triangles, longest edge {lmax:.1f} m > {T:.1f} m, area {area:.0f} m2",
                        conf, {"triangles": int(len(tris)), "area": area, "longest_edge": lmax}))
    stats["suggestions"] = len(suggestions)
    return DetectionResult(suggestions, stats)
