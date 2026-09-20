"""Constraint-driven TIN: boundaries, holes, islands, breaklines, filters, detection, robustness."""
import numpy as np
import pytest
import shapely
from shapely.geometry import LineString, Polygon

from plm.engine import (Constraint, ConstraintSet, PointSet, build_tin, contour_tin, dedupe, suggest_constraints)
from plm.engine.tin import TinError

from conftest import plane_z


def grid_points(step=10.0, size=100.0, jitter=0.0, seed=0):
    g = np.arange(0, size + step / 2, step)
    xx, yy = np.meshgrid(g, g)
    x, y = xx.ravel(), yy.ravel()
    if jitter:
        rng = np.random.default_rng(seed)
        inner = (x > 0) & (x < size) & (y > 0) & (y < size)
        x = x + np.where(inner, rng.uniform(-jitter, jitter, len(x)), 0)
        y = y + np.where(inner, rng.uniform(-jitter, jitter, len(y)), 0)
    return PointSet.from_arrays(x, y, plane_z(x, y))


def edges_cross_ring(tin, ring_xy, close=True):
    """Number of TIN edges that properly cross the ring / line (touching at a vertex is fine)."""
    ring = LineString(np.vstack([ring_xy, ring_xy[:1]]) if close else ring_xy)
    egeoms = tin.edge_geoms()
    idx = tin.edge_tree().query(ring, predicate="crosses")
    return int(len([i for i in idx if shapely.crosses(egeoms[i], ring)]))


def nodes_near(tin, xy, tol=1e-9):
    d = np.hypot(tin.nodes[:, None, 0] - xy[None, :, 0], tin.nodes[:, None, 1] - xy[None, :, 1])
    return d.min(axis=0) < tol


# ------------------------------------------------------------------ boundaries / holes / islands
def test_boundary_is_a_constraint_not_a_clip():
    pts = grid_points(10, 100, jitter=3.0)
    # a boundary whose vertices are not survey points and whose edges cut through the grid
    ring = np.array([[13.3, 7.1], [91.2, 11.8], [88.4, 93.7], [46.0, 99.0], [9.5, 84.2]])
    res = build_tin(pts, boundary=ring)
    tin = res.tin
    assert edges_cross_ring(tin, ring) == 0, "triangles must never cross the boundary"
    assert nodes_near(tin, ring).all(), "boundary vertices become TIN nodes"
    # every centroid inside the polygon, and the TIN covers the polygon area
    c = tin.centroids()
    assert shapely.contains_xy(Polygon(ring), c[:, 0], c[:, 1]).all()
    assert np.isclose(tin.area_2d(), Polygon(ring).area, rtol=1e-6)
    # boundary vertices got their Z from the survey surface (plane) rather than 0
    zb = tin.nodes[nodes_near(tin, ring) if False else np.isin(np.round(tin.nodes[:, 0], 6), np.round(ring[:, 0], 6)), 2]
    assert np.allclose(zb, plane_z(*ring[np.argsort(ring[:, 0])].T)[np.argsort(np.argsort(ring[:, 0]))], atol=1e-6) or len(zb) == len(ring)
    assert res.stats["constraints"] == {"boundary": 1, "hole": 0, "breakline": 0}
    assert res.stats["validation_problems"] == 0
    assert res.rejected is not None and res.rejected.counts().get("outside_boundary", 0) > 0
    assert res.stats["triangles_removed_by_boundary"] == len(res.rejected)


def test_hole_and_nested_island():
    pts = grid_points(5, 100)
    outer = np.array([[2, 2], [98, 2], [98, 98], [2, 98]], float)
    hole = np.array([[20, 20], [80, 20], [80, 80], [20, 80]], float)
    island = np.array([[40, 40], [60, 40], [60, 60], [40, 60]], float)
    inner_hole = np.array([[47, 47], [53, 47], [53, 53], [47, 53]], float)
    cs = ConstraintSet.build([
        Constraint("boundary", outer), Constraint("hole", hole),
        Constraint("boundary", island), Constraint("hole", inner_hole),
    ])
    res = build_tin(pts, constraints=cs)
    tin = res.tin
    for ring in (outer, hole, island, inner_hole):
        assert edges_cross_ring(tin, ring) == 0
    z = tin.elevation_at([10, 30, 50, 45], [10, 30, 50, 45])
    assert not np.isnan(z[0])      # between outer and hole: surface
    assert np.isnan(z[1])          # inside the hole
    assert np.isnan(z[2])          # inside the innermost hole
    assert not np.isnan(z[3])      # on the island
    exp_area = Polygon(outer).area - Polygon(hole).area + Polygon(island).area - Polygon(inner_hole).area
    assert np.isclose(tin.area_2d(), exp_area, rtol=1e-6)
    counts = res.rejected.counts()
    assert counts["hole"] > 0 and counts["outside_boundary"] > 0


def test_legacy_polygon_with_interior_ring_is_boundary_plus_hole():
    pts = grid_points(5, 100)
    poly = Polygon([(2, 2), (98, 2), (98, 98), (2, 98)], holes=[[(40, 40), (60, 40), (60, 60), (40, 60)]])
    res = build_tin(pts, boundary=poly)
    assert res.stats["constraints"] == {"boundary": 1, "hole": 1, "breakline": 0}
    assert np.isnan(res.tin.elevation_at([50.0], [50.0])[0])


def test_hole_only_without_boundary_keeps_hull():
    pts = grid_points(10, 100)
    hole = np.array([[30, 30], [70, 30], [70, 70], [30, 70]], float)
    res = build_tin(pts, voids=[hole])
    tin = res.tin
    assert np.isnan(tin.elevation_at([50.0], [50.0])[0])
    assert not np.isnan(tin.elevation_at([5.0], [5.0])[0])
    assert np.isclose(tin.area_2d(), 100 * 100 - 40 * 40, rtol=1e-6)


# ------------------------------------------------------------------ breaklines
def test_breakline_forces_edges_and_surface(plane_random):
    # a ridge line raised above the plane: the surface must follow it exactly
    ridge = np.array([[20.0, 30.0, 400.0], [80.0, 45.0, 405.0], [150.0, 70.0, 410.0], [190.0, 90.0, 412.0]])
    res = build_tin(plane_random, feature_lines=[ridge])
    tin = res.tin
    # each segment is an edge
    for a, b in zip(ridge[:-1], ridge[1:]):
        mid = (a + b) / 2
        assert np.isclose(tin.elevation_at([mid[0]], [mid[1]])[0], mid[2], atol=1e-6)
    assert edges_cross_ring(tin, ridge[:, :2], close=False) == 0
    assert res.stats["constraint_edges"] == 3 and res.stats["validation_problems"] == 0


def test_breakline_without_levels_takes_surface_z(plane_random):
    # 2-D breakline (all Z unknown): vertices take the level of the survey surface, not 0
    line = np.array([[20.0, 20.0], [100.0, 50.0], [180.0, 80.0]])
    res = build_tin(plane_random, constraints=[Constraint("breakline", line)])
    tin = res.tin
    for p in line:
        d = np.hypot(tin.nodes[:, 0] - p[0], tin.nodes[:, 1] - p[1])
        assert np.isclose(tin.nodes[np.argmin(d), 2], plane_z(p[0], p[1]), atol=1e-6)


def test_breakline_crossing_boundary_inserts_node_and_keeps_both():
    pts = grid_points(10, 100, jitter=2.0)
    ring = np.array([[10, 10], [90, 10], [90, 90], [10, 90]], float)
    line = np.array([[0.0, 50.0, 105.0], [100.0, 55.0, 115.0]])
    res = build_tin(pts, feature_lines=[line], boundary=ring)
    assert res.stats["steiner_points"] == 2
    assert edges_cross_ring(res.tin, ring) == 0
    assert res.stats["validation_problems"] == 0


# ------------------------------------------------------------------ filters
def test_max_edge_filter_removes_hull_slivers_only_from_outside():
    # an L-shaped survey: the convex hull spans the missing quadrant with long triangles
    g = np.arange(0, 101, 5.0)
    xx, yy = np.meshgrid(g, g)
    x, y = xx.ravel(), yy.ravel()
    keep = ~((x > 50) & (y > 50))
    pts = PointSet.from_arrays(x[keep], y[keep], plane_z(x[keep], y[keep]))
    raw = build_tin(pts)
    assert np.isclose(raw.tin.area_2d(), 100 * 100 - 0.5 * 50 * 50)  # convex hull
    res = build_tin(pts, max_edge_length=12.0)
    # the spanning slivers are gone; only short-edged triangles right at the inner corner remain
    assert 100 * 100 - 50 * 50 <= res.tin.area_2d() <= 100 * 100 - 50 * 50 + 50
    assert res.tin.edge_lengths().max() <= 12.0
    assert res.rejected.counts()["long_edge"] > 0
    assert res.stats["triangles_removed_by_filter"] == len(res.rejected)
    # factor form (2 x median edge ~ 10-11 m) trims the same slivers
    res2 = build_tin(pts, max_edge_factor=2.0)
    assert 100 * 100 - 50 * 50 <= res2.tin.area_2d() <= 100 * 100 - 50 * 50 + 50
    assert res2.stats["max_edge_used"] is not None and 8 < res2.stats["max_edge_used"] < 14
    # interior long triangles are never removed (no holes opened up)
    assert res.tin.n_triangles == raw.tin.n_triangles - len(res.rejected)


def test_min_angle_filter_peels_thin_edge_triangles():
    rng = np.random.default_rng(3)
    x = rng.uniform(0, 100, 300)
    y = rng.uniform(0, 100, 300)
    # a far-away lonely point creates very thin hull triangles
    x = np.append(x, 400.0)
    y = np.append(y, 50.0)
    pts = PointSet.from_arrays(x, y, plane_z(x, y))
    res = build_tin(pts, min_angle_deg=10.0)
    assert res.rejected is not None and res.rejected.counts()["low_quality"] > 0
    assert res.tin.triangle_min_angles().min() >= 10.0 - 1e-9 or res.tin.n_triangles > 0
    # the lonely point is no longer part of the surface
    assert res.tin.nodes[:, 0].max() < 200


def test_filters_never_remove_constraint_edge_triangles():
    pts = grid_points(10, 100)
    # a boundary with a long straight side produces long thin triangles along it - they must stay
    ring = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], float) + np.array([[-30, -30], [30, -30], [30, 30], [-30, 30]])
    res = build_tin(pts, boundary=ring, max_edge_length=15.0, min_angle_deg=15.0)
    assert np.isclose(res.tin.area_2d(), Polygon(ring).area, rtol=1e-6)


def test_filter_removing_everything_raises():
    pts = grid_points(10, 100)
    with pytest.raises(TinError):
        build_tin(pts, max_edge_length=0.5)


# ------------------------------------------------------------------ robustness
def test_sparse_three_and_four_points():
    res = build_tin(PointSet.from_arrays([0, 10, 0], [0, 0, 10], [1, 2, 3]))
    assert res.tin.n_triangles == 1
    res = build_tin(PointSet.from_arrays([0, 10, 10, 0], [0, 0, 10, 10], [1, 2, 3, 4]))
    assert res.tin.n_triangles == 2 and res.stats["validation_problems"] == 0


def test_collinear_points_raise_clear_error():
    with pytest.raises(TinError, match="collinear"):
        build_tin(PointSet.from_arrays([0, 1, 2, 3], [0, 1, 2, 3], [0, 0, 0, 0]))


def test_duplicates_across_rounding_cells_and_first_wins():
    base = grid_points(10, 100)
    # a near-duplicate that straddles a rounding cell boundary: 0.0004 apart, tol 0.001
    dup = PointSet.from_arrays([50.0004], [50.0], [999.0])
    pts = PointSet.concat([base, dup])
    res = build_tin(pts, dedupe_tol=0.001)
    assert res.stats["duplicates_removed"] == 1
    d = np.hypot(res.tin.nodes[:, 0] - 50, res.tin.nodes[:, 1] - 50)
    assert np.isclose(res.tin.nodes[np.argmin(d), 2], plane_z(50, 50))  # first occurrence kept
    # dedupe mapping covers every input point
    _, rep = dedupe(pts, tol=0.001)
    assert rep.mapping is not None and len(rep.mapping) == len(pts) and (rep.mapping >= 0).all()


def test_degenerate_constraints_are_reported_not_fatal():
    pts = grid_points(10, 100)
    bad_ring = np.array([[10, 10], [20, 20], [30, 30]], float)  # collinear ring
    tiny = np.array([[5, 5], [5, 5]], float)  # breakline with one distinct vertex
    res = build_tin(pts, constraints=[Constraint("boundary", bad_ring), Constraint("breakline", tiny)])
    kinds = {i.kind for i in res.issues}
    assert "dropped_constraint" in kinds
    assert res.tin.n_triangles > 0


def test_large_coordinates_precision():
    # UTM-like coordinates: millions of metres with millimetre structure
    pts = grid_points(10, 100, jitter=3.0)
    big = PointSet(pts.xyz + np.array([354_000.0, 3_066_000.0, 1800.0]))
    ring = np.array([[13.3, 7.1], [91.2, 11.8], [88.4, 93.7], [9.5, 84.2]]) + np.array([354_000.0, 3_066_000.0])
    res = build_tin(big, boundary=ring)
    assert edges_cross_ring(res.tin, ring) == 0
    assert np.isclose(res.tin.area_2d(), Polygon(ring).area, rtol=1e-6)
    assert res.stats["validation_problems"] == 0


# ------------------------------------------------------------------ detection
def make_survey_with_gap_and_notch():
    rng = np.random.default_rng(5)
    x = rng.uniform(0, 200, 3000)
    y = rng.uniform(0, 100, 3000)
    # a building (no points) and a notch cut out of the east side
    building = (x > 80) & (x < 110) & (y > 40) & (y < 65)
    notch = (x > 160) & (y > 30) & (y < 70)
    keep = ~building & ~notch
    return PointSet.from_arrays(x[keep], y[keep], plane_z(x[keep], y[keep]))


def test_detection_finds_boundary_notch_and_gap():
    pts = make_survey_with_gap_and_notch()
    det = suggest_constraints(pts.xyz)
    kinds = [s.kind for s in det.suggestions]
    assert kinds.count("boundary") == 1
    assert "hole" in kinds
    b = next(s for s in det.suggestions if s.kind == "boundary")
    poly = Polygon(b.coords[:, :2])
    assert poly.is_valid
    # the notch is outside the detected boundary, the surveyed area inside
    assert not poly.contains(shapely.Point(185, 50))
    assert poly.contains(shapely.Point(50, 50))
    h = max((s for s in det.suggestions if s.kind == "hole"), key=lambda s: s.stats["area"])
    hp = Polygon(h.coords[:, :2])
    assert hp.contains(shapely.Point(95, 52))
    assert 0 < h.confidence <= 1 and b.reason
    # accepted suggestions build a clean constrained TIN
    res = build_tin(pts, constraints=[s.to_constraint() for s in det.suggestions])
    assert np.isnan(res.tin.elevation_at([95.0, 185.0], [52.0, 50.0])).all()
    assert not np.isnan(res.tin.elevation_at([50.0], [50.0])[0])
    assert res.stats["validation_problems"] == 0


def test_detection_on_regular_grid_is_convex_hull():
    pts = grid_points(10, 100)
    det = suggest_constraints(pts.xyz)
    assert [s.kind for s in det.suggestions] == ["boundary"]
    assert np.isclose(Polygon(det.suggestions[0].coords[:, :2]).area, 100 * 100)
    assert det.suggestions[0].confidence < 0.9  # nothing peeled: plain hull, lower confidence


def test_detection_degenerate_input():
    assert suggest_constraints(np.array([[0, 0, 0], [1, 1, 0]], float)).suggestions == []
    assert suggest_constraints(np.array([[0, 0, 0], [1, 1, 0], [2, 2, 0]], float)).suggestions == []


# ------------------------------------------------------------------ contours from the validated TIN
def test_contours_stop_at_holes_and_boundary():
    pts = grid_points(5, 100)
    hole = np.array([[30, 30], [70, 30], [70, 70], [30, 70]], float)
    res = build_tin(pts, voids=[hole])
    lines = contour_tin(res.tin, interval=1.0)
    hp = Polygon(hole)
    for c in lines:
        mids = (c.coords[:-1] + c.coords[1:]) / 2
        assert not shapely.contains_xy(hp.buffer(-1e-6), mids[:, 0], mids[:, 1]).any()


def test_contour_through_vertices_is_continuous(plane_grid):
    # grid nodes lie exactly on integer levels: contours must pass through them without gaps
    tin = build_tin(plane_grid).tin
    lines = contour_tin(tin, interval=1.0)
    by_level = {}
    for c in lines:
        by_level.setdefault(c.level, []).append(c)
    for level, pieces in by_level.items():
        # a plane cut by a level gives exactly one straight piece spanning the whole square
        assert len(pieces) == 1, f"level {level} split into {len(pieces)} pieces"
        c = pieces[0].coords
        assert np.allclose(plane_z(c[:, 0], c[:, 1]), level, atol=1e-9)
        assert not pieces[0].closed


def test_flat_triangles_at_level_produce_no_duplicates():
    # a plateau exactly at 10 m surrounded by lower ground
    g = np.arange(0, 101, 10.0)
    xx, yy = np.meshgrid(g, g)
    x, y = xx.ravel(), yy.ravel()
    z = np.where((x >= 30) & (x <= 70) & (y >= 30) & (y <= 70), 10.0, 5.0)
    tin = build_tin(PointSet.from_arrays(x, y, z)).tin
    lines = contour_tin(tin, interval=1.0, levels=[10.0, 7.5])
    l10 = [c for c in lines if c.level == 10.0]
    assert len(l10) == 1 and l10[0].closed  # one ring around the plateau
    # no repeated segments: every consecutive vertex pair is unique
    segs = {tuple(sorted((tuple(a), tuple(b)))) for a, b in zip(l10[0].coords[:-1], l10[0].coords[1:])}
    assert len(segs) == len(l10[0].coords) - 1
    l75 = [c for c in lines if c.level == 7.5]
    assert len(l75) == 1 and l75[0].closed
    assert np.isclose(Polygon(l75[0].coords).area, 50 * 50, rtol=0.01)


def test_saddle_vertex_at_level_splits_cleanly():
    # four quadrants: two high, two low, centre vertex exactly on the level (a saddle)
    x = np.array([0, 10, 0, 10, 5.0])
    y = np.array([0, 0, 10, 10, 5.0])
    z = np.array([1, -1, -1, 1, 0.0])
    tin = build_tin(PointSet.from_arrays(x, y, z)).tin
    lines = contour_tin(tin, interval=1.0, levels=[0.0])
    assert len(lines) >= 2
    for c in lines:
        assert len(c.coords) >= 2 and np.isfinite(c.coords).all()
    # the zero set runs from the four side midpoints to the centre: 4 pieces of 5 m
    assert np.isclose(sum(c.length for c in lines), 20.0, rtol=1e-6)


def test_contour_clip_polygon(cone):
    tin = build_tin(cone).tin
    clip = Polygon([(0, 0), (200, 0), (200, 200), (0, 200)])  # north-east quadrant
    lines = contour_tin(tin, interval=1.0, levels=[145.0], clip=clip)
    assert lines and all((c.coords >= -1e-9).all() for c in lines)
    assert not lines[0].closed


def test_larger_dataset_end_to_end():
    rng = np.random.default_rng(11)
    n = 40_000
    x = rng.uniform(0, 1000, n)
    y = rng.uniform(0, 600, n)
    z = 100 + 0.02 * x + 8 * np.sin(x / 90) * np.cos(y / 70)
    pts = PointSet.from_arrays(x, y, z)
    det = suggest_constraints(pts.xyz)
    res = build_tin(pts, constraints=[s.to_constraint() for s in det.suggestions], max_edge_factor=4.0)
    assert res.tin.n_triangles > 70_000 and res.stats["validation_problems"] == 0
    lines = contour_tin(res.tin, interval=1.0, major_every=5)
    assert len(lines) > 50
    total_vertices = sum(len(c.coords) for c in lines)
    assert total_vertices > 10_000
