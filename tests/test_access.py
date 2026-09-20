"""Accounts, project roles, guest sandbox, queue / busy handling, catalogue, archive."""
import io
import struct
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient

from plm.api import services
from plm.api.archive import backup
from plm.api.config import Settings
from plm.api.main import create_app

from conftest import plane_z


def _settings(tmp_path, **kw) -> Settings:
    return Settings(data_dir=tmp_path / "data", secret_key="test-secret", auth_enabled=True, open_registration=kw.get("open", False),
                    guest_enabled=kw.get("guest", True), guest_max_points=kw.get("guest_points", 400), guest_max_projects=2,
                    guest_max_tin_runs=2, sync_point_limit=250_000, max_concurrent_heavy=kw.get("heavy", 2),
                    busy_queue_threshold=kw.get("busy", 3))


def _csv(n=300, seed=1) -> bytes:
    rng = np.random.default_rng(seed)
    x = np.concatenate([rng.uniform(0, 200, n), [0, 200, 0, 200]])
    y = np.concatenate([rng.uniform(0, 100, n), [0, 0, 100, 100]])
    rows = ["PtNo,Easting,Northing,Elevation"] + [f"{i+1},{x[i]:.3f},{y[i]:.3f},{plane_z(x[i], y[i]):.3f}" for i in range(len(x))]
    return "\n".join(rows).encode()


def _import(c, pid, data=None):
    r = c.post(f"/api/projects/{pid}/import", files={"file": ("p.csv", io.BytesIO(data or _csv()))})
    return r


def _login(app, username, password) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


def _bootstrap(app) -> TestClient:
    """First registration creates the admin; the admin then creates the other accounts."""
    admin = TestClient(app)
    assert admin.post("/api/auth/register", json={"username": "admin", "password": "adminpass1", "organisation": "DoR"}).status_code == 201
    return admin


# ------------------------------------------------------------------ invite-only accounts
def test_invite_only_accounts(tmp_path):
    app = create_app(_settings(tmp_path))
    admin = _bootstrap(app)
    st = admin.get("/api/auth/status").json()
    assert st["open_registration"] is False and st["guest_enabled"] is True
    # nobody else can self-register
    r = TestClient(app).post("/api/auth/register", json={"username": "eve", "password": "evepass123"})
    assert r.status_code == 403 and "administrator" in r.json()["detail"]
    # the admin creates accounts with full details
    r = admin.post("/api/auth/users", json={"username": "ram", "password": "rampass123", "role": "editor", "organisation": "DoR",
                                            "full_name": "Ram Thapa", "email": "ram@example.org", "notes": "district engineer"})
    assert r.status_code == 201 and r.json()["full_name"] == "Ram Thapa" and "password_hash" not in r.json()
    assert admin.post("/api/auth/users", json={"username": "ram", "password": "rampass123"}).status_code == 409
    ram = _login(app, "ram", "rampass123")
    me = ram.get("/api/auth/me").json()
    assert me["role"] == "editor" and me["full_name"] == "Ram Thapa" and me["authenticated"] is True
    # non-admins cannot manage users
    assert ram.post("/api/auth/users", json={"username": "x", "password": "xpass1234"}).status_code == 403
    # admin resets a password and disables the account
    uid = me["id"]
    assert admin.patch(f"/api/auth/users/{uid}", json={"password": "newpass123", "role": "viewer"}).status_code == 200
    assert TestClient(app).post("/api/auth/login", json={"username": "ram", "password": "rampass123"}).status_code == 401
    assert _login(app, "ram", "newpass123").get("/api/auth/me").json()["role"] == "viewer"
    admin.patch(f"/api/auth/users/{uid}", json={"disabled": True})
    assert TestClient(app).post("/api/auth/login", json={"username": "ram", "password": "newpass123"}).status_code == 401
    # an admin cannot lock themselves out
    assert admin.patch(f"/api/auth/users/{me['id']}", json={"disabled": False}).status_code == 200
    my_id = admin.get("/api/auth/me").json()["id"]
    assert admin.patch(f"/api/auth/users/{my_id}", json={"disabled": True}).status_code == 400


# ------------------------------------------------------------------ project roles and sharing
def test_project_roles_and_owner_only_sharing(tmp_path):
    app = create_app(_settings(tmp_path))
    admin = _bootstrap(app)
    for u, org in (("owner", "DoR"), ("mate", "DoR"), ("other", "Consult"), ("view", "DoR")):
        admin.post("/api/auth/users", json={"username": u, "password": f"{u}pass123", "organisation": org, "role": "viewer" if u == "view" else "editor"})
    owner = _login(app, "owner", "ownerpass123")
    mate = _login(app, "mate", "matepass123")
    other = _login(app, "other", "otherpass123")
    view = _login(app, "view", "viewpass123")

    p = owner.post("/api/projects", json={"name": "Shared", "crs": "local", "settings": {"visibility": "private"}}).json()
    pid = p["id"]
    assert p["my_role"] == "owner" and p["owner_id"]
    assert _import(owner, pid).status_code == 200
    # private: nobody else sees it
    assert mate.get(f"/api/projects/{pid}").status_code == 403
    assert [x["id"] for x in mate.get("/api/projects").json()] == []
    # only the owner shares
    assert mate.post(f"/api/projects/{pid}/members", json={"username": "other", "role": "editor"}).status_code == 403
    assert owner.post(f"/api/projects/{pid}/members", json={"username": "mate", "role": "viewer"}).status_code == 201
    assert mate.get(f"/api/projects/{pid}").json()["my_role"] == "viewer"
    # a viewer reads but cannot write
    assert mate.get(f"/api/projects/{pid}/points.geojson").status_code == 200
    assert _import(mate, pid).status_code == 403
    assert mate.post(f"/api/projects/{pid}/tin", json={}).status_code == 403
    # owner promotes to editor -> can build
    assert owner.patch(f"/api/projects/{pid}/members/{mate.get('/api/auth/me').json()['id']}", json={"role": "editor"}).status_code == 200
    assert mate.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "manual"}).json()["status"] == "done"
    # visibility is owner-only; editors cannot change it
    assert mate.patch(f"/api/projects/{pid}", json={"settings": {"visibility": "org"}}).status_code == 403
    assert owner.patch(f"/api/projects/{pid}", json={"settings": {"visibility": "org"}}).status_code == 200
    # org visibility: same organisation edits (view has viewer account role -> viewer), other org sees nothing
    assert view.get(f"/api/projects/{pid}").json()["my_role"] == "viewer"
    assert other.get(f"/api/projects/{pid}").status_code == 403
    assert owner.patch(f"/api/projects/{pid}", json={"settings": {"visibility": "public"}}).status_code == 200
    assert other.get(f"/api/projects/{pid}").json()["my_role"] == "viewer"
    assert _import(other, pid).status_code == 403
    # delete / transfer are owner-only
    assert mate.delete(f"/api/projects/{pid}").status_code == 403
    assert owner.post(f"/api/projects/{pid}/transfer", json={"username": "mate"}).status_code == 200
    assert mate.get(f"/api/projects/{pid}").json()["my_role"] == "owner"
    assert owner.get(f"/api/projects/{pid}").json()["my_role"] == "editor"
    assert owner.delete(f"/api/projects/{pid}").status_code == 403
    assert mate.delete(f"/api/projects/{pid}").status_code == 204


# ------------------------------------------------------------------ guest sandbox
def test_guest_sandbox_quotas_and_claim(tmp_path):
    app = create_app(_settings(tmp_path, guest_points=400))
    admin = _bootstrap(app)
    admin.post("/api/auth/users", json={"username": "sita", "password": "sitapass123"})
    guest = TestClient(app)
    me = guest.get("/api/auth/me").json()
    assert me["guest"] is True and me["authenticated"] is False and me["quota"]["max_points"] == 400
    assert "plm_guest" in guest.cookies
    p = guest.post("/api/projects", json={"name": "try", "crs": "local"}).json()
    assert p["settings"]["visibility"] == "private" and p["my_role"] == "owner"
    pid = p["id"]
    # points quota: 304 ok, another 304 would exceed 400
    assert _import(guest, pid).status_code == 200
    r = _import(guest, pid)
    assert r.status_code == 403 and "guest sandbox" in r.json()["detail"]
    # project quota
    guest.post("/api/projects", json={"name": "try2", "crs": "local"})
    r = guest.post("/api/projects", json={"name": "try3", "crs": "local"})
    assert r.status_code == 403
    # TIN builds queue behind accounts and are capped
    j = guest.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "manual"}).json()
    assert j["status"] == "done" and j["priority"] == 10
    guest.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "manual"})
    assert guest.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "manual"}).status_code == 403
    # no exports for guests
    assert guest.get(f"/api/projects/{pid}/export.dxf").status_code == 403
    # another guest cannot see the sandbox; a signed-in stranger neither
    assert TestClient(app).get(f"/api/projects/{pid}").status_code == 401
    # signing in claims the sandbox
    r = guest.post("/api/auth/login", json={"username": "sita", "password": "sitapass123"})
    assert r.status_code == 200 and r.json()["claimed_projects"] == 2
    pj = guest.get(f"/api/projects/{pid}").json()
    assert pj["my_role"] == "owner" and not pj["owner_id"].startswith("guest:")
    assert guest.get(f"/api/projects/{pid}/export.dxf").status_code == 200


def test_guest_disabled_requires_login(tmp_path):
    app = create_app(_settings(tmp_path, guest=False))
    _bootstrap(app)
    assert TestClient(app).get("/api/auth/me").status_code == 401
    assert TestClient(app).get("/api/projects").status_code == 401


# ------------------------------------------------------------------ queue, busy, memory
def test_queue_position_and_busy_responses(tmp_path, monkeypatch):
    app = create_app(_settings(tmp_path, heavy=1))
    admin = _bootstrap(app)
    pid = admin.post("/api/projects", json={"name": "q", "crs": "local"}).json()["id"]
    _import(admin, pid)
    run = admin.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "manual"}).json()
    assert run["status"] == "done" and run["queue_length"] == 0 and run["queue_position"] is None
    # heavy slot taken -> detect answers 503 with Retry-After
    assert app.state.heavy.acquire()
    r = admin.post(f"/api/projects/{pid}/constraints/detect", json={})
    assert r.status_code == 503 and r.headers.get("retry-after") == "10"
    h = admin.get("/api/health").json()
    assert h["busy"] is True and h["load"]["heavy_in_use"] == 1
    # a sync TIN request while the slot is busy is queued instead of refused
    j = admin.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "manual", "sync": True}).json()
    assert j["status"] in ("pending", "running", "done")
    app.state.heavy.release()
    assert admin.post(f"/api/projects/{pid}/constraints/detect", json={}).status_code == 200
    assert admin.get("/api/health").json()["busy"] is False
    # worker mode: the web process only enqueues; queue info counts positions
    app.state.settings.job_mode = "worker"
    a = admin.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "manual", "sync": False}).json()
    b = admin.post(f"/api/projects/{pid}/contours", json={"interval": 1.0, "sync": False}).json()
    assert a["status"] == "pending" and a["queue_position"] == 1
    assert b["queue_position"] == 2 and b["queue_length"] == 2
    assert admin.get("/api/health").json()["queue"]["pending"] == 2
    # the worker drains them in order (fair queue)
    from plm.api.jobs import run_pending_jobs

    assert run_pending_jobs(app.state.settings) == 2
    assert admin.get(f"/api/jobs/{a['id']}").json()["status"] == "done"
    assert admin.get(f"/api/jobs/{b['id']}").json()["status"] == "done"
    app.state.settings.job_mode = "inline"
    # memory guard
    monkeypatch.setattr("plm.api.deps.available_memory_mb", lambda: 0.1)  # 304 points need ~0.4 MB
    r = admin.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "manual"})
    assert r.status_code == 507 and "memory" in r.json()["detail"]


# ------------------------------------------------------------------ catalogue and clone
def test_catalogue_footprints_and_clone(tmp_path):
    app = create_app(_settings(tmp_path))
    admin = _bootstrap(app)
    admin.post("/api/auth/users", json={"username": "user2", "password": "u2pass1234", "organisation": "Other"})
    u2 = _login(app, "user2", "u2pass1234")
    # a georeferenced project gets a lon/lat footprint
    pid = admin.post("/api/projects", json={"name": "Nagarkot", "crs": "UTM45N", "settings": {"tags": ["bhaktapur", "hill"], "visibility": "private"}}).json()["id"]
    rng = np.random.default_rng(2)
    x = 354_000 + rng.uniform(0, 300, 200)
    y = 3_066_000 + rng.uniform(0, 200, 200)
    rows = ["PtNo,Easting,Northing,Elevation"] + [f"{i+1},{x[i]:.3f},{y[i]:.3f},{1800 + 0.01 * (x[i] - 354_000):.3f}" for i in range(200)]
    _import(admin, pid, "\n".join(rows).encode())
    run_id = admin.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "manual"}).json()["result"]["id"]
    items = admin.get("/api/catalogue").json()
    assert len(items) == 1
    it = items[0]
    assert it["kind"] == "tin" and it["ref_id"] == run_id and it["tags"] == ["bhaktapur", "hill"]
    assert it["footprint"]["type"] == "Polygon" and 85.5 < it["lon"] < 85.53 and 27.71 < it["lat"] < 27.72
    assert it["triangles"] > 0 and it["spacing"] and it["project_name"] == "Nagarkot"
    # search by text / tag / bbox; private -> hidden from others
    assert len(admin.get("/api/catalogue", params={"q": "hill"}).json()) == 1
    assert len(admin.get("/api/catalogue", params={"tag": "nope"}).json()) == 0
    assert len(admin.get("/api/catalogue", params={"bbox": "85.4,27.6,85.6,27.8"}).json()) == 1
    assert len(admin.get("/api/catalogue", params={"bbox": "80,20,81,21"}).json()) == 0
    assert u2.get("/api/catalogue").json() == []
    # a design adds an entry; public visibility exposes both
    d = admin.post(f"/api/projects/{pid}/designs", json={"module": "road", "name": "Bypass"}).json()
    admin.patch(f"/api/projects/{pid}", json={"settings": {"visibility": "public"}})
    kinds = sorted(i["kind"] for i in u2.get("/api/catalogue").json())
    assert kinds == ["design", "tin"]
    # clone into a new project owned by the cloner
    r = u2.post(f"/api/catalogue/{it['id']}/clone", json={"name": "My copy"})
    assert r.status_code == 201, r.text
    cp = r.json()
    assert cp["name"] == "My copy" and cp["settings"]["cloned_from"]["project_id"] == pid
    cpj = u2.get(f"/api/projects/{cp['id']}").json()
    assert cpj["my_role"] == "owner" and cpj["summary"]["points"] == 200 and cpj["summary"]["tin_runs"] == 1
    assert admin.get(f"/api/projects/{cp['id']}").json()["my_role"] == "owner"  # admin sees everything
    # deleting the design removes its entry
    admin.delete(f"/api/projects/{pid}/designs/{d['id']}")
    assert all(i["kind"] == "tin" for i in admin.get("/api/catalogue", params={"q": "Nagarkot"}).json())
    # local-grid projects are catalogued without a map footprint
    pid2 = admin.post("/api/projects", json={"name": "Local", "crs": "local"}).json()["id"]
    _import(admin, pid2)
    admin.post(f"/api/projects/{pid2}/tin", json={"constraint_mode": "manual"})
    loc = [i for i in admin.get("/api/catalogue").json() if i["project_id"] == pid2][0]
    assert loc["footprint"] is None and loc["bounds"] is not None


# ------------------------------------------------------------------ archive, restore, backup
def test_archive_restore_and_backup(tmp_path):
    app = create_app(_settings(tmp_path))
    admin = _bootstrap(app)
    admin.post("/api/auth/users", json={"username": "editor1", "password": "edpass1234", "organisation": "DoR"})
    ed = _login(app, "editor1", "edpass1234")
    pid = ed.post("/api/projects", json={"name": "Old road", "crs": "local"}).json()["id"]
    _import(ed, pid)
    ed.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "manual"})
    r = ed.post(f"/api/projects/{pid}/archive")
    assert r.status_code == 200 and r.json()["status"] == "archived"
    # archived: readable, not writable (423)
    assert ed.get(f"/api/projects/{pid}/points.geojson").status_code == 200
    assert _import(ed, pid).status_code == 423
    assert ed.post(f"/api/projects/{pid}/tin", json={}).status_code == 423
    # bundle contents
    z = ed.get(f"/api/projects/{pid}/archive.zip")
    assert z.status_code == 200
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        assert set(zf.namelist()) == {"project.gpkg", "meta.json"}
        assert b"plm-archive/1" in zf.read("meta.json")
    assert len(ed.get("/api/archives").json()) >= 1
    assert admin.get("/api/archives").json()
    # a member who is not the owner cannot archive or restore
    ed.post(f"/api/projects/{pid}/members", json={"username": "admin", "role": "editor"})
    assert ed.post(f"/api/projects/{pid}/restore").status_code == 200
    assert ed.get(f"/api/projects/{pid}").json()["status"] == "active"
    assert _import(ed, pid).status_code == 200
    # hidden from the list when asked
    assert ed.post(f"/api/projects/{pid}/archive").status_code == 200
    assert pid not in [p["id"] for p in ed.get("/api/projects", params={"include_archived": "false"}).json()]
    assert pid in [p["id"] for p in ed.get("/api/projects").json()]
    # server backup
    out = backup(app.state.settings, tmp_path / "bk")
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "plm.db" in names and any(n.endswith("project.gpkg") for n in names)


@pytest.mark.parametrize("fmt", ["PLMP"])
def test_points_bin_still_works_for_viewers(tmp_path, fmt):
    app = create_app(_settings(tmp_path))
    admin = _bootstrap(app)
    pid = admin.post("/api/projects", json={"name": "v", "crs": "local", "settings": {"visibility": "public"}}).json()["id"]
    _import(admin, pid)
    b = TestClient(app).get(f"/api/projects/{pid}/points.bin").content  # guest viewer of a public project
    assert b[:4] == fmt.encode() and struct.unpack("<III", b[4:16])[1] == 304
    assert services.tin_cache_info()["limit"] >= 1
