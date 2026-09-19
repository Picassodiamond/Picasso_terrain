import numpy as np

from plm.engine import build_tin, contour_labels, contour_tin, levels_for
from plm.engine.contour import chaikin

from conftest import plane_z


def test_levels_for():
    lv = levels_for(100.3, 105.2, 1.0)
    assert np.allclose(lv, [101, 102, 103, 104, 105])
    lv2 = levels_for(100.0, 102.0, 0.5, base=0.25)
    assert np.allclose(lv2, [100.25, 100.75, 101.25, 101.75])


def test_plane_contours_are_straight_and_correct(plane_random):
    tin = build_tin(plane_random).tin
    lines = contour_tin(tin, interval=1.0, major_every=5)
    zmin, zmax = tin.z_range()
    expected = levels_for(zmin, zmax, 1.0)
    got = sorted({c.level for c in lines})
    assert np.allclose(got, expected)
    for c in lines:
        # all vertices on the plane at the contour level
        assert np.allclose(plane_z(c.coords[:, 0], c.coords[:, 1]), c.level, atol=1e-6)
        # straight: collinear vertices
        d = c.coords[-1] - c.coords[0]
        n = np.array([-d[1], d[0]]) / np.hypot(*d)
        off = (c.coords - c.coords[0]) @ n
        assert np.abs(off).max() < 1e-6
        assert not c.closed
        assert c.is_major == (abs(c.level / 5 - round(c.level / 5)) < 1e-9)
    # one piece per level on a convex plane
    levels = [c.level for c in lines]
    assert len(levels) == len(set(levels))


def test_cone_contours_are_closed_rings(cone):
    tin = build_tin(cone).tin
    lines = contour_tin(tin, interval=1.0, major_every=5)
    for c in lines:
        if 141 <= c.level <= 149:  # away from the hull
            assert c.closed
            r = np.hypot(c.coords[:, 0], c.coords[:, 1])
            r_exp = (150.0 - c.level) * 10.0
            assert np.allclose(r, r_exp, rtol=0.02)


def test_contour_level_equal_to_node_elevation(plane_grid):
    # grid nodes have z exactly on integer levels at many places -> epsilon shift must cope
    tin = build_tin(plane_grid).tin
    lines = contour_tin(tin, interval=1.0)
    assert lines
    for c in lines:
        assert len(c.coords) >= 2
        assert np.all(np.isfinite(c.coords))


def test_min_spacing_and_smoothing(cone):
    tin = build_tin(cone).tin
    raw = contour_tin(tin, 1.0, levels=[145.0])
    thin = contour_tin(tin, 1.0, levels=[145.0], min_spacing=5.0)
    assert len(thin[0].coords) < len(raw[0].coords)
    assert thin[0].closed and np.allclose(thin[0].coords[0], thin[0].coords[-1])
    sm = contour_tin(tin, 1.0, levels=[145.0], smoothing="chaikin")
    assert len(sm[0].coords) > len(raw[0].coords)
    assert sm[0].closed and np.allclose(sm[0].coords[0], sm[0].coords[-1])


def test_chaikin_open_keeps_endpoints():
    c = np.array([[0, 0], [10, 0], [10, 10.0]])
    out = chaikin(c, 2, closed=False)
    assert np.allclose(out[0], c[0]) and np.allclose(out[-1], c[-1])


def test_labels(plane_random):
    tin = build_tin(plane_random).tin
    lines = contour_tin(tin, 1.0)
    labels = contour_labels(lines, every_m=50.0, fmt="{z:.1f}", suffix=" m")
    assert labels
    assert all(-90 <= lb.angle_deg <= 90 for lb in labels)
    assert labels[0].text.endswith(" m")
    only_major = contour_labels(lines, every_m=50.0, major_only=True)
    assert all(lb.level % 5 == 0 for lb in only_major)
