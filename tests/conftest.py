import numpy as np
import pytest

from plm.engine import PointSet


def plane_z(x, y, a=0.10, b=0.05, c=100.0):
    return a * x + b * y + c


@pytest.fixture
def plane_grid():
    """Regular 11x11 grid on a plane z = 0.1x + 0.05y + 100 over [0,100]^2."""
    g = np.linspace(0, 100, 11)
    xx, yy = np.meshgrid(g, g)
    x, y = xx.ravel(), yy.ravel()
    return PointSet.from_arrays(x, y, plane_z(x, y), ids=[str(i + 1) for i in range(len(x))])


@pytest.fixture
def plane_random():
    rng = np.random.default_rng(7)
    x = rng.uniform(0, 200, 400)
    y = rng.uniform(0, 100, 400)
    # include the corners so the hull is the full rectangle
    x = np.concatenate([x, [0, 200, 0, 200]])
    y = np.concatenate([y, [0, 0, 100, 100]])
    return PointSet.from_arrays(x, y, plane_z(x, y))


@pytest.fixture
def cone():
    """Cone with apex 150 at (0,0), slope 1:10, points on rings plus centre."""
    pts = [(0.0, 0.0, 150.0)]
    for r in np.arange(5, 105, 5):
        n = int(max(8, 2 * np.pi * r / 4))
        ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
        for a in ang:
            pts.append((r * np.cos(a), r * np.sin(a), 150.0 - r / 10.0))
    p = np.array(pts)
    return PointSet(p)
