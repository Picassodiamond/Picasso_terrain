"""Triangulated irregular network: build, topology, clipping and sampling.

Replaces `frmtriangulate.frm` + the Triangle DLL, and the `Find_RL` / `Find_Int_RL`
routines used by the profile / cross-section generators.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry

from .constraints import Constraint, ConstraintSet
from .points import PointSet, DedupeReport, dedupe, fill_zero_z_along_line


class TinError(RuntimeError):
    """Triangulation could not be performed (too few points, collinear input...)."""


# --------------------------------------------------------------------------------------
# Triangulator interface + adapters
# --------------------------------------------------------------------------------------
class Triangulator(Protocol):
    """Constrained Delaunay triangulation of 2-D points with optional segment constraints.

    Returns (vertices (k, 2), triangles (m, 3), segments_out (s, 2) | None). k >= len(xy); extra
    vertices (Steiner points) are appended after the input vertices and only appear where
    constraint segments cross. `segments_out` are the constraint edges actually present in the
    output (input segments split at Steiner points), or None when the backend cannot report them.
    """

    def triangulate(
        self, xy: np.ndarray, segments: np.ndarray | None
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]: ...


class TriangleAdapter:
    """Shewchuk Triangle via the `triangle` package - the same algorithm as the legacy DLL.

    Flags: Q quiet, c keep the convex hull (legacy convex=1; without it open feature lines
    would let Triangle carve away the whole mesh), p PSLG when segments are given.
    No quality (q) or area (a) refinement, so no Steiner points except at segment crossings.
    Coordinates are shifted to a local origin before triangulation: Triangle's predicates are
    exact but survey coordinates in the millions of metres waste most of a double's mantissa.
    """

    def triangulate(
        self, xy: np.ndarray, segments: np.ndarray | None
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
        import triangle  # local import keeps the engine importable without it

        xy = np.ascontiguousarray(xy, dtype=np.float64)
        origin = xy.min(axis=0)
        data = {"vertices": np.ascontiguousarray(xy - origin)}
        opts = "Qc"
        has_segs = segments is not None and len(segments) > 0
        if has_segs:
            data["segments"] = np.ascontiguousarray(segments, dtype=np.int32)
            # marker 2 = our constraints; Triangle marks the convex hull segments it adds for 'c' with 1
            data["segment_markers"] = np.full((len(segments), 1), 2, dtype=np.int32)
            opts += "p"
        try:
            out = triangle.triangulate(data, opts)
        except Exception as exc:  # pragma: no cover - depends on native lib
            raise TinError(f"Triangle failed: {exc}") from exc
        if "triangles" not in out or len(out["triangles"]) == 0:
            raise TinError("Triangle produced no triangles (degenerate or collinear input?)")
        verts = np.asarray(out["vertices"], dtype=np.float64) + origin
        verts[: len(xy)] = xy  # keep the input coordinates bit-exact
        segs_out = None
        if has_segs and "segments" in out and len(out["segments"]):
            so = np.asarray(out["segments"], dtype=np.int64)
            mk = np.asarray(out.get("segment_markers", np.full((len(so), 1), 2))).reshape(-1)
            segs_out = so[mk == 2]
        return verts, np.asarray(out["triangles"], dtype=np.int64), segs_out


class ScipyDelaunayAdapter:
    """Unconstrained fallback (Qhull). Ignores segments - use only when no constraints exist."""

    def triangulate(
        self, xy: np.ndarray, segments: np.ndarray | None
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
        from scipy.spatial import Delaunay

        if segments is not None and len(segments):
            raise TinError("ScipyDelaunayAdapter cannot honour constraint segments")
        try:
            d = Delaunay(np.asarray(xy, dtype=np.float64))
        except Exception as exc:
            raise TinError(f"Qhull failed: {exc}") from exc
        return np.asarray(xy, dtype=np.float64), np.asarray(d.simplices, dtype=np.int64), None


# --------------------------------------------------------------------------------------
# Uniform-grid helpers (vectorised CSR expansion)
# --------------------------------------------------------------------------------------
def _expand_cells(i0: np.ndarray, i1: np.ndarray, j0: np.ndarray, j1: np.ndarray, nx: int, ids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """All (cell id, owner id) pairs for inclusive cell ranges [i0..i1] x [j0..j1] per owner."""
    di = i1 - i0 + 1
    dj = j1 - j0 + 1
    count = di * dj
    total = int(count.sum())
    if total == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=ids.dtype)
    rep = np.repeat(np.arange(len(ids)), count)
    start = np.cumsum(count) - count
    k = np.arange(total) - start[rep]
    djr = dj[rep]
    ii = i0[rep] + k // djr
    jj = j0[rep] + k % djr
    return jj * nx + ii, ids[rep]


def _gather(offsets: np.ndarray, items: np.ndarray, cell_ids: np.ndarray, owners: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """CSR lookup: for every (cell, owner) return (owner, item) for all items stored in that cell."""
    st = offsets[cell_ids]
    cnt = offsets[cell_ids + 1] - st
    total = int(cnt.sum())
    if total == 0:
        return np.zeros(0, dtype=owners.dtype), np.zeros(0, dtype=items.dtype)
    rep = np.repeat(np.arange(len(cell_ids)), cnt)
    start = np.cumsum(cnt) - cnt
    k = np.arange(total) - start[rep]
    return owners[rep], items[st[rep] + k]


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
        self._grid_data: dict | None = None

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
        """Outer boundary (and holes) of the triangulated area as a (Multi)Polygon.

        Built from the boundary edges only (polygonize + one point-in-TIN test per face), so it
        costs a few thousand small geometries instead of one polygon per triangle."""
        b = self.boundary_edges
        if not len(b):
            return Polygon()
        lines = shapely.linestrings(self.nodes[self.edges[b], :2])
        faces = shapely.get_parts(shapely.polygonize(lines))
        if len(faces):
            rp = shapely.get_coordinates(shapely.point_on_surface(faces))
            inside = self.locate(rp[:, 0], rp[:, 1]) >= 0
            if inside.any():
                return shapely.unary_union(faces[inside])
        return shapely.unary_union(self.triangle_geoms())  # pragma: no cover - degenerate meshes only

    # -- spatial index -------------------------------------------------------------------
    def _grid(self) -> dict:
        """Uniform grid over triangle bounding boxes (CSR arrays; ~2-3 entries per triangle).

        Replaces the STRtree of shapely polygons for point location and edge candidates: about
        16-24 bytes per triangle instead of a few hundred, and pure numpy queries."""
        if self._grid_data is None:
            m = self.n_triangles
            p = self.nodes[self.triangles, :2]
            bmin = p.min(axis=1)
            bmax = p.max(axis=1)
            x0, y0, x1, y1 = self.bounds()
            w = max(x1 - x0, 1e-9)
            h = max(y1 - y0, 1e-9)
            cell = max(np.sqrt(w * h / m) * 2.0, 1e-9)
            nx, ny = int(w / cell) + 1, int(h / cell) + 1
            while nx * ny > 4 * m + 16:  # very elongated extents: coarsen rather than allocate a huge grid
                cell *= 1.5
                nx, ny = int(w / cell) + 1, int(h / cell) + 1
            i0 = np.clip(((bmin[:, 0] - x0) / cell).astype(np.int64), 0, nx - 1)
            i1 = np.clip(((bmax[:, 0] - x0) / cell).astype(np.int64), 0, nx - 1)
            j0 = np.clip(((bmin[:, 1] - y0) / cell).astype(np.int64), 0, ny - 1)
            j1 = np.clip(((bmax[:, 1] - y0) / cell).astype(np.int64), 0, ny - 1)
            cells, tris = _expand_cells(i0, i1, j0, j1, nx, np.arange(m, dtype=np.int64))
            order = np.argsort(cells, kind="stable")
            cells = cells[order]
            tris = tris[order].astype(np.int32 if m < 2**31 - 1 else np.int64)
            offsets = np.searchsorted(cells, np.arange(nx * ny + 1)).astype(np.int64)
            self._grid_data = {"x0": x0, "y0": y0, "cell": float(cell), "nx": nx, "ny": ny, "offsets": offsets, "tris": tris}
        return self._grid_data

    def _cells_of(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        g = self._grid()
        ci = np.floor((x - g["x0"]) / g["cell"]).astype(np.int64)
        cj = np.floor((y - g["y0"]) / g["cell"]).astype(np.int64)
        return ci, cj

    # -- queries -----------------------------------------------------------------------
    def locate(self, x, y) -> np.ndarray:
        """Triangle index containing each point (-1 when outside). Vectorised grid + barycentric test."""
        x = np.atleast_1d(np.asarray(x, dtype=np.float64))
        y = np.atleast_1d(np.asarray(y, dtype=np.float64))
        out = -np.ones(len(x), dtype=np.int64)
        if not len(x):
            return out
        g = self._grid()
        ci, cj = self._cells_of(x, y)
        ok = (ci >= 0) & (ci < g["nx"]) & (cj >= 0) & (cj < g["ny"])
        q = np.flatnonzero(ok)
        if not len(q):
            return out
        qi, tri = _gather(g["offsets"], g["tris"], cj[q] * g["nx"] + ci[q], q)
        if not len(tri):
            return out
        tri = tri.astype(np.int64)
        a = self.nodes[self.triangles[tri, 0], :2]
        b = self.nodes[self.triangles[tri, 1], :2]
        c = self.nodes[self.triangles[tri, 2], :2]
        px, py = x[qi], y[qi]
        d1 = (b[:, 0] - a[:, 0]) * (py - a[:, 1]) - (b[:, 1] - a[:, 1]) * (px - a[:, 0])
        d2 = (c[:, 0] - b[:, 0]) * (py - b[:, 1]) - (c[:, 1] - b[:, 1]) * (px - b[:, 0])
        d3 = (a[:, 0] - c[:, 0]) * (py - c[:, 1]) - (a[:, 1] - c[:, 1]) * (px - c[:, 0])
        # triangles are CCW (area2 > 0): inside when all three are non-negative, with a relative slack
        area2 = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
        eps = 1e-10 * np.abs(area2) + 1e-300
        hit = (d1 >= -eps) & (d2 >= -eps) & (d3 >= -eps)
        if hit.any():
            qh, th = qi[hit], tri[hit]
            _, first = np.unique(qh, return_index=True)  # a point on a shared edge: keep the first
            out[qh[first]] = th[first]
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

    def sample_line(self, coords: np.ndarray, include_vertices: bool = True) -> LineSample:
        """Ground line along a 2-D polyline.

        One sample at every TIN edge crossing plus the polyline vertices (legacy Find_RL +
        Find_Int_RL). Samples outside the TIN have z = NaN.

        Each polyline segment is handled on its own: the edge tree is queried with all segments
        in one bulk call and every segment/edge pair is intersected analytically, so the cost is
        linear in the number of crossings. (Intersecting the whole polyline as one shapely
        LineString made every crossing walk the full line - quadratic on a 100 km centreline.)
        """
        coords = np.asarray(coords, dtype=np.float64)[:, :2]
        if len(coords) < 2:
            raise ValueError("need at least two vertices")
        seg_vec = np.diff(coords, axis=0)
        seg_len = np.hypot(seg_vec[:, 0], seg_vec[:, 1])
        cum = np.concatenate([[0.0], np.cumsum(seg_len)])
        total = float(cum[-1])
        d_list, xy_list, z_list = [], [], []

        live = seg_len > 0
        if live.any():
            si, ei = self._edge_candidates(coords, seg_vec, seg_len, np.flatnonzero(live))
            if len(si):
                p = coords[si]
                r = seg_vec[si]
                q = self.nodes[self.edges[ei, 0], :2]
                sv = self.nodes[self.edges[ei, 1], :2] - q
                denom = r[:, 0] * sv[:, 1] - r[:, 1] * sv[:, 0]
                scale = np.maximum(seg_len[si] * np.hypot(sv[:, 0], sv[:, 1]), 1e-300)
                ok = np.abs(denom) > 1e-12 * scale  # parallel / collinear edges: their end nodes are
                # caught through the neighbouring edges, so nothing is lost by skipping them
                qp = q - p
                with np.errstate(divide="ignore", invalid="ignore"):
                    t = (qp[:, 0] * sv[:, 1] - qp[:, 1] * sv[:, 0]) / denom  # along the segment
                    u = (qp[:, 0] * r[:, 1] - qp[:, 1] * r[:, 0]) / denom  # along the edge
                eps = 1e-9
                ok &= (t >= -eps) & (t <= 1 + eps) & (u >= -eps) & (u <= 1 + eps)
                if ok.any():
                    t = np.clip(t[ok], 0.0, 1.0)
                    u = np.clip(u[ok], 0.0, 1.0)
                    si, ei = si[ok], ei[ok]
                    z0 = self.nodes[self.edges[ei, 0], 2]
                    z1 = self.nodes[self.edges[ei, 1], 2]
                    d_list.append(cum[si] + t * seg_len[si])
                    xy_list.append(coords[si] + t[:, None] * seg_vec[si])
                    z_list.append(z0 + u * (z1 - z0))

        if include_vertices:
            zv = self.elevation_at(coords[:, 0], coords[:, 1])
            d_list.append(cum)
            xy_list.append(coords)
            z_list.append(zv)

        if not d_list:
            return LineSample(distance=np.zeros(0), xy=np.zeros((0, 2)), z=np.zeros(0))
        d = np.concatenate(d_list)
        xy = np.vstack(xy_list)
        z = np.concatenate(z_list)
        order = np.argsort(d, kind="stable")
        d, xy, z = d[order], xy[order], z[order]
        # merge samples at (numerically) the same distance, preferring a finite z
        tol = 1e-6 * max(1.0, total)
        group = np.concatenate([[0], np.cumsum(np.diff(d) > tol)])
        pick = np.lexsort((np.isnan(z), group))  # per group: finite z first, then original order
        first = np.r_[True, group[pick][1:] != group[pick][:-1]]
        keep = np.sort(pick[first])
        return LineSample(distance=d[keep], xy=xy[keep], z=z[keep])

    def _edge_candidates(self, coords: np.ndarray, seg_vec: np.ndarray, seg_len: np.ndarray, idx: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(segment index, edge index) pairs whose bounding boxes may intersect, via the triangle
        grid. Long segments are cut into pieces of a few cells so a long diagonal does not pull in
        every triangle under its bounding box."""
        g = self._grid()
        L = seg_len[idx]
        n_pieces = np.maximum(1, np.ceil(L / (8.0 * g["cell"]))).astype(np.int64)
        total = int(n_pieces.sum())
        rep = np.repeat(np.arange(len(idx)), n_pieces)
        start = np.cumsum(n_pieces) - n_pieces
        k = np.arange(total) - start[rep]
        seg_rep = idx[rep]
        f0 = k / n_pieces[rep]
        f1 = (k + 1) / n_pieces[rep]
        pa = coords[seg_rep] + f0[:, None] * seg_vec[seg_rep]
        pb = coords[seg_rep] + f1[:, None] * seg_vec[seg_rep]
        bmin = np.minimum(pa, pb)
        bmax = np.maximum(pa, pb)
        # pieces entirely outside the grid contribute nothing
        inside = (bmax[:, 0] >= g["x0"]) & (bmax[:, 1] >= g["y0"]) & (bmin[:, 0] <= g["x0"] + g["nx"] * g["cell"]) & (bmin[:, 1] <= g["y0"] + g["ny"] * g["cell"])
        if not inside.any():
            return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
        bmin, bmax, seg_rep = bmin[inside], bmax[inside], seg_rep[inside]
        i0 = np.clip(np.floor((bmin[:, 0] - g["x0"]) / g["cell"]).astype(np.int64), 0, g["nx"] - 1)
        i1 = np.clip(np.floor((bmax[:, 0] - g["x0"]) / g["cell"]).astype(np.int64), 0, g["nx"] - 1)
        j0 = np.clip(np.floor((bmin[:, 1] - g["y0"]) / g["cell"]).astype(np.int64), 0, g["ny"] - 1)
        j1 = np.clip(np.floor((bmax[:, 1] - g["y0"]) / g["cell"]).astype(np.int64), 0, g["ny"] - 1)
        cells, owners = _expand_cells(i0, i1, j0, j1, g["nx"], seg_rep)
        si_c, tri_c = _gather(g["offsets"], g["tris"], cells, owners)
        if not len(tri_c):
            return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
        e3 = self.tri_edges[tri_c.astype(np.int64)]  # (k, 3)
        si = np.repeat(si_c, 3)
        ei = e3.reshape(-1)
        key = np.unique(si * self.n_edges + ei)
        return key // self.n_edges, key % self.n_edges

    # -- editing -----------------------------------------------------------------------
    def keep_triangles(self, mask: np.ndarray, return_index: bool = False):
        """New TIN with the selected triangles; unreferenced nodes are dropped and re-indexed.

        With return_index=True also returns the old node index of every new node."""
        tri = self.triangles[np.asarray(mask, dtype=bool)]
        if len(tri) == 0:
            raise TinError("clipping removed every triangle")
        used, inverse = np.unique(tri.reshape(-1), return_inverse=True)
        out = TIN(self.nodes[used], np.asarray(inverse).reshape(tri.shape))
        return (out, used) if return_index else out

    # -- metrics ------------------------------------------------------------------------
    def edge_lengths(self) -> np.ndarray:
        d = self.nodes[self.edges[:, 1], :2] - self.nodes[self.edges[:, 0], :2]
        return np.hypot(d[:, 0], d[:, 1])

    def triangle_min_angles(self) -> np.ndarray:
        """Smallest interior angle of every triangle in degrees (2-D)."""
        L = self.edge_lengths()[self.tri_edges]  # (m, 3): |n0n1|, |n1n2|, |n2n0|
        a, b, c = L[:, 0], L[:, 1], L[:, 2]
        with np.errstate(invalid="ignore", divide="ignore"):
            angs = np.stack([
                np.arccos(np.clip((a * a + c * c - b * b) / (2 * a * c), -1, 1)),  # at n0
                np.arccos(np.clip((a * a + b * b - c * c) / (2 * a * b), -1, 1)),  # at n1
                np.arccos(np.clip((b * b + c * c - a * a) / (2 * b * c), -1, 1)),  # at n2
            ], axis=1)
        return np.degrees(np.nan_to_num(angs.min(axis=1), nan=0.0))

    def edge_index(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """Edge index of node pairs (vectorised), -1 when no such edge exists."""
        a = np.asarray(a, dtype=np.int64)
        b = np.asarray(b, dtype=np.int64)
        lo, hi = np.minimum(a, b), np.maximum(a, b)
        n = self.n_nodes
        keys = self.edges[:, 0] * n + self.edges[:, 1]  # edges are sorted lexicographically
        q = lo * n + hi
        pos = np.searchsorted(keys, q)
        pos = np.clip(pos, 0, len(keys) - 1)
        found = keys[pos] == q
        return np.where(found, pos, -1)

    def validate(self, segments: np.ndarray | None = None) -> list[str]:
        """Structural checks; returns a list of problems (empty when the TIN is sound).

        segments  optional (s, 2) node pairs that must exist as edges (constraint check)
        """
        problems: list[str] = []
        if not np.isfinite(self.nodes).all():
            problems.append("non-finite node coordinates")
        area2 = self._signed_area2()
        flat = np.abs(area2) <= 1e-12 * max(float(np.abs(area2).max()), 1e-300)
        if flat.any():
            problems.append(f"{int(flat.sum())} zero-area (degenerate) triangles")
        if (area2 < 0).any():
            problems.append("clockwise triangles present")
        srt = np.sort(self.triangles, axis=1)
        if len(np.unique(srt, axis=0)) != len(srt):
            problems.append("duplicate triangles")
        if (srt[:, 0] == srt[:, 1]).any() or (srt[:, 1] == srt[:, 2]).any():
            problems.append("triangles with repeated vertices")
        cnt = np.bincount(self.tri_edges.reshape(-1), minlength=self.n_edges)
        if (cnt > 2).any():
            problems.append(f"{int((cnt > 2).sum())} non-manifold edges (shared by more than two triangles)")
        if self.triangles.max() >= self.n_nodes:
            problems.append("triangle references a missing node")
        if segments is not None and len(segments):
            seg = np.asarray(segments, dtype=np.int64)
            missing = int((self.edge_index(seg[:, 0], seg[:, 1]) < 0).sum())
            if missing:
                problems.append(f"{missing} constraint segments are not TIN edges")
        return problems


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
# Boundary clipping (post-hoc; prefer passing the rings to build_tin so they become constraints)
# --------------------------------------------------------------------------------------
def clip_to_boundary(
    tin: TIN,
    boundary=None,
    voids: Sequence = (),
    mode: str = "inside",
) -> TIN:
    """Remove triangles outside the boundary and inside voids.

    mode="inside"        keep triangles by the innermost ring containing their centroid
                         (boundary keeps, hole removes; rings may nest: islands inside holes).
    mode="legacy_cross"  legacy DeleteTriangle rule: delete only triangles having an edge that
                         intersects a boundary/void line; nothing is tested for inside/outside.
    Triangles may still straddle the rings here because they were not constraints during
    triangulation; build_tin(..., boundary=..., voids=...) avoids that.
    """
    cs = ConstraintSet.from_legacy((), boundary, voids)
    if not cs.rings:
        return tin
    if mode == "inside":
        keep, _ = cs.classify(tin.centroids())
        return tin.keep_triangles(keep)
    if mode == "legacy_cross":
        return tin.keep_triangles(_legacy_cross_mask(tin, cs.polygons()))
    raise ValueError(f"unknown boundary mode {mode!r}")


# --------------------------------------------------------------------------------------
# Building a TIN from survey data
# --------------------------------------------------------------------------------------
@dataclass
class TinIssue:
    kind: str  # crossing_features | duplicate_points | dropped_segment | invalid_ring | self_intersection | validation
    x: float
    y: float
    message: str


REJECT_REASONS = {1: "outside_boundary", 2: "hole", 3: "long_edge", 4: "low_quality", 5: "legacy_crossing"}


@dataclass
class RejectedTriangles:
    """Triangles removed from the raw constrained Delaunay triangulation, kept for review."""

    xyz: np.ndarray  # (r, 3, 3) vertex coordinates
    reason: np.ndarray  # (r,) int8 codes, see REJECT_REASONS

    def __len__(self) -> int:
        return len(self.reason)

    def counts(self) -> dict[str, int]:
        return {name: int((self.reason == code).sum()) for code, name in REJECT_REASONS.items() if (self.reason == code).any()}

    def reason_names(self) -> list[str]:
        return [REJECT_REASONS.get(int(r), "unknown") for r in self.reason]

    @classmethod
    def empty(cls) -> "RejectedTriangles":
        return cls(np.zeros((0, 3, 3)), np.zeros(0, dtype=np.int8))


@dataclass
class TinResult:
    tin: TIN
    issues: list[TinIssue] = field(default_factory=list)
    dedupe: DedupeReport | None = None
    stats: dict = field(default_factory=dict)
    # per node: 0 survey point, 1 breakline vertex, 2 Steiner point (constraint crossing), 3 ring vertex
    node_source: np.ndarray | None = None
    rejected: RejectedTriangles | None = None
    constraints: ConstraintSet | None = None
    validation: list[str] = field(default_factory=list)


def delaunay_tin(xyz: np.ndarray) -> TIN | None:
    """Unconstrained Delaunay TIN of (n, 3) points via Qhull, degenerate slivers removed.
    None when the points cannot be triangulated (fewer than three, collinear)."""
    from scipy.spatial import Delaunay

    from .constraints import is_collinear

    xyz = np.asarray(xyz, dtype=np.float64).reshape(-1, 3)
    if len(xyz) < 3 or is_collinear(xyz[:, :2]):
        return None
    try:
        d = Delaunay(xyz[:, :2])
    except Exception:
        return None
    tin = TIN(xyz, d.simplices)
    area2 = np.abs(tin._signed_area2())
    good = area2 > 1e-12 * max(float(area2.max()), 1e-300)
    if not good.all():
        if not good.any():
            return None
        tin = tin.keep_triangles(good)
    return tin


def surface_z(survey_xyz: np.ndarray, qxy: np.ndarray) -> np.ndarray:
    """Elevation of query points from an unconstrained Delaunay surface of the survey points;
    the nearest survey point is used outside the hull. Used to give constraint vertices (2-D
    boundaries, holes, breaklines without levels) a plausible Z."""
    qxy = np.asarray(qxy, dtype=np.float64).reshape(-1, 2)
    z = np.full(len(qxy), np.nan)
    if len(qxy) == 0:
        return z
    prelim = delaunay_tin(survey_xyz)
    if prelim is not None:
        z = prelim.elevation_at(qxy[:, 0], qxy[:, 1])
    miss = np.isnan(z)
    if miss.any() and len(survey_xyz):
        from scipy.spatial import cKDTree

        _, j = cKDTree(np.asarray(survey_xyz)[:, :2]).query(qxy[miss])
        z[miss] = np.asarray(survey_xyz)[j, 2]
    return z


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


def _legacy_cross_mask(tin: TIN, polys: Sequence[Polygon]) -> np.ndarray:
    """Legacy DeleteTriangle rule: triangles having an edge that intersects a ring are removed."""
    rings = []
    for p in polys:
        rings.append(LineString(p.exterior.coords))
        rings.extend(LineString(r.coords) for r in p.interiors)
    if not rings:
        return np.ones(tin.n_triangles, dtype=bool)
    hit = tin.edge_tree().query(shapely.union_all(rings), predicate="intersects")
    bad_edges = np.zeros(tin.n_edges, dtype=bool)
    bad_edges[hit] = True
    return ~bad_edges[tin.tri_edges].any(axis=1)


def filter_triangles(
    tin: TIN,
    *,
    max_edge: float | None = None,
    min_angle_deg: float = 0.0,
    protected_edges: np.ndarray | None = None,
    initial_keep: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Peel long or badly shaped triangles from the outer edge of the mesh inwards.

    Only triangles that touch the current boundary are removed, so no holes open up inside
    the data; the loop repeats until the boundary is clean. Triangles owning a protected
    (constraint) edge are never removed - a drawn boundary is exactly where long thin triangles
    are legitimate. Returns (keep, reason) with reason 3 long edge, 4 low quality, 0 kept.
    """
    m = tin.n_triangles
    keep = np.ones(m, dtype=bool) if initial_keep is None else np.asarray(initial_keep, dtype=bool).copy()
    reason = np.zeros(m, dtype=np.int8)
    if not max_edge and min_angle_deg <= 0:
        return keep, reason
    elen = tin.edge_lengths()
    long_tri = elen[tin.tri_edges].max(axis=1) > float(max_edge) if max_edge else np.zeros(m, dtype=bool)
    poor = tin.triangle_min_angles() < float(min_angle_deg) if min_angle_deg > 0 else np.zeros(m, dtype=bool)
    bad = long_tri | poor
    if protected_edges is not None and len(protected_edges):
        prot = np.zeros(tin.n_edges, dtype=bool)
        prot[np.asarray(protected_edges, dtype=np.int64)] = True
        bad &= ~prot[tin.tri_edges].any(axis=1)
    if not bad.any():
        return keep, reason
    et = tin.edge_tris
    e0 = np.maximum(et[:, 0], 0)
    e1 = np.maximum(et[:, 1], 0)
    for _ in range(1_000_000):
        k0 = np.where(et[:, 0] >= 0, keep[e0], False)
        k1 = np.where(et[:, 1] >= 0, keep[e1], False)
        bnd = k0 != k1
        owner = np.where(k0[bnd], et[bnd, 0], et[bnd, 1])
        cand = np.unique(owner[bad[owner]])
        if not len(cand):
            break
        keep[cand] = False
        reason[cand] = np.where(long_tri[cand], 3, 4)
    if not keep.any():
        raise TinError("the edge-length / quality filters removed every triangle; relax max_edge_length or min_angle_deg")
    return keep, reason


def build_tin(
    points: PointSet,
    feature_lines: Sequence[np.ndarray] = (),
    boundary=None,
    voids: Sequence = (),
    *,
    constraints: ConstraintSet | Sequence[Constraint] | None = None,
    dedupe_tol: float = 0.001,
    drop_zero_z: bool = False,
    boundary_mode: str = "inside",
    max_edge_length: float | None = None,
    max_edge_factor: float | None = None,
    min_angle_deg: float = 0.0,
    keep_rejected: bool = True,
    triangulator: Triangulator | None = None,
) -> TinResult:
    """Survey points + constraints -> de-duplicate -> constrained Delaunay -> classify -> filter.

    The constrained TIN is the authoritative terrain model: every constraint segment (boundary,
    hole, island, breakline) is a TIN edge, so no triangle ever crosses one. Closed constraints
    then decide which triangles survive by nesting (innermost ring wins), and the optional
    long-edge / min-angle filters peel poor triangles from the outer edge.

    feature_lines / boundary / voids   legacy arguments (breaklines, outer polygon(s), holes)
    constraints      ConstraintSet or list of Constraint (merged with the legacy arguments)
    dedupe_tol       points closer than this (m) collapse onto the first occurrence
    drop_zero_z      legacy: discard survey points with Z exactly 0
    boundary_mode    'inside' (constraints + nesting, recommended) or 'legacy_cross' (rings are
                     not constraints; triangles crossing a ring are deleted, nothing else)
    max_edge_length  remove edge triangles with an edge longer than this (m)
    max_edge_factor  ... or longer than factor x median edge length (used when max_edge_length is None)
    min_angle_deg    remove edge triangles whose smallest angle is below this
    keep_rejected    keep the removed triangles in the result for display
    Constraint vertices with unknown Z (NaN, or 0 as in the legacy files) take their level from
    the survey surface (interpolated along the breakline first, then from a preliminary TIN).
    """
    triangulator = triangulator or TriangleAdapter()
    issues: list[TinIssue] = []

    # 0. constraints --------------------------------------------------------------------
    cs = ConstraintSet.from_legacy(feature_lines, boundary, voids, tol=dedupe_tol)
    if constraints is not None:
        extra = constraints if isinstance(constraints, ConstraintSet) else ConstraintSet.build(constraints, tol=dedupe_tol)
        cs.rings += extra.rings
        cs.breaklines += extra.breaklines
        cs.issues += extra.issues
        cs._polys = None
    for ci in cs.issues:
        issues.append(TinIssue(ci.kind, ci.x, ci.y, ci.message))
    legacy = boundary_mode == "legacy_cross"
    if boundary_mode not in ("inside", "legacy_cross"):
        raise ValueError(f"unknown boundary mode {boundary_mode!r}")

    # 1. gather nodes ----------------------------------------------------------------
    n_survey = len(points)
    for c in cs.breaklines:
        c.coords = fill_zero_z_along_line(c.coords)
    used_constraints = ConstraintSet(rings=[] if legacy else cs.rings, breaklines=cs.breaklines)
    cverts, csegs, owner = used_constraints.segments(base_index=n_survey)
    if len(cverts):
        unknown = np.isnan(cverts[:, 2]) | (cverts[:, 2] == 0.0)
        if unknown.any():
            cverts[unknown, 2] = surface_z(points.xyz, cverts[unknown, :2])
            cverts[np.isnan(cverts[:, 2]), 2] = 0.0
    kinds_used = np.array([c.kind for c in used_constraints.all]) if len(used_constraints) else np.zeros(0, dtype=str)
    csource = np.where(kinds_used[owner] == "breakline", 1, 3).astype(np.int8) if len(cverts) else np.zeros(0, np.int8)
    all_xyz = np.vstack([points.xyz, cverts]) if len(cverts) else points.xyz.copy()
    source = np.concatenate([np.zeros(n_survey, np.int8), csource])

    # 2. dedupe -----------------------------------------------------------------------
    deduped, report = dedupe(PointSet(all_xyz), tol=dedupe_tol, drop_zero_z=drop_zero_z)
    if len(deduped) < 3:
        raise TinError("at least three distinct points are required")
    from .constraints import is_collinear

    if is_collinear(deduped.xyz[:, :2]):
        raise TinError("all points are collinear - a surface needs points spread in two dimensions")
    orig_to_kept = report.mapping if report.mapping is not None else np.arange(len(all_xyz))
    node_source = np.full(len(deduped), 2, dtype=np.int8)
    for orig in range(len(all_xyz) - 1, -1, -1):  # first occurrence wins
        k = orig_to_kept[orig]
        if k >= 0:
            node_source[k] = source[orig]
    # constraint vertices that coincide with survey points simply snap onto them (expected, e.g. a
    # detected boundary runs through survey points); only survey-vs-survey duplicates are issues
    survey_dups = [(a, b) for a, b in report.duplicate_pairs if b < n_survey]
    snapped = len(report.duplicate_pairs) - len(survey_dups)
    for a, b in survey_dups[:1000]:
        issues.append(TinIssue("duplicate_points", float(all_xyz[b, 0]), float(all_xyz[b, 1]),
                               f"point {b} duplicates point {a} (tol {dedupe_tol})"))

    # 3. segments ---------------------------------------------------------------------
    segments = np.zeros((0, 2), dtype=np.int64)
    if len(csegs):
        s = orig_to_kept[csegs]
        valid = (s[:, 0] >= 0) & (s[:, 1] >= 0) & (s[:, 0] != s[:, 1])
        for i in np.flatnonzero(~valid)[:200]:
            a = all_xyz[csegs[i, 0]]
            issues.append(TinIssue("dropped_segment", float(a[0]), float(a[1]),
                                   "constraint segment collapsed by de-duplication"))
        s = np.sort(s[valid], axis=1)
        segments = np.unique(s, axis=0) if len(s) else s

    # 4. triangulate ------------------------------------------------------------------
    verts, tris, segs_out = triangulator.triangulate(deduped.xyz[:, :2], segments if len(segments) else None)
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
            issues.append(TinIssue("crossing_features", float(px), float(py),
                                   "constraints cross here; a node was inserted (legacy: redundant feature)"))
        node_source = np.concatenate([node_source, np.full(n_out - n_in, 2, np.int8)])
    if segs_out is None:
        segs_out = segments

    tin = TIN(nodes, tris)
    raw_triangles = tin.n_triangles
    keep = np.ones(raw_triangles, dtype=bool)
    reason = np.zeros(raw_triangles, dtype=np.int8)

    # 5. classify by constraints ---------------------------------------------------------
    if legacy:
        polys = ConstraintSet(rings=cs.rings).polygons()
        if polys:
            keep = _legacy_cross_mask(tin, polys)
            reason[~keep] = 5
    elif cs.rings:
        keep, reason = cs.classify(tin.centroids())

    # 6. edge-length / quality filters ---------------------------------------------------
    median_edge = float(np.median(tin.edge_lengths())) if tin.n_edges else 0.0
    max_edge = float(max_edge_length) if max_edge_length else (float(max_edge_factor) * median_edge if max_edge_factor else None)
    protected = tin.edge_index(segs_out[:, 0], segs_out[:, 1]) if len(segs_out) else np.zeros(0, dtype=np.int64)
    protected = protected[protected >= 0]
    if max_edge or min_angle_deg > 0:
        keep2, reason2 = filter_triangles(tin, max_edge=max_edge, min_angle_deg=min_angle_deg,
                                          protected_edges=protected, initial_keep=keep)
        newly = keep & ~keep2
        reason[newly] = reason2[newly]
        keep = keep2
    if not keep.any():
        raise TinError("every triangle was rejected - check the boundary / hole constraints")

    # 7. reject + validate ---------------------------------------------------------------
    rejected = None
    if keep_rejected and not keep.all():
        rejected = RejectedTriangles(tin.nodes[tin.triangles[~keep]], reason[~keep].copy())
    final_segments = segs_out
    if not keep.all():
        tin, used = tin.keep_triangles(keep, return_index=True)
        node_source = node_source[used]
        remap = -np.ones(n_out, dtype=np.int64)
        remap[used] = np.arange(len(used))
        fs = remap[segs_out] if len(segs_out) else np.zeros((0, 2), dtype=np.int64)
        final_segments = fs[(fs >= 0).all(axis=1)] if len(fs) else fs
    validation = tin.validate(final_segments)
    for v in validation:
        issues.append(TinIssue("validation", float(tin.nodes[0, 0]), float(tin.nodes[0, 1]), v))

    n_bound = int(((reason == 1) | (reason == 2) | (reason == 5)).sum())
    n_filter = int(((reason == 3) | (reason == 4)).sum())
    stats = {
        "input_points": n_survey,
        "feature_vertices": int((csource == 1).sum()),
        "ring_vertices": int((csource == 3).sum()),
        "constraints": {k: int(sum(1 for c in cs.all if c.kind == k)) for k in ("boundary", "hole", "breakline")},
        "nodes": tin.n_nodes,
        "triangles": tin.n_triangles,
        "edges": tin.n_edges,
        "segments": int(len(segments)),
        "constraint_edges": int(len(final_segments)),
        "steiner_points": int(n_out - n_in),
        "raw_triangles": int(raw_triangles),
        "triangles_removed_by_boundary": n_bound,
        "triangles_removed_by_filter": n_filter,
        "rejected": rejected.counts() if rejected is not None else {},
        "median_edge": median_edge,
        "max_edge_used": max_edge,
        "min_angle_used": float(min_angle_deg),
        "duplicates_removed": len(survey_dups),
        "snapped_constraint_vertices": snapped,
        "zero_z_removed": report.removed_zero_z,
        "validation_problems": len(validation),
    }
    return TinResult(tin=tin, issues=issues, dedupe=report, stats=stats, node_source=node_source,
                     rejected=rejected, constraints=cs, validation=validation)
