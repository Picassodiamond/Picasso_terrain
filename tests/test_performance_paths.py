"""Large-data code paths: grid point location, light hull, numpy point reads, TIN cache, binary
points, mesh tiles."""
import io
import json
import struct

import numpy as np
import pytest
import shapely
from fastapi.testclient import TestClient
from shapely.geometry import Polygon

from plm.api import services
from plm.api.config import Settings
from plm.api.gpkg import ProjectStore
from plm.api.main import create_app
from plm.engine import PointSet, build_tin

from conftest import plane_z


# ------------------------------------------------------------------ engine: grid locate
def _shapely_locate(tin, x, y):
    pts = shapely.points(x, y)
    inp, tri = tin.triangle_tree().query(pts, predicate="intersects")
    out = -np.ones(len(x), dtype=np.int64)
    if len(inp):
        _, first = np.unique(inp, return_index=True)
        out[inp[first]] = tri[first]
    return out


def test_grid_locate_matches_geometry_predicate():
    rng = np.random.default_rng(2)
    x = rng.uniform(0, 300, 3000)
    y = rng.uniform(0, 200, 3000)
    tin = build_tin(PointSet.from_arrays(x, y, plane_z(x, y))).tin
    qx = rng.uniform(-20, 320, 5000)
    qy = rng.uniform(-20, 220, 5000)
    got = tin.locate(qx, qy)
    ref = _shapely_locate(tin, qx, qy)
    inside_agree = (got >= 0) == (ref >= 0)
    assert inside_agree.all()
    # where both find a triangle it must contain the point (same triangle unless the point is on an edge)
    same = got == ref
    if not same.all():
        for i in np.flatnonzero(~same):
            assert shapely.intersects(tin.triangle_geoms()[got[i]], shapely.Point(qx[i], qy[i]))


def test_grid_locate_vertices_edges_and_outside(plane_grid):
    tin = build_tin(plane_grid).tin
    # exactly on nodes
    t = tin.locate(tin.nodes[:, 0], tin.nodes[:, 1])
    assert (t >= 0).all()
    z = tin.elevation_at(tin.nodes[:, 0], tin.nodes[:, 1])
    assert np.allclose(z, tin.nodes[:, 2], atol=1e-9)
    # on edge midpoints
    mid = tin.nodes[tin.edges].mean(axis=1)
    assert (tin.locate(mid[:, 0], mid[:, 1]) >= 0).all()
    assert np.allclose(tin.elevation_at(mid[:, 0], mid[:, 1]), plane_z(mid[:, 0], mid[:, 1]), atol=1e-9)
    # just outside the hull
    assert tin.locate([-0.01, 50.0], [50.0, 100.01]).tolist() == [-1, -1]
    assert tin.locate([], []).shape == (0,)


def test_hull_from_boundary_edges_with_hole():
    g = np.arange(0, 101, 5.0)
    xx, yy = np.meshgrid(g, g)
    x, y = xx.ravel(), yy.ravel()
    hole = np.array([[30, 30], [70, 30], [70, 70], [30, 70]], float)
    tin = build_tin(PointSet.from_arrays(x, y, plane_z(x, y)), voids=[hole]).tin
    h = tin.hull()
    assert h.geom_type == "Polygon" and len(h.interiors) == 1
    assert np.isclose(h.area, 100 * 100 - 40 * 40, rtol=1e-6)
    assert np.isclose(Polygon(h.interiors[0]).area, 40 * 40, rtol=1e-6)


def test_hull_convex_case(plane_random):
    tin = build_tin(plane_random).tin
    h = tin.hull()
    assert h.geom_type == "Polygon" and np.isclose(h.area, 200 * 100, rtol=1e-6)


# ------------------------------------------------------------------ store
def test_points_xyz_matches_points(tmp_path):
    store = ProjectStore.create(tmp_path / "p.gpkg", "local", "t")
    ps = PointSet.from_arrays([1, 2, 3], [4, 5, 6], [7, 8, 9], ids=["a", "b", "c"], remarks=["", "GL", ""], layers=["L1", "L1", "L2"])
    store.add_points(ps, source="test")
    fids, xyz = store.points_xyz()
    full, fids2 = store.points(with_fid=True)
    assert np.array_equal(fids, fids2) and np.allclose(xyz, full.xyz)
    assert full.ids == ["a", "b", "c"] and full.remarks[1] == "GL"
    _, xyz_l2 = store.points_xyz(layers=["L2"])
    assert xyz_l2.tolist() == [[3.0, 6.0, 9.0]]
    d = store.point(int(fids[1]))
    assert d and d["id"] == "b" and d["remark"] == "GL"
    near = store.nearest_point(2.2, 5.1, radius=1.0)
    assert near and near["fid"] == int(fids[1]) and near["distance"] < 0.3
    assert store.nearest_point(50, 50, radius=1.0) is None


# ------------------------------------------------------------------ API
def _client(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", secret_key="s", auth_enabled=False, sync_point_limit=250_000)
    return TestClient(create_app(settings))


def _project_with_points(client, n=400, seed=3):
    r = client.post("/api/projects", json={"name": "perf", "crs": "local"})
    pid = r.json()["id"]
    rng = np.random.default_rng(seed)
    x = rng.uniform(0, 200, n)
    y = rng.uniform(0, 100, n)
    z = plane_z(x, y)
    rows = ["PtNo,Easting,Northing,Elevation,Remark"] + [f"P{i+1},{x[i]:.3f},{y[i]:.3f},{z[i]:.3f},GL" for i in range(n)]
    client.post(f"/api/projects/{pid}/import", files={"file": ("pts.csv", io.BytesIO("\n".join(rows).encode()))})
    return pid


def test_points_binary_and_detail(tmp_path):
    with _client(tmp_path) as client:
        pid = _project_with_points(client)
        b = client.get(f"/api/projects/{pid}/points.bin").content
        assert b[:4] == b"PLMP"
        ver, n, dtype = struct.unpack("<III", b[4:16])
        assert ver == 1 and n == 400 and dtype == 0
        origin = struct.unpack("<3d", b[16:40])
        pos = np.frombuffer(b[40:40 + n * 12], dtype=np.float32).reshape(n, 3)
        fids = np.frombuffer(b[40 + n * 12:], dtype=np.uint32)
        assert len(fids) == n and len(b) == 40 + n * 16
        xyz = pos.astype(np.float64) + np.array(origin)
        gj = client.get(f"/api/projects/{pid}/points.geojson").json()
        ref = np.array([f["geometry"]["coordinates"] for f in gj["features"]])
        assert np.allclose(xyz, ref, atol=0.01)
        d = client.get(f"/api/projects/{pid}/points/{int(fids[5])}").json()
        assert d["id"] == gj["features"][5]["properties"]["id"] and d["remark"] == "GL"
        near = client.get(f"/api/projects/{pid}/points/nearest", params={"x": xyz[7, 0], "y": xyz[7, 1], "radius": 0.5}).json()
        assert near["fid"] == int(fids[7])
        assert client.get(f"/api/projects/{pid}/points/nearest", params={"x": -500, "y": -500}).status_code == 404
        assert client.get(f"/api/projects/{pid}/points/layers").status_code == 200  # not swallowed by /points/{fid}
        assert client.get(f"/api/projects/{pid}/points/999999").status_code == 404


def test_tin_cache_and_tiles(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "TILE_TRIANGLES", 200)  # force several tiles on a small mesh
    with _client(tmp_path) as client:
        pid = _project_with_points(client)
        r = client.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "manual"})
        run_id = r.json()["result"]["id"]
        services.evict_tin(tmp_path / "data" / "projects" / pid / "project.gpkg")
        before = services.tin_cache_info()["size"]
        client.get(f"/api/projects/{pid}/tin/{run_id}/elevation", params={"x": 50, "y": 50})
        client.get(f"/api/projects/{pid}/tin/{run_id}/elevation", params={"x": 60, "y": 50})
        info = services.tin_cache_info()
        assert info["size"] >= 1 and info["size"] <= info["limit"] and info["size"] >= before
        assert any(k.endswith(f":{run_id}") for k in info["keys"])

        idx = client.get(f"/api/projects/{pid}/tin/{run_id}/tiles").json()
        assert idx["n"] >= 2 and idx["tiles"]
        total = 0
        n_tiles = 0
        for t in idx["tiles"]:
            b = client.get(f"/api/projects/{pid}/tin/{run_id}/tiles/{t['i']}/{t['j']}.bin").content
            assert b[:4] == b"PLMM"
            _, n, m, dtype = struct.unpack("<IIII", b[4:20])
            assert m == t["triangles"] and dtype == 0
            pos = np.frombuffer(b[44:44 + n * 12], dtype=np.float32).reshape(n, 3)
            tris = np.frombuffer(b[44 + n * 12:], dtype=np.uint32)
            assert len(tris) == m * 3 and tris.max() < n and pos.min() >= 0
            total += m
            n_tiles += 1
        assert total == idx["n_triangles"] and n_tiles == len(idx["tiles"])
        assert client.get(f"/api/projects/{pid}/tin/{run_id}/tiles/{idx['n']}/0.bin").status_code == 404

        # deleting the run evicts it from the cache
        assert client.delete(f"/api/projects/{pid}/tin/{run_id}").status_code == 204
        assert not any(k.endswith(f":{run_id}") for k in services.tin_cache_info()["keys"])


def test_tin_cache_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "TIN_CACHE_SIZE", 2)
    services._TIN_CACHE.clear()
    with _client(tmp_path) as client:
        pid = _project_with_points(client)
        runs = [client.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "manual"}).json()["result"]["id"] for _ in range(3)]
        for rid in runs:
            client.get(f"/api/projects/{pid}/tin/{rid}/elevation", params={"x": 50, "y": 50})
        info = services.tin_cache_info()
        assert info["size"] == 2
        assert not any(k.endswith(f":{runs[0]}") for k in info["keys"])  # oldest evicted


@pytest.mark.parametrize("payload", [json.dumps({"constraint_mode": "manual", "min_angle_deg": 5})])
def test_mesh_binary_unchanged_format(tmp_path, payload):
    with _client(tmp_path) as client:
        pid = _project_with_points(client)
        run_id = client.post(f"/api/projects/{pid}/tin", content=payload, headers={"Content-Type": "application/json"}).json()["result"]["id"]
        b = client.get(f"/api/projects/{pid}/tin/{run_id}/mesh.bin").content
        assert b[:4] == b"PLMM" and struct.unpack("<IIII", b[4:20])[0] == 1
