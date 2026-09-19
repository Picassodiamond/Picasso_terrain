import numpy as np
import pytest
from shapely.geometry import Polygon

from plm.engine import PointSet, build_tin, clip_to_boundary
from plm.engine.tin import TIN, TinError

from conftest import plane_z


def test_topology_consistency(plane_grid):
    res = build_tin(plane_grid)
    tin = res.tin
    n, m, e = tin.n_nodes, tin.n_triangles, tin.n_edges
    # Euler for a disc: V - E + F = 1
    assert n - e + m == 1
    # every triangle edge index maps back to its node pair
    for k, (i, j) in enumerate([(0, 1), (1, 2), (2, 0)]):
        pair = np.sort(tin.triangles[:, [i, j]], axis=1)
        assert np.array_equal(tin.edges[tin.tri_edges[:, k]], pair)
    # each interior edge is shared by exactly two triangles, hull edges by one
    interior = tin.edge_tris[:, 1] >= 0
    assert interior.sum() + len(tin.boundary_edges) == e
    assert res.stats["steiner_points"] == 0
    assert np.isclose(tin.area_2d(), 100 * 100)


def test_elevation_on_plane_is_exact(plane_random):
    tin = build_tin(plane_random).tin
    rng = np.random.default_rng(1)
    x = rng.uniform(1, 199, 200)
    y = rng.uniform(1, 99, 200)
    z = tin.elevation_at(x, y)
    assert np.allclose(z, plane_z(x, y), atol=1e-6)
    # outside -> NaN
    assert np.isnan(tin.elevation_at([250.0], [50.0])[0])
    assert tin.locate([250.0], [50.0])[0] == -1


def test_sample_line_on_plane(plane_random):
    tin = build_tin(plane_random).tin
    s = tin.sample_line(np.array([[5.0, 5.0], [195.0, 95.0]]))
    assert s.distance[0] == 0.0
    assert np.isclose(s.distance[-1], np.hypot(190, 90))
    assert np.all(np.diff(s.distance) > 0)
    # every sample lies on the plane (edge crossings interpolate exactly on a plane)
    assert np.allclose(s.z, plane_z(s.xy[:, 0], s.xy[:, 1]), atol=1e-6)
    # there must be many more samples than the two vertices (one per crossed edge)
    assert len(s) > 20


def test_feature_line_becomes_edges(plane_random):
    # a diagonal break line with a raised elevation must appear as TIN edges
    fl = np.array([[20.0, 20.0, 500.0], [100.0, 60.0, 500.0], [180.0, 80.0, 500.0]])
    res = build_tin(plane_random, feature_lines=[fl])
    tin = res.tin
    idx = []
    for p in fl:
        d = np.hypot(tin.nodes[:, 0] - p[0], tin.nodes[:, 1] - p[1])
        idx.append(int(np.argmin(d)))
        assert d.min() < 1e-9
        assert tin.nodes[idx[-1], 2] == 500.0
    assert tin.has_edge(idx[0], idx[1]) and tin.has_edge(idx[1], idx[2])
    assert res.stats["segments"] == 2
    assert res.stats["steiner_points"] == 0
    # along the break line the surface is flat at 500
    z = tin.elevation_at([60.0], [40.0])
    assert np.isclose(z[0], 500.0)


def test_crossing_features_are_reported(plane_random):
    a = np.array([[20.0, 50.0, 300.0], [180.0, 50.0, 300.0]])
    b = np.array([[100.0, 10.0, 400.0], [100.0, 90.0, 400.0]])
    res = build_tin(plane_random, feature_lines=[a, b])
    crossing = [i for i in res.issues if i.kind == "crossing_features"]
    assert len(crossing) == 1
    assert np.isclose(crossing[0].x, 100.0) and np.isclose(crossing[0].y, 50.0)
    assert res.stats["steiner_points"] == 1
    # Steiner node elevation = mean of the two segments (300 and 400)
    d = np.hypot(res.tin.nodes[:, 0] - 100, res.tin.nodes[:, 1] - 50)
    assert np.isclose(res.tin.nodes[np.argmin(d), 2], 350.0)


def test_zero_z_feature_vertices_are_interpolated(plane_random):
    fl = np.array([[20.0, 20.0, 200.0], [60.0, 20.0, 0.0], [100.0, 20.0, 220.0]])
    res = build_tin(plane_random, feature_lines=[fl])
    d = np.hypot(res.tin.nodes[:, 0] - 60, res.tin.nodes[:, 1] - 20)
    assert np.isclose(res.tin.nodes[np.argmin(d), 2], 210.0)


def test_boundary_inside_and_legacy(plane_grid):
    poly = Polygon([(25, 25), (75, 25), (75, 75), (25, 75)])
    full = build_tin(plane_grid).tin
    inside = clip_to_boundary(full, poly, mode="inside")
    assert 0 < inside.n_triangles < full.n_triangles
    c = inside.centroids()
    assert np.all((c[:, 0] > 25) & (c[:, 0] < 75) & (c[:, 1] > 25) & (c[:, 1] < 75))
    # unused nodes dropped
    assert inside.n_nodes < full.n_nodes
    legacy = clip_to_boundary(full, poly, mode="legacy_cross")
    # legacy only removes triangles crossing the line; the outer triangles remain
    assert legacy.n_triangles < full.n_triangles
    assert legacy.n_triangles > inside.n_triangles
    # voids
    void = Polygon([(40, 40), (60, 40), (60, 60), (40, 60)])
    holed = clip_to_boundary(full, None, voids=[void], mode="inside")
    assert np.isnan(holed.elevation_at([50.0], [50.0])[0])
    assert not np.isnan(holed.elevation_at([10.0], [10.0])[0])
    # build_tin with boundary array
    res = build_tin(plane_grid, boundary=np.array(poly.exterior.coords))
    assert res.stats["triangles_removed_by_boundary"] > 0
    assert res.node_source is not None and len(res.node_source) == res.tin.n_nodes


def test_too_few_points():
    with pytest.raises(TinError):
        build_tin(PointSet.from_arrays([0, 1], [0, 1], [0, 0]))


def test_duplicates_removed_in_build(plane_grid):
    dup = PointSet.concat([plane_grid, plane_grid.subset(np.arange(10))])
    res = build_tin(dup)
    assert res.stats["duplicates_removed"] == 10
    assert res.tin.n_nodes == len(plane_grid)


def test_tin_orientation_ccw():
    nodes = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0.0]])
    tin = TIN(nodes, np.array([[0, 2, 1]]))  # clockwise input
    assert tin._signed_area2()[0] > 0
