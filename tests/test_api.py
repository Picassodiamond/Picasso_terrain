"""End-to-end API tests with FastAPI TestClient (temporary data directory)."""
from __future__ import annotations

import io
import json
import struct

import numpy as np
import pytest
from fastapi.testclient import TestClient

from plm.api.config import Settings
from plm.api.main import create_app


def _settings(tmp_path, **kw) -> Settings:
    return Settings(data_dir=tmp_path / "data", secret_key="test-secret", auth_enabled=kw.get("auth", False),
                    open_registration=kw.get("open", True), sync_point_limit=kw.get("sync_limit", 250_000))


@pytest.fixture
def client(tmp_path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as c:
        yield c


def _points_csv(n=300, seed=1) -> bytes:
    rng = np.random.default_rng(seed)
    x = rng.uniform(0, 200, n)
    y = rng.uniform(0, 100, n)
    x = np.concatenate([x, [0, 200, 0, 200]])
    y = np.concatenate([y, [0, 0, 100, 100]])
    z = 100 + 0.1 * x + 0.05 * y
    rows = ["PtNo,Easting,Northing,Elevation,Remark"] + [f"{i+1},{x[i]:.3f},{y[i]:.3f},{z[i]:.3f},GL" for i in range(len(x))]
    return "\n".join(rows).encode()


def _lines_geojson() -> bytes:
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[20, 20, 300], [100, 60, 300], [180, 80, 300]]},
         "properties": {"kind": "feature", "layer": "Features", "name": "ridge"}},
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[10, 10], [190, 10], [190, 90], [10, 90], [10, 10]]]},
         "properties": {"kind": "boundary"}},
    ]}
    return json.dumps(gj).encode()


def _make_project(client, name="Test", crs="local") -> str:
    r = client.post("/api/projects", json={"name": name, "crs": crs})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _import(client, pid, filename, data, **form):
    r = client.post(f"/api/projects/{pid}/import", files={"file": (filename, io.BytesIO(data))}, data=form)
    assert r.status_code == 200, r.text
    return r.json()


def test_health_and_docs(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/openapi.json").status_code == 200
    assert client.get("/api/crs/presets").status_code == 200
    assert client.get("/api/crs/describe", params={"spec": "MUTM84"}).json()["is_local"] is False


def test_project_crud(client):
    pid = _make_project(client, "Road A")
    r = client.get("/api/projects")
    assert r.status_code == 200 and any(p["id"] == pid for p in r.json())
    r = client.patch(f"/api/projects/{pid}", json={"name": "Road A1", "crs": "UTM45N", "description": "test"})
    assert r.status_code == 200 and r.json()["crs_info"]["epsg"] == 32645
    assert client.post("/api/projects", json={"name": "bad", "crs": "EPSG:999999"}).status_code == 400
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_full_pipeline(client, tmp_path):
    pid = _make_project(client)
    res = _import(client, pid, "pts.csv", _points_csv())
    assert res["points_added"] == 304
    res = _import(client, pid, "lines.geojson", _lines_geojson())
    assert res["lines_added"] == {"feature": 1, "boundary": 1}
    pj = client.get(f"/api/projects/{pid}").json()
    assert pj["summary"]["points"] == 304 and pj["summary"]["lines"]["boundary"] == 1

    # points / lines geojson
    gj = client.get(f"/api/projects/{pid}/points.geojson").json()
    assert len(gj["features"]) == 304 and gj["features"][0]["properties"]["remark"] == "GL"
    assert len(client.get(f"/api/projects/{pid}/lines.geojson", params={"kind": "feature"}).json()["features"]) == 1
    # no CRS -> transform must fail cleanly
    assert client.get(f"/api/projects/{pid}/points.geojson", params={"crs": "EPSG:4326"}).status_code == 400

    # TIN (sync)
    r = client.post(f"/api/projects/{pid}/tin", json={"boundary_mode": "inside"})
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["status"] == "done", job
    run_id = job["result"]["id"]
    run = client.get(f"/api/projects/{pid}/tin/{run_id}").json()
    # 2 breakline segments + 4 boundary sides: the boundary is a constraint, not just a clip
    assert run["stats"]["triangles_removed_by_boundary"] > 0 and run["stats"]["segments"] == 6
    assert run["stats"]["constraints"] == {"boundary": 1, "hole": 0, "breakline": 1}
    assert run["stats"]["validation_problems"] == 0
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "done"

    # mesh binary
    b = client.get(f"/api/projects/{pid}/tin/{run_id}/mesh.bin").content
    assert b[:4] == b"PLMM"
    ver, n, m, dtype = struct.unpack("<IIII", b[4:20])
    assert n == run["n_nodes"] and m == run["n_triangles"] and dtype == 0
    origin = struct.unpack("<3d", b[20:44])
    pos = np.frombuffer(b[44:44 + n * 12], dtype=np.float32).reshape(n, 3)
    idx = np.frombuffer(b[44 + n * 12:], dtype=np.uint32)
    assert len(idx) == m * 3 and idx.max() < n
    assert pos.min() >= 0 and origin[0] >= 0
    hull = client.get(f"/api/projects/{pid}/tin/{run_id}/hull.geojson").json()
    assert hull["features"][0]["geometry"]["type"] == "Polygon"

    # spot height and ad-hoc profile
    e = client.get(f"/api/projects/{pid}/tin/{run_id}/elevation", params={"x": 50, "y": 50}).json()
    assert e["inside"] and abs(e["z"] - (100 + 5 + 2.5)) < 1e-3
    prof = client.post(f"/api/projects/{pid}/tin/{run_id}/profile", json={"coords": [[20, 50], [180, 50]]}).json()
    assert len(prof["distance"]) > 5 and prof["z"][0] is not None

    # contours
    r = client.post(f"/api/projects/{pid}/contours", json={"interval": 1.0, "major_every": 5, "style": {"major_color": "#ff0000"}})
    assert r.status_code == 202 and r.json()["status"] == "done", r.text
    set_id = r.json()["result"]["id"]
    cs = client.get(f"/api/projects/{pid}/contours/{set_id}").json()
    assert cs["n_lines"] > 5 and cs["style"]["major_color"] == "#ff0000"
    gj = client.get(f"/api/projects/{pid}/contours/{set_id}.geojson").json()
    assert len(gj["features"]) == cs["n_lines"]
    assert all(len(f["geometry"]["coordinates"][0]) == 3 for f in gj["features"])
    lbl = client.get(f"/api/projects/{pid}/contours/{set_id}/labels.geojson", params={"every": 30}).json()
    assert lbl["features"] and "text" in lbl["features"][0]["properties"]
    r = client.patch(f"/api/projects/{pid}/contours/{set_id}", json={"name": "1 m contours", "style": {"label_format": "{z:.0f} m"}})
    assert r.json()["name"] == "1 m contours" and r.json()["style"]["label_format"] == "{z:.0f} m"

    # alignment
    aln = {"name": "Road A", "start_chainage": 1000, "ips": [{"x": 20, "y": 30}, {"x": 100, "y": 40, "radius": 60}, {"x": 180, "y": 80}]}
    r = client.post(f"/api/projects/{pid}/alignments/preview", json=aln)
    assert r.status_code == 200 and r.json()["valid"]
    r = client.post(f"/api/projects/{pid}/alignments", json=aln)
    assert r.status_code == 201, r.text
    a = r.json()
    aid = a["id"]
    assert a["version"] == 1 and a["length"] > 100 and any(k["kind"] == "MC" for k in a["key_points"])
    g = client.get(f"/api/projects/{pid}/alignments/{aid}/geometry.geojson", params={"chainage_interval": 20}).json()
    kinds = {f["properties"]["kind"] for f in g["features"]}
    assert {"centreline", "chainage", "IP", "BC", "EC"} <= kinds
    assert client.get(f"/api/projects/{pid}/alignments/{aid}.csv").text.startswith("2,1000")
    # edit -> version 2
    aln["ips"][1]["radius"] = 50
    r = client.put(f"/api/projects/{pid}/alignments/{aid}", json=aln, params={"note": "smaller curve"})
    assert r.status_code == 200 and r.json()["version"] == 2
    vs = client.get(f"/api/projects/{pid}/alignments/{aid}/versions").json()
    assert [v["version"] for v in vs] == [2, 1] and vs[0]["note"] == "smaller curve"
    r = client.post(f"/api/projects/{pid}/alignments/{aid}/versions/1/restore", json={"note": "back"})
    assert r.status_code == 200 and r.json()["ips"][1]["radius"] == 60

    # sections
    r = client.post(f"/api/projects/{pid}/sections", json={"alignment_id": aid, "interval": 20, "left": 15, "right": 15})
    assert r.status_code == 202 and r.json()["status"] == "done", r.text
    sid = r.json()["result"]["id"]
    ss = client.get(f"/api/projects/{pid}/sections/{sid}").json()
    assert ss["summary"]["sections"] == len(ss["sections"]) > 5
    assert ss["profile"][0]["chainage"] == 1000
    prof_csv = client.get(f"/api/projects/{pid}/sections/{sid}/profile.csv").text
    assert prof_csv.startswith("Chainage,RL,Remarks")
    cross_csv = client.get(f"/api/projects/{pid}/sections/{sid}/cross.csv").text
    assert cross_csv.splitlines()[1].startswith("1000.000,-15.00")
    assert client.get(f"/api/projects/{pid}/sections/{sid}/lines.geojson").json()["features"]

    # exports
    r = client.get(f"/api/projects/{pid}/export.dxf", params={"tin": "true", "alignments": str(aid), "section_set": sid})
    assert r.status_code == 200 and r.content[:20].lstrip().startswith(b"0")
    import ezdxf
    dxf_path = tmp_path / "export.dxf"
    dxf_path.write_bytes(r.content)
    doc = ezdxf.readfile(str(dxf_path))
    assert len(doc.audit().errors) == 0
    msp = doc.modelspace()
    layers = {e.dxf.layer for e in msp}
    assert {"Points-Blk", "Triangle", "Contour", "H_ALIGN", "Chainage", "Cross_Section", "Features", "Boundary"} <= layers
    h = [e for e in msp.query("LWPOLYLINE") if e.dxf.layer == "H_ALIGN"]
    assert h and any(abs(b) > 0 for (_, _, _, _, b) in h[0].get_points("xyseb"))
    r = client.get(f"/api/projects/{pid}/export.gpkg")
    assert r.status_code == 200 and r.content[:15] == b"SQLite format 3"
    r = client.get(f"/api/projects/{pid}/export.geojson")
    assert r.status_code == 200 and len(r.json()["features"]) > 304

    # activity feed + jobs list
    act = client.get(f"/api/projects/{pid}/activity").json()["activity"]
    actions = {a["action"] for a in act}
    assert {"import", "tin_done", "contours_done", "alignment_created", "alignment_edited", "sections_done"} <= actions
    assert len(client.get(f"/api/projects/{pid}/jobs").json()) == 3

    # delete cascade
    assert client.delete(f"/api/projects/{pid}/tin/{run_id}").status_code == 204
    assert client.get(f"/api/projects/{pid}/contours").json() == []


def test_background_job_and_worker(tmp_path):
    settings = _settings(tmp_path, sync_limit=10)  # force background path
    app = create_app(settings)
    with TestClient(app) as client:
        pid = _make_project(client)
        _import(client, pid, "pts.csv", _points_csv(50))
        r = client.post(f"/api/projects/{pid}/tin", json={})
        assert r.status_code == 202
        job = client.get(f"/api/jobs/{r.json()['id']}").json()
        assert job["status"] == "done"  # TestClient runs background tasks before returning
        # worker path: create a pending job manually and run the worker
        j = app.state.db.create_job(pid, "contours", {"interval": 2.0, "major_every": 5})
        from plm.api.jobs import run_pending_jobs
        assert run_pending_jobs(settings) == 1
        assert app.state.db.get_job(j["id"])["status"] == "done"
        assert app.state.db.create_job(pid, "contours", {"interval": 1.0, "run_id": 999})
        run_pending_jobs(settings)
        errs = app.state.db.list_jobs(pid, status="error")
        assert errs and "not found" in errs[0]["error"]


def test_csv_variants_and_dxf_import(client, tmp_path):
    pid = _make_project(client)
    res = _import(client, pid, "a.xyz", b"1 2 3\n4 5 6\n7 8 9\n")
    assert res["points_added"] == 3
    res = _import(client, pid, "b.txt", b"id;x;y;z\n1;10;20;30\n", delimiter=";")
    assert res["points_added"] == 1
    res = _import(client, pid, "c.csv", b"a,b,c\n1,2,3\n", mapping=json.dumps({"x": 0, "y": 1, "z": 2}), has_header="true", layer="Extra")
    assert res["points_added"] == 1
    assert any(lay["layer"] == "Extra" for lay in client.get(f"/api/projects/{pid}/points/layers").json())
    assert client.post(f"/api/projects/{pid}/import", files={"file": ("x.bin", io.BytesIO(b"\x00"))}).status_code == 415
    # export DXF then re-import into a new project
    _import(client, pid, "pts.csv", _points_csv(80), replace="true")
    client.post(f"/api/projects/{pid}/tin", json={})
    client.post(f"/api/projects/{pid}/contours", json={"interval": 1})
    dxf = client.get(f"/api/projects/{pid}/export.dxf").content
    pid2 = _make_project(client, "reimport")
    res = _import(client, pid2, "survey.dxf", dxf)
    assert res["points_added"] == 84 and res["lines_added"].get("contour", 0) > 0
    # delete helpers
    assert client.delete(f"/api/projects/{pid2}/lines", params={"kind": "contour"}).json()["deleted"] > 0
    assert client.delete(f"/api/projects/{pid2}/points", params={"all": "true"}).json()["deleted"] == 84


def test_drawn_lines_and_voids(client):
    pid = _make_project(client)
    _import(client, pid, "pts.csv", _points_csv(400))
    r = client.post(f"/api/projects/{pid}/lines", json={"kind": "void", "coords": [[80, 40], [120, 40], [120, 60], [80, 60], [80, 40]]})
    assert r.status_code == 200 and r.json()["added"] == 1
    r = client.post(f"/api/projects/{pid}/tin", json={})
    run_id = r.json()["result"]["id"]
    e = client.get(f"/api/projects/{pid}/tin/{run_id}/elevation", params={"x": 100, "y": 50}).json()
    assert e["inside"] is False


def test_auth_and_collaboration(tmp_path):
    app = create_app(_settings(tmp_path, auth=True, open=True))
    with TestClient(app) as anon:
        # with auth on, an anonymous visitor is a guest in a sandbox (see test_access for quotas)
        assert anon.get("/api/projects").status_code == 200 and anon.get("/api/auth/me").json()["guest"] is True
        st = anon.get("/api/auth/status").json()
        assert st["auth_enabled"] and st["open_registration"] and st["guest_enabled"]
    # two users
    alice = TestClient(app)
    bob = TestClient(app)
    r = alice.post("/api/auth/register", json={"username": "alice", "password": "secret123", "organisation": "SSTN"})
    assert r.status_code == 201 and r.json()["role"] == "admin"  # first user is admin
    # org visibility now means the owner's organisation: bob joins SSTN too
    r = bob.post("/api/auth/register", json={"username": "bob", "password": "secret456", "organisation": "SSTN"})
    assert r.status_code == 201 and r.json()["role"] == "editor"
    assert alice.get("/api/auth/me").json()["username"] == "alice"
    assert bob.post("/api/auth/login", json={"username": "bob", "password": "wrong"}).status_code == 401
    assert bob.post("/api/auth/login", json={"username": "bob", "password": "secret456"}).status_code == 200

    pid = _make_project(alice, "Shared road")
    assert alice.get(f"/api/projects/{pid}/members").json()[0]["username"] == "alice"
    # bob sees org-visible project and can edit
    assert any(p["id"] == pid for p in bob.get("/api/projects").json())
    alice.post(f"/api/projects/{pid}/members", json={"username": "bob", "role": "editor"})
    _import(bob, pid, "pts.csv", _points_csv(100))

    aln = {"name": "Option 1", "ips": [{"x": 10, "y": 10}, {"x": 100, "y": 30, "radius": 40}, {"x": 190, "y": 90}]}
    aid = bob.post(f"/api/projects/{pid}/alignments", json=aln).json()["id"]
    # bob takes the editing turn
    r = bob.post(f"/api/projects/{pid}/alignments/{aid}/lock")
    assert r.status_code == 200 and r.json()["username"] == "bob"
    # alice cannot edit or lock while bob holds the lock
    assert alice.post(f"/api/projects/{pid}/alignments/{aid}/lock").status_code == 409
    r = alice.put(f"/api/projects/{pid}/alignments/{aid}", json=aln)
    assert r.status_code == 409 and "bob" in r.json()["detail"]
    a = alice.get(f"/api/projects/{pid}/alignments/{aid}").json()
    assert a["lock"]["username"] == "bob"
    # alice gives feedback instead
    c = alice.post(f"/api/projects/{pid}/comments", json={"text": "Curve too tight at IP 1", "target_type": "alignment", "target_id": str(aid), "chainage": 95.0}).json()
    assert c["username"] == "alice" and c["resolved"] is False
    reply = bob.post(f"/api/projects/{pid}/comments", json={"text": "Will widen to R=60", "parent_id": c["id"], "target_type": "alignment", "target_id": str(aid)}).json()
    assert reply["parent_id"] == c["id"]
    aln["ips"][1]["radius"] = 60
    assert bob.put(f"/api/projects/{pid}/alignments/{aid}", json=aln, params={"note": "R=60 per Alice"}).status_code == 200
    assert bob.patch(f"/api/projects/{pid}/comments/{c['id']}", json={"resolved": True}).json()["resolved"] is True
    # bob releases; alice's turn
    assert bob.delete(f"/api/projects/{pid}/alignments/{aid}/lock").json()["released"] is True
    assert alice.post(f"/api/projects/{pid}/alignments/{aid}/lock").status_code == 200
    # admin can force release
    assert bob.delete(f"/api/projects/{pid}/alignments/{aid}/lock", params={"force": "true"}).status_code == 403
    assert alice.delete(f"/api/projects/{pid}/alignments/{aid}/lock", params={"force": "true"}).json()["released"] is True
    act = alice.get(f"/api/projects/{pid}/activity").json()
    names = {a["username"] for a in act["activity"]}
    assert {"alice", "bob"} <= names
    assert act["locks"] == []
    comments = alice.get(f"/api/projects/{pid}/comments", params={"target_type": "alignment", "target_id": str(aid)}).json()
    assert len(comments) == 2
    # private project visibility
    pid2 = _make_project(alice, "Private")
    alice.patch(f"/api/projects/{pid2}", json={"settings": {"visibility": "private"}})
    assert not any(p["id"] == pid2 for p in bob.get("/api/projects").json())
    # logout
    alice.post("/api/auth/logout")
    assert alice.get("/api/auth/me").json()["guest"] is True  # back to a guest sandbox identity
    # registration closed after toggling
    app.state.settings.open_registration = False
    assert TestClient(app).post("/api/auth/register", json={"username": "carol", "password": "secret789"}).status_code == 403


def _gap_points_csv(seed=5) -> bytes:
    """Random survey with a building gap and a notch cut out of the east side."""
    rng = np.random.default_rng(seed)
    x = rng.uniform(0, 200, 2500)
    y = rng.uniform(0, 100, 2500)
    building = (x > 80) & (x < 110) & (y > 40) & (y < 65)
    notch = (x > 160) & (y > 30) & (y < 70)
    keep = ~building & ~notch
    x, y = x[keep], y[keep]
    z = 100 + 0.1 * x + 0.05 * y
    rows = ["PtNo,Easting,Northing,Elevation"] + [f"{i+1},{x[i]:.3f},{y[i]:.3f},{z[i]:.3f}" for i in range(len(x))]
    return "\n".join(rows).encode()


def test_constraint_workflow_semi_automatic(client):
    pid = _make_project(client, "gaps")
    _import(client, pid, "pts.csv", _gap_points_csv())

    # detect: boundary + gap suggestions, nothing stored yet
    r = client.post(f"/api/projects/{pid}/constraints/detect", json={"edge_factor": 3.0})
    assert r.status_code == 200, r.text
    sug = r.json()
    kinds = [f["properties"]["kind"] for f in sug["features"]]
    assert kinds.count("boundary") == 1 and "hole" in kinds
    assert all("reason" in f["properties"] and 0 < f["properties"]["confidence"] <= 1 for f in sug["features"])
    assert sug["stats"]["peeled_triangles"] > 0
    assert client.get(f"/api/projects/{pid}/lines/summary").json() == []

    # user reviews: accept everything
    feats = [{"kind": f["properties"]["kind"], "coords": f["geometry"]["coordinates"], "name": f["properties"]["reason"]} for f in sug["features"]]
    r = client.post(f"/api/projects/{pid}/constraints/accept", json={"features": feats})
    assert r.status_code == 200 and r.json()["added"] == len(feats)
    cons = client.get(f"/api/projects/{pid}/constraints", params={"source": "accepted"}).json()
    assert len(cons["features"]) == len(feats)
    assert {f["properties"]["kind"] for f in cons["features"]} == {"boundary", "void"}

    # TIN in semi mode honours them; rejected triangles are available for display
    r = client.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "semi", "min_angle_deg": 5.0})
    assert r.status_code == 202 and r.json()["status"] == "done", r.text
    run_id = r.json()["result"]["id"]
    run = client.get(f"/api/projects/{pid}/tin/{run_id}").json()
    assert run["stats"]["constraints"]["boundary"] == 1 and run["stats"]["constraints"]["hole"] >= 1
    assert run["stats"]["validation_problems"] == 0
    rej = client.get(f"/api/projects/{pid}/tin/{run_id}/rejected.geojson").json()
    assert rej["total"] > 0 and rej["counts"].get("hole", 0) > 0 and rej["counts"].get("outside_boundary", 0) > 0
    assert rej["features"][0]["geometry"]["type"] == "Polygon" and rej["features"][0]["properties"]["reason"]
    # inside the gap there is no surface; on the surveyed ground there is
    assert client.get(f"/api/projects/{pid}/tin/{run_id}/elevation", params={"x": 95, "y": 52}).json()["inside"] is False
    assert client.get(f"/api/projects/{pid}/tin/{run_id}/elevation", params={"x": 185, "y": 50}).json()["inside"] is False
    assert client.get(f"/api/projects/{pid}/tin/{run_id}/elevation", params={"x": 50, "y": 50}).json()["inside"] is True
    # contours come from the validated TIN only
    r = client.post(f"/api/projects/{pid}/contours", json={"interval": 1.0})
    assert r.json()["status"] == "done"


def test_constraint_workflow_automatic(client):
    pid = _make_project(client, "auto")
    _import(client, pid, "pts.csv", _gap_points_csv(seed=9))
    # automatic mode: detection runs inside the job and stores the constraints with source=auto
    r = client.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "auto", "max_edge_factor": 4.0})
    assert r.status_code == 202 and r.json()["status"] == "done", r.text
    run = client.get(f"/api/projects/{pid}/tin/{r.json()['result']['id']}").json()
    assert run["stats"]["constraint_mode"] == "auto" and run["stats"]["detection"]["suggestions"] >= 2
    auto = client.get(f"/api/projects/{pid}/constraints", params={"source": "auto"}).json()["features"]
    assert sum(1 for f in auto if f["properties"]["kind"] == "boundary") == 1
    # rebuilding replaces the automatic constraints instead of piling them up
    client.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "auto"})
    auto2 = client.get(f"/api/projects/{pid}/constraints", params={"source": "auto"}).json()["features"]
    assert len(auto2) == len(auto)
    # a boundary drawn by the user switches detection off; the auto boundary is no longer used
    r = client.post(f"/api/projects/{pid}/lines", json={"kind": "boundary", "coords": [[20, 20], [150, 20], [150, 80], [20, 80], [20, 20]]})
    assert r.status_code == 200
    r = client.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "auto"})
    run = client.get(f"/api/projects/{pid}/tin/{r.json()['result']['id']}").json()
    assert "detection" not in run["stats"]
    assert client.delete(f"/api/projects/{pid}/constraints/auto").json()["deleted"] >= 1
    assert client.get(f"/api/projects/{pid}/constraints", params={"source": "auto"}).json()["features"] == []
    # manual mode with the drawn boundary only
    r = client.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "manual"})
    run = client.get(f"/api/projects/{pid}/tin/{r.json()['result']['id']}").json()
    assert run["stats"]["constraints"] == {"boundary": 1, "hole": 0, "breakline": 0}
    assert client.get(f"/api/projects/{pid}/tin/{run['id']}/elevation", params={"x": 10, "y": 10}).json()["inside"] is False
