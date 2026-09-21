import os

import numpy as np
import pytest

from plm.engine import PointSet


@pytest.fixture(autouse=True, scope="session")
def _no_ambient_settings():
    """Run the suite against the defaults, never against a configured machine.

    A deployed server exports its whole configuration (PLM_AUTH_ENABLED, PLM_JOB_MODE,
    PLM_VISITOR_INTAKE_REQUIRED, ...) into the interpreter's environment, and `Settings` reads any
    field a test does not pass explicitly from there. Without this the same suite passes on a
    laptop and fails on the server for reasons that have nothing to do with the code.
    """
    saved = {k: v for k, v in os.environ.items() if k.startswith("PLM_")}
    for k in saved:
        del os.environ[k]
    yield
    os.environ.update(saved)


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
