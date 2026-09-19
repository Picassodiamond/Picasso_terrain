"""Triangulated irregular network: build, topology, clipping and sampling.

Replaces `frmtriangulate.frm` + the Triangle DLL, and the `Find_RL` / `Find_Int_RL`
routines used by the profile / cross-section generators.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from .points import PointSet, DedupeReport, dedupe, fill_zero_z_along_line


class TinError(RuntimeError):
    """Triangulation could not be performed (too few points, collinear input...)."""


# --------------------------------------------------------------------------------------
# Triangulator interface + adapters
# --------------------------------------------------------------------------------------
class Triangulator(Protocol):
    """Constrained Delaunay triangulation of 2-D points with optional segment constraints.

    Returns (vertices (k, 2), triangles (m, 3)). k >= len(xy); extra vertices (Steiner points)
    are appended after the input vertices and only appear when constraint segments cross.
    """

    def triangulate(
        self, xy: np.ndarray, segments: np.ndarray | None
    ) -> tuple[np.ndarray, np.ndarray]: ...


class TriangleAdapter:
    """Shewchuk Triangle via the `triangle` package - the same algorithm as the legacy DLL.

    Flags: Q quiet, c keep the convex hull (legacy convex=1; without it open feature lines
    would let Triangle carve away the whole mesh), p PSLG when segments are given.
    No quality (q) or area (a) refinement, so no Steiner points except at segment crossings.
    """

    def triangulate(
        self, xy: np.ndarray, segments: np.ndarray | None
    ) -> tuple[np.ndarray, np.ndarray]:
        import triangle  # local import keeps the engine importable without it

        data = {"vertices": np.ascontiguousarray(xy, dtype=np.float64)}
        opts = "Qc"
        if segments is not None and len(segments):
            data["segments"] = np.ascontiguousarray(segments, dtype=np.int32)
            opts += "p"
        try:
            out = triangle.triangulate(data, opts)
        except Exception as exc:  # pragma: no cover - depends on native lib
            raise TinError(f"Triangle failed: {exc}") from exc
        if "triangles" not in out or len(out["triangles"]) == 0:
            raise TinError("Triangle produced no triangles (degenerate or collinear input?)")
        return (
            np.asarray(out["vertices"], dtype=np.float64),
            np.asarray(out["triangles"], dtype=np.int64),
        )


class ScipyDelaunayAdapter:
    """Unconstrained fallback (Qhull). Ignores segments - use only when no feature lines exist."""

    def triangulate(
        self, xy: np.ndarray, segments: np.ndarray | None
    ) -> tuple[np.ndarray, np.ndarray]:
        from scipy.spatial import Delaunay

        if segments is not None and len(segments):
            raise TinError("ScipyDelaunayAdapter cannot honour constraint segments")
        try:
            d = Delaunay(np.asarray(xy, dtype=np.float64))
        except Exception as exc:
            raise TinError(f"Qhull failed: {exc}") from exc
        return np.asarray(xy, dtype=np.float64), np.asarray(d.simplices, dtype=np.int64)


# --------------------------------------------------------------------------------------
# TIN data structure
# --------------------------------------------------------------------------------------
class TIN:
    """Nodes (n, 3), triangles (m, 3) and derived edge topology (all 0-based).

    edges      (e, 2) node pairs, sorted so edges[:, 0] < edges[:, 1]
    tri_edges  (m, 3) edge index of (n0-n1), (n1-n2), (n2-n0)   - legacy plm.tri layout
    edge_tris  (e, 2) triangles sharing each edge, -1 for hull/boundary edges
    """

    def __init__(self, nodes: np.ndarray, triangles: np.ndarray):
        self.nodes = np.ascontiguousarray(nodes, dtype=np.float64).reshape(-1, 3)
        self.triangles = np.ascontiguousarray(triangles, dtype=np.int64).reshape(-1, 3)
        if len(self.triangles) == 0:
            raise TinError("TIN has no triangles")
        self._orient_ccw()
        self._build_topology()
        self._tri_tree: shapely.STRtree | None = None
        self._tri_geoms: np.ndarray | None = None
        self._edge_tree: shapely.STRtree | None = None
        self._edge_geoms: np.ndarray | None = None

    # -- construction ------------------------------------------------------------------
    def _signed_area2(self) -> np.ndarray:
        p = self.nodes[self.triangles, :2]
        return (p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1]) - (
            p[:, 2, 0] - p[:, 0, 0]
        ) * (p[:, 1, 1] - p[:, 0, 1])

    def _orient_ccw(self) -> None:
        cw = self._signed_area2() < 0
        if cw.any():
            self.triangles[cw] = self.triangles[cw][:, [0, 2, 1]]

    def _build_topology(self) -> None:
        tri = self.triangles
        m = len(tri)
        e = np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]])
        e = np.sort(e, axis=1)
        edges, inverse = np.unique(e, axis=0, return_inverse=True)
        inverse = np.asarray(inverse).reshape(-1)
        self.edges = edges
        self.tri_edges = np.stack([inverse[:m], inverse[m : 2 * m], inverse[2 * m :]], axis=1)
        n_e = len(edges)
        edge_tris = -np.ones((n_e, 2), dtype=np.int64)
        flat_e = self.tri_edges.T.reshape(-1)
        flat_t = np.tile(np.arange(m), 3)
        order = np.argsort(flat_e, kind="stable")
        fe, ft = flat_e[order], flat_t[order]
        first = np.r_[True, fe[1:] != fe[:-1]]
        edge_tris[fe[first], 0] = ft[first]
        second = ~first
        edge_tris[fe[second], 1] = ft[second]
        self.edge_tris = edge_tris

    # -- properties --------------------------------------------------------------------
    @property
    def n_nodes(self) -> int:
        return len(self.nodes)

    @property
    def n_triangles(self) -> int:
        return len(self.triangles)

    @property
    def n_edges(self) -> int:
        return len(self.edges)

    @property
    def boundary_edges(self) -> np.ndarray:
        """Indices of edges with a single adjacent triangle."""
        return np.flatnonzero(self.edge_tris[:, 1] < 0)

    def bounds(self) -> tuple[float, float, float, float]:
        return (
            float(self.nodes[:, 0].min()),
            float(self.nodes[:, 1].min()),
            float(self.nodes[:, 0].max()),
            float(self.nodes[:, 1].max()),
        )

    def z_range(self) -> tuple[float, float]:
        return float(self.nodes[:, 2].min()), float(self.nodes[:, 2].max())

    def centroids(self) -> np.ndarray:
        return self.nodes[self.triangles, :2].mean(axis=1)

    def area_2d(self) -> float:
        return float(np.abs(self._signed_area2()).sum() / 2.0)

    def has_edge(self, a: int, b: int) -> bool:
        lo, hi = (a, b) if a < b else (b, a)
        i = int(np.searchsorted(self.edges[:, 0], lo))
        while i < len(self.edges) and self.edges[i, 0] == lo:
            if self.edges[i, 1] == hi:
                return True
            i += 1
        return False

    # -- geometry caches ---------------------------------------------------------------
    def triangle_geoms(self) -> np.ndarray:
        if self._tri_geoms is None:
            p = self.nodes[self.triangles, :2]
            ring = np.concatenate([p, p[:, :1, :]], axis=1)  # close rings
            self._tri_geoms = shapely.polygons(ring)
        return self._tri_geoms

    def triangle_tree(self) -> shapely.STRtree:
        if self._tri_tree is None:
            self._tri_tree = shapely.STRtree(self.triangle_geoms())
        return self._tri_tree

    def edge_geoms(self) -> np.ndarray:
        if self._edge_geoms is None:
            self._edge_geoms = shapely.linestrings(self.nodes[self.edges, :2])
        return self._edge_geoms

    def edge_tree(self) -> shapely.STRtree:
        if self._edge_tree is None:
            self._edge_tree = shapely.STRtree(self.edge_geoms())
        return self._edge_tree

    def hull(self) -> BaseGeometry:
        """Outer boundary (and holes) of the triangulated area as a (Multi)Polygon."""
        return shapely.unary_union(self.triangle_geoms())

    # -- queries -----------------------------------------------------------------------
    def locate(self, x, y) -> np.ndarray:
        """Triangle index containing each point (-1 when outside)."""
        x = np.atleast_1d(np.asarray(x, dtype=np.float64))
        y = np.atleast_1d(np.asarray(y, dtype=np.float64))
        pts = shapely.points(x, y)
        inp, tri = self.triangle_tree().query(pts, predicate="intersects")
        out = -np.ones(len(x), dtype=np.int64)
        if len(inp):
            # a point on a shared edge hits two triangles: keep the first
            _, first = np.unique(inp, return_index=True)
            out[inp[first]] = tri[first]
        return out

    def elevation_at(self, x, y) -> np.ndarray:
        """Barycentric elevation at (x, y); NaN outside the TIN. Vectorised."""
        x = np.atleast_1d(np.asarray(x, dtype=np.float64))
        y = np.atleast_1d(np.asarray(y, dtype=np.float64))
        t = self.locate(x, y)
        z = np.full(len(x), np.nan)
        ok = t >= 0
        if ok.any():
            z[ok] = self._barycentric_z(t[ok], x[ok], y[ok])
        return z

    def _barycentric_z(self, t: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        a, b, c = (self.nodes[self.triangles[t, k]] for k in range(3))
        det = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
        wa = ((b[:, 1] - c[:, 1]) * (x - c[:, 0]) + (c[:, 0] - b[:, 0]) * (y - c[:, 1])) / det
        wb = ((c[:, 1] - a[:, 1]) * (x - c[:, 0]) + (a[:, 0] - c[:, 0]) * (y - c[:, 1])) / det
        wc = 1.0 - wa - wb
        return wa * a[:, 2] + wb * b[:, 2] + wc * c[:, 2]

    def sample_line(self, coords: np.ndarray, include_vertices: bool = True) -> "LineSample":
        """Ground line along a 2-D polyline.

        One sample at every TIN edge crossing plus the polyline vertices (legacy Find_RL +
        Find_Int_RL). Samples outside the TIN have z = NaN.
        """
        coords = np.asarray(coords, dtype=np.float64)[:, :2]
        if len(coords) < 2:
            raise ValueError("need at least two vertices")
        line = LineString(coords)
        d_list, xy_list, z_list = [], [], []

        idx = self.edge_tree().query(line, predicate="intersects")
        if len(idx):
            egeoms = self.edge_geoms()[idx]
            inter = shapely.intersection(line, egeoms)
            pxy, gi = shapely.get_coordinates(inter, return_index=True)
            if len(pxy):
                pts = shapely.points(pxy[:, 0], pxy[:, 1])
                e_sel = egeoms[gi]
                t = shapely.line_locate_point(e_sel, pts) / shapely.length(e_sel)
                eidx = idx[gi]
                z0 = self.nodes[self.edges[eidx, 0], 2]
                z1 = self.nodes[self.edges[eidx, 1], 2]
                z = z0 + np.clip(t, 0.0, 1.0) * (z1 - z0)
                d = shapely.line_locate_point(line, pts)
                d_list.append(d)
                xy_list.append(pxy)
                z_list.append(z)

        if include_vertices:
            dv = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(coords, axis=0).T))])
            zv = self.elevation_at(coords[:, 0], coords[:, 1])
            d_list.append(dv)
            xy_list.append(coords)
            z_list.append(zv)

        d = np.concatenate(d_list)
        xy = np.vstack(xy_list)
        z = np.concatenate(z_list)
        order = np.argsort(d, kind="stable")
        d, xy, z = d[order], xy[order], z[order]
        # merge samples at (numerically) the same distance, preferring a finite z
        keep = np.ones(len(d), dtype=bool)
        tol = 1e-6 * max(1.0, float(line.length))
        last = 0
        for i in range(1, len(d)):
            if d[i] - d[last] <= tol:
                if np.isnan(z[last]) and not np.isnan(z[i]):
                    z[last] = z[i]
                keep[i] = False
            else:
                last = i
        return LineSample(distance=d[keep], xy=xy[keep], z=z[keep])

    # -- editing -----------------------------------------------------------------------
    def keep_triangles(self, mask: np.ndarray) -> "TIN":
        """New TIN with the selected triangles; unreferenced nodes are dropped and re-indexed."""
        tri = self.triangles[np.asarray(mask, dtype=bool)]
        if len(tri) == 0:
            raise TinError("clipping removed every triangle")
        used, inverse = np.unique(tri.reshape(-1), return_inverse=True)
        return TIN(self.nodes[used], np.asarray(inverse).reshape(tri.shape))


@dataclass
class LineSample:
    distance: np.ndarray  # along the polyline
    xy: np.ndarray  # (k, 2)
    z: np.ndarray  # NaN outside the TIN

    def __len__(self) -> int:
        return len(self.distance)

    @property
    def inside(self) -> np.ndarray:
        return ~np.isnan(self.z)


# --------------------------------------------------------------------------------------
# Boundary clipping
# --------------------------------------------------------------------------------------
def _as_polygons(geom) -> list[Polygon]:
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
        if isinstance(geom[0], BaseGeometry) or isinstance(geom[0], (list, tuple, np.ndarray)) and np.ndim(geom[0]) == 2:
            out: list[Polygon] = []
            for g in geom:
                out.extend(_as_polygons(g))
            return out
        return [Polygon(np.asarray(geom, dtype=np.float64)[:, :2])]
    raise TypeError(f"unsupported boundary geometry: {type(geom)!r}")


def clip_to_boundary(
    tin: TIN,
    boundary=None,
    voids: Sequence = (),
    mode: str = "inside",
) -> TIN:
    """Remove triangles outside the boundary and inside voids.

    mode="inside"        keep triangles whose centroid lies inside the boundary polygon(s) and
                         outside every void polygon (recommended).
    mode="legacy_cross"  legacy DeleteTriangle rule: delete only triangles having an edge that
                         intersects a boundary/void line; nothing is tested for inside/outside.
    """
    b_polys = _as_polygons(boundary)
    v_polys = _as_polygons(list(voids)) if len(voids) else []
    if not b_polys and not v_polys:
        return tin
    if mode == "inside":
        c = tin.centroids()
        keep = np.ones(len(c), dtype=bool)
        if b_polys:
            inside = np.zeros(len(c), dtype=bool)
            for p in b_polys:
                inside |= shapely.contains_xy(p, c[:, 0], c[:, 1])
            keep &= inside
        for p in v_polys:
            keep &= ~shapely.contains_xy(p, c[:, 0], c[:, 1])
        return tin.keep_triangles(keep)
    if mode == "legacy_cross":
        rings = []
        for p in b_polys + v_polys:
            rings.append(LineString(p.exterior.coords))
            rings.extend(LineString(r.coords) for r in p.interiors)
        boundary_lines = shapely.union_all(rings)
        hit = tin.edge_tree().query(boundary_lines, predicate="intersects")
        bad_edges = np.zeros(tin.n_edges, dtype=bool)
        bad_edges[hit] = True
        bad_tri = bad_edges[tin.tri_edges].any(axis=1)
        return tin.keep_triangles(~bad_tri)
    raise ValueError(f"unknown boundary mode {mode!r}")


# --------------------------------------------------------------------------------------
# Building a TIN from survey data
# --------------------------------------------------------------------------------------
@dataclass
class TinIssue:
    kind: str  # 'crossing_features' | 'duplicate_points' | 'dropped_segment'
    x: float
    y: float
    message: str


@dataclass
class TinResult:
    tin: TIN
    issues: list[TinIssue] = field(default_factory=list)
    dedupe: DedupeReport | None = None
    stats: dict = field(default_factory=dict)
    node_source: np.ndarray | None = None  # per node: 0 survey point, 1 feature vertex, 2 steiner


def _segments_from_lines(
    lines: Sequence[np.ndarray], base_index: int
) -> tuple[np.ndarray, np.ndarray]:
    """Stack feature-line vertices; return (vertices (k,3), segments (s,2) into stacked order)."""
    verts, segs = [], []
    offset = base_index
    for c in lines:
        c = np.asarray(c, dtype=np.float64)
        if c.ndim != 2 or len(c) < 2:
            continue
        if c.shape[1] == 2:
            c = np.column_stack([c, np.zeros(len(c))])
        verts.append(c[:, :3])
        k = len(c)
        s = np.column_stack([np.arange(offset, offset + k - 1), np.arange(offset + 1, offset + k)])
        segs.append(s)
        offset += k
    if not verts:
        return np.zeros((0, 3)), np.zeros((0, 2), dtype=np.int64)
    return np.vstack(verts), np.vstack(segs).astype(np.int64)


def _steiner_z(px: float, py: float, segments: np.ndarray, xyz: np.ndarray, tol: float) -> float:
    """Elevation for a Steiner vertex: mean of the interpolated Z on segments passing through it."""
    if not len(segments):
        return float("nan")
    pa = xyz[segments[:, 0]]
    pb = xyz[segments[:, 1]]
    dx, dy = pb[:, 0] - pa[:, 0], pb[:, 1] - pa[:, 1]
    L2 = dx * dx + dy * dy
    L2[L2 == 0] = 1e-30
    t = np.clip(((px - pa[:, 0]) * dx + (py - pa[:, 1]) * dy) / L2, 0.0, 1.0)
    qx, qy = pa[:, 0] + t * dx, pa[:, 1] + t * dy
    dist = np.hypot(qx - px, qy - py)
    on = dist <= max(tol * 2, 1e-6)
    if not on.any():
        return float("nan")
    return float(np.mean(pa[on, 2] + t[on] * (pb[on, 2] - pa[on, 2])))


def build_tin(
    points: PointSet,
    feature_lines: Sequence[np.ndarray] = (),
    boundary=None,
    voids: Sequence = (),
    *,
    dedupe_tol: float = 0.001,
    drop_zero_z: bool = False,
    boundary_mode: str = "inside",
    triangulator: Triangulator | None = None,
) -> TinResult:
    """Full legacy pipeline: points + feature vertices -> dedupe -> CDT -> clip.

    feature_lines  sequence of (k, 3) arrays; vertices become TIN nodes and consecutive vertex
                   pairs become constraint segments (legacy Features file). Vertices with Z = 0
                   between known vertices are interpolated (legacy rule).
    boundary       polygon (coords array or shapely Polygon/MultiPolygon); voids likewise.
    """
    triangulator = triangulator or TriangleAdapter()
    issues: list[TinIssue] = []

    # 1. gather nodes ----------------------------------------------------------------
    n_survey = len(points)
    lines = [fill_zero_z_along_line(np.asarray(c, dtype=np.float64)) for c in feature_lines]
    fverts, fsegs = _segments_from_lines(lines, base_index=n_survey)
    all_xyz = np.vstack([points.xyz, fverts]) if len(fverts) else points.xyz.copy()
    source = np.concatenate([np.zeros(n_survey, np.int8), np.ones(len(fverts), np.int8)])

    # 2. dedupe -----------------------------------------------------------------------
    deduped, report = dedupe(PointSet(all_xyz), tol=dedupe_tol, drop_zero_z=drop_zero_z)
    if len(deduped) < 3:
        raise TinError("at least three distinct points are required")
    keys = np.round(all_xyz[:, :2] / dedupe_tol).astype(np.int64)
    kept_keys = np.round(deduped.xyz[:, :2] / dedupe_tol).astype(np.int64)
    key_to_idx = {(int(a), int(b)): i for i, (a, b) in enumerate(kept_keys)}
    orig_to_kept = np.array(
        [key_to_idx.get((int(a), int(b)), -1) for a, b in keys], dtype=np.int64
    )
    node_source = np.full(len(deduped), 2, dtype=np.int8)
    for orig in range(len(all_xyz) - 1, -1, -1):  # first occurrence wins
        k = orig_to_kept[orig]
        if k >= 0:
            node_source[k] = source[orig]
    for a, b in report.duplicate_pairs[:1000]:
        issues.append(
            TinIssue(
                "duplicate_points",
                float(all_xyz[b, 0]),
                float(all_xyz[b, 1]),
                f"point {b} duplicates point {a} (tol {dedupe_tol})",
            )
        )

    # 3. segments ---------------------------------------------------------------------
    segments = np.zeros((0, 2), dtype=np.int64)
    if len(fsegs):
        s = orig_to_kept[fsegs]
        valid = (s[:, 0] >= 0) & (s[:, 1] >= 0) & (s[:, 0] != s[:, 1])
        for i in np.flatnonzero(~valid)[:200]:
            a = all_xyz[fsegs[i, 0]]
            issues.append(
                TinIssue(
                    "dropped_segment",
                    float(a[0]),
                    float(a[1]),
                    "feature segment collapsed by de-duplication",
                )
            )
        s = np.sort(s[valid], axis=1)
        segments = np.unique(s, axis=0) if len(s) else s

    # 4. triangulate ------------------------------------------------------------------
    verts, tris = triangulator.triangulate(
        deduped.xyz[:, :2], segments if len(segments) else None
    )
    n_in = len(deduped)
    n_out = len(verts)
    nodes = np.empty((n_out, 3))
    nodes[:, :2] = verts
    nodes[:n_in, 2] = deduped.z
    if n_out > n_in:
        for j, (px, py) in enumerate(verts[n_in:]):
            z_est = _steiner_z(px, py, segments, deduped.xyz, dedupe_tol)
            if np.isnan(z_est):
                d = np.hypot(deduped.x - px, deduped.y - py)
                z_est = float(deduped.z[np.argmin(d)])
            nodes[n_in + j, 2] = z_est
            issues.append(
                TinIssue(
                    "crossing_features",
                    float(px),
                    float(py),
                    "feature lines cross here; a node was inserted (legacy: redundant feature)",
                )
            )
        node_source = np.concatenate([node_source, np.full(n_out - n_in, 2, np.int8)])

    tin = TIN(nodes, tris)
    raw_triangles = tin.n_triangles

    # 5. clip ---------------------------------------------------------------------------
    if boundary is not None or len(voids):
        tin = clip_to_boundary(tin, boundary, voids, mode=boundary_mode)
        lookup = {
            (round(float(x), 9), round(float(y), 9)): int(s)
            for (x, y), s in zip(nodes[:, :2], node_source)
        }
        node_source = np.array(
            [lookup.get((round(float(x), 9), round(float(y), 9)), 2) for x, y in tin.nodes[:, :2]],
            dtype=np.int8,
        )

    stats = {
        "input_points": n_survey,
        "feature_vertices": int(len(fverts)),
        "nodes": tin.n_nodes,
        "triangles": tin.n_triangles,
        "edges": tin.n_edges,
        "segments": int(len(segments)),
        "steiner_points": int(n_out - n_in),
        "triangles_removed_by_boundary": int(raw_triangles - tin.n_triangles),
        "duplicates_removed": report.removed_duplicates,
        "zero_z_removed": report.removed_zero_z,
    }
    return TinResult(tin=tin, issues=issues, dedupe=report, stats=stats, node_source=node_source)
