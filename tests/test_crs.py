import numpy as np
import pytest

from plm.engine import crs


def test_presets_resolve():
    assert crs.resolve("local") is None
    assert crs.resolve(None) is None
    assert crs.resolve("UTM45N").to_epsg() == 32645
    assert crs.resolve("EPSG:4326").is_geographic
    m = crs.resolve("MUTM84")
    assert m is not None and not m.is_geographic
    d = crs.describe("MUTM84")
    assert d["is_local"] is False
    assert crs.describe("local")["is_local"] is True


def test_mutm84_roundtrip_kathmandu():
    # Kathmandu ~ 85.32E 27.70N -> MUTM84 easting near 500000 + ~131 km
    x, y = crs.transform_xy("EPSG:4326", "MUTM84", [85.32], [27.70])
    assert 620_000 < x[0] < 640_000
    assert 3_060_000 < y[0] < 3_070_000
    lon, lat = crs.to_wgs84("MUTM84", x, y)
    assert np.isclose(lon[0], 85.32, atol=1e-6) and np.isclose(lat[0], 27.70, atol=1e-6)


def test_local_cannot_transform():
    with pytest.raises(ValueError):
        crs.to_wgs84("local", [1.0], [2.0])


def test_helmert_fit():
    rng = np.random.default_rng(0)
    src = rng.uniform(0, 100, (5, 2))
    s, th, tx, ty = 1.002, np.radians(3.0), 500000.0, 3050000.0
    c, sn = np.cos(th) * s, np.sin(th) * s
    dst = np.column_stack([c * src[:, 0] - sn * src[:, 1] + tx, sn * src[:, 0] + c * src[:, 1] + ty])
    h = crs.fit_helmert(src, dst)
    assert np.isclose(h.scale, s) and np.isclose(h.rotation, th) and h.rms < 1e-6
    px, py = h.apply(src[:, 0], src[:, 1])
    assert np.allclose(px, dst[:, 0]) and np.allclose(py, dst[:, 1])
