"""Design workspaces: registry, creation pinned to a TIN run, seeding from an alignment, rebase."""
import io

import numpy as np
from fastapi.testclient import TestClient

from plm.api.config import Settings
from plm.api.main import create_app

from conftest import plane_z


def _client(tmp_path):
    return TestClient(create_app(Settings(data_dir=tmp_path / "data", secret_key="s", auth_enabled=False)))


def _project(client, n=300):
    pid = client.post("/api/projects", json={"name": "d", "crs": "local"}).json()["id"]
    rng = np.random.default_rng(1)
    x = np.concatenate([rng.uniform(0, 200, n), [0, 200, 0, 200]])
    y = np.concatenate([rng.uniform(0, 100, n), [0, 0, 100, 100]])
    rows = ["PtNo,Easting,Northing,Elevation"] + [f"{i+1},{x[i]:.3f},{y[i]:.3f},{plane_z(x[i], y[i]):.3f}" for i in range(len(x))]
    client.post(f"/api/projects/{pid}/import", files={"file": ("p.csv", io.BytesIO("\n".join(rows).encode()))})
    return pid


def test_module_registry(tmp_path):
    with _client(tmp_path) as client:
        mods = client.get("/api/modules").json()
        ids = {m["id"]: m for m in mods}
        assert ids["terrain"]["kind"] == "core"
        assert ids["road"]["status"] == "available" and [s["id"] for s in ids["road"]["stages"]][:2] == ["alignment", "profile"]
        assert ids["canal"]["status"] == "planned"


def test_design_lifecycle(tmp_path):
    with _client(tmp_path) as client:
        pid = _project(client)
        # a design needs a terrain snapshot
        r = client.post(f"/api/projects/{pid}/designs", json={"module": "road", "name": "Bypass"})
        assert r.status_code == 400 and "TIN" in r.json()["detail"]
        run1 = client.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "manual"}).json()["result"]["id"]
        al = client.post(f"/api/projects/{pid}/alignments", json={"name": "Road A", "start_chainage": 0,
                                                                  "ips": [{"x": 10, "y": 50, "radius": 0}, {"x": 100, "y": 60, "radius": 80}, {"x": 190, "y": 40, "radius": 0}]}).json()
        r = client.post(f"/api/projects/{pid}/designs", json={"module": "road", "name": "Bypass", "alignment_id": al["id"]})
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["tin_run_id"] == run1 and d["alignment_id"] == al["id"] and d["status"] == "draft"
        # planned modules cannot be created, unknown ones neither
        assert client.post(f"/api/projects/{pid}/designs", json={"module": "canal"}).status_code == 400
        assert client.post(f"/api/projects/{pid}/designs", json={"module": "bridge"}).status_code == 400
        assert client.post(f"/api/projects/{pid}/designs", json={"module": "road", "alignment_id": 999}).status_code == 404
        # listing, summary, default name
        d2 = client.post(f"/api/projects/{pid}/designs", json={"module": "road"}).json()
        assert d2["name"] == "Road design 2"
        assert [x["id"] for x in client.get(f"/api/projects/{pid}/designs").json()] == [d["id"], d2["id"]]
        assert client.get(f"/api/projects/{pid}").json()["summary"]["designs"] == {"road": 2}
        # settings merge and rebase onto a newer run
        r = client.patch(f"/api/projects/{pid}/designs/{d['id']}", json={"settings": {"road_class": "feeder"}})
        r = client.patch(f"/api/projects/{pid}/designs/{d['id']}", json={"settings": {"design_speed": 40}})
        assert r.json()["settings"] == {"road_class": "feeder", "design_speed": 40}
        run2 = client.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "manual"}).json()["result"]["id"]
        assert client.patch(f"/api/projects/{pid}/designs/{d['id']}", json={"tin_run_id": run2}).json()["tin_run_id"] == run2
        assert client.patch(f"/api/projects/{pid}/designs/{d['id']}", json={"tin_run_id": 999}).status_code == 404
        actions = {a["action"] for a in client.get(f"/api/projects/{pid}/activity").json()["activity"]}
        assert {"design_created", "design_rebased"} <= actions
        assert client.delete(f"/api/projects/{pid}/designs/{d2['id']}").status_code == 204
        assert client.get(f"/api/projects/{pid}/designs/{d2['id']}").status_code == 404
