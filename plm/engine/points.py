"""Survey point container and point-level operations.

Replaces the legacy `Points` / `Pointnxy` text files and the duplicate removal in
`frmtriangulate.cmdProcess_Click`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np


@dataclass
class PointSet:
    """A set of survey points.

    xyz      (n, 3) float64 array of easting, northing, elevation
    ids      point numbers / labels ('' when unknown)
    remarks  free-text remark / description per point
    layers   optional layer / feature-class name per point
    """

    xyz: np.ndarray
    ids: list[str] = field(default_factory=list)
    remarks: list[str] = field(default_factory=list)
    layers: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.xyz = np.asarray(self.xyz, dtype=np.float64).reshape(-1, 3)
        n = len(self.xyz)
        if not self.ids:
            self.ids = [""] * n
        if not self.remarks:
            self.remarks = [""] * n
        if not self.layers:
            self.layers = [""] * n
        if not (len(self.ids) == len(self.remarks) == len(self.layers) == n):
            raise ValueError("ids, remarks and layers must have one entry per point")

    # -- construction helpers -------------------------------------------------
    @classmethod
    def from_arrays(
        cls,
        x: Sequence[float],
        y: Sequence[float],
        z: Sequence[float] | None = None,
        ids: Sequence[str] | None = None,
        remarks: Sequence[str] | None = None,
        layers: Sequence[str] | None = None,
    ) -> "PointSet":
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        z = np.zeros_like(x) if z is None else np.asarray(z, dtype=np.float64)
        xyz = np.column_stack([x, y, z])
        return cls(
            xyz,
            list(map(str, ids)) if ids is not None else [],
            list(map(str, remarks)) if remarks is not None else [],
            list(map(str, layers)) if layers is not None else [],
        )

    @classmethod
    def empty(cls) -> "PointSet":
        return cls(np.zeros((0, 3)))

    @classmethod
    def concat(cls, sets: Iterable["PointSet"]) -> "PointSet":
        sets = [s for s in sets if len(s)]
        if not sets:
            return cls.empty()
        return cls(
            np.vstack([s.xyz for s in sets]),
            sum((s.ids for s in sets), []),
            sum((s.remarks for s in sets), []),
            sum((s.layers for s in sets), []),
        )

    # -- basic API -------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.xyz)

    @property
    def x(self) -> np.ndarray:
        return self.xyz[:, 0]

    @property
    def y(self) -> np.ndarray:
        return self.xyz[:, 1]

    @property
    def z(self) -> np.ndarray:
        return self.xyz[:, 2]

    def subset(self, mask: np.ndarray) -> "PointSet":
        idx = np.flatnonzero(np.asarray(mask)) if np.asarray(mask).dtype == bool else np.asarray(mask)
        return PointSet(
            self.xyz[idx],
            [self.ids[i] for i in idx],
            [self.remarks[i] for i in idx],
            [self.layers[i] for i in idx],
        )

    def bounds(self) -> tuple[float, float, float, float]:
        if not len(self):
            return (0.0, 0.0, 0.0, 0.0)
        return (
            float(self.x.min()),
            float(self.y.min()),
            float(self.x.max()),
            float(self.y.max()),
        )


@dataclass
class DedupeReport:
    input_count: int
    kept: int
    removed_duplicates: int
    removed_zero_z: int
    # (kept original index, removed original index)
    duplicate_pairs: list[tuple[int, int]] = field(default_factory=list)
    # per input point: index into the de-duplicated set (-1 when the point was dropped)
    mapping: np.ndarray | None = None


def dedupe(
    points: PointSet, tol: float = 0.001, drop_zero_z: bool = False
) -> tuple[PointSet, DedupeReport]:
    """Remove horizontally coincident points.

    Two points are duplicates when their XY distance is <= `tol` (exact neighbour search with a
    k-d tree, so pairs straddling a rounding cell are found too). Groups of mutually close points
    are collapsed onto the *first* occurrence (survey points precede feature vertices, so the
    surveyed elevation wins). `drop_zero_z` reproduces the legacy rule of discarding points with
    elevation exactly 0. `report.mapping` maps every input index to its kept index.
    """
    n = len(points)
    if n == 0:
        return points, DedupeReport(0, 0, 0, 0, mapping=np.zeros(0, dtype=np.int64))
    keep = np.ones(n, dtype=bool)
    removed_zero = 0
    if drop_zero_z:
        zero = points.z == 0.0
        removed_zero = int(zero.sum())
        keep &= ~zero

    idx_all = np.flatnonzero(keep)
    rep = idx_all.copy()  # representative (original index) of every candidate point
    if len(idx_all) > 1 and tol > 0:
        from scipy.spatial import cKDTree

        xy = points.xyz[idx_all, :2]
        pairs = cKDTree(xy).query_pairs(tol, output_type="ndarray")
        if len(pairs):
            # union-find on local indices; the smallest index of a group is its representative
            parent = np.arange(len(idx_all))

            def find(a: int) -> int:
                while parent[a] != a:
                    parent[a] = parent[parent[a]]
                    a = parent[a]
                return a

            for a, b in pairs:
                ra, rb = find(int(a)), find(int(b))
                if ra != rb:
                    if ra < rb:
                        parent[rb] = ra
                    else:
                        parent[ra] = rb
            roots = np.fromiter((find(int(i)) for i in range(len(idx_all))), dtype=np.int64, count=len(idx_all))
            rep = idx_all[roots]
    is_dup = rep != idx_all
    pairs_out = [(int(a), int(b)) for a, b in zip(rep[is_dup], idx_all[is_dup])]
    keep[idx_all[is_dup]] = False

    kept_orig = np.flatnonzero(keep)
    pos = -np.ones(n, dtype=np.int64)
    pos[kept_orig] = np.arange(len(kept_orig))
    mapping = -np.ones(n, dtype=np.int64)
    mapping[idx_all] = pos[rep]

    out = points.subset(keep)
    report = DedupeReport(
        input_count=n,
        kept=len(out),
        removed_duplicates=int(is_dup.sum()),
        removed_zero_z=removed_zero,
        duplicate_pairs=pairs_out,
        mapping=mapping,
    )
    return out, report


def fill_zero_z_along_line(coords: np.ndarray, missing: float = 0.0) -> np.ndarray:
    """Interpolate missing elevations along a polyline by cumulative 2-D distance.

    Legacy rule (`Pro_Tin.Interpolate`, `frmGetPoints.get_RL_forInbetweenPoints`): vertices whose
    Z equals `missing` and that lie between two vertices with known Z get a linearly
    interpolated Z. Leading/trailing unknown vertices are left unchanged (the caller decides
    whether to drop them). Returns a copy.
    """
    c = np.array(coords, dtype=np.float64)
    if c.ndim != 2 or c.shape[1] < 3 or len(c) < 3:
        return c
    known = c[:, 2] != missing
    if known.all() or known.sum() < 2:
        return c
    seg = np.hypot(np.diff(c[:, 0]), np.diff(c[:, 1]))
    dist = np.concatenate([[0.0], np.cumsum(seg)])
    k = np.flatnonzero(known)
    lo, hi = k[0], k[-1]
    inner = np.arange(lo, hi + 1)
    unknown_inner = inner[~known[inner]]
    if len(unknown_inner):
        c[unknown_inner, 2] = np.interp(dist[unknown_inner], dist[k], c[k, 2])
    return c
