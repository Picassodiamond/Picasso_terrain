"""Visitor register: first-visit detection by address, the introduction form, and the usage log."""
from __future__ import annotations

import io

import numpy as np
import pytest
from fastapi.testclient import TestClient

from plm.api.config import Settings
from plm.api.main import create_app
from plm.api.visitors import match_action

from conftest import plane_z


def _settings(tmp_path, **kw) -> Settings:
    return Settings(data_dir=tmp_path / "data", secret_key="test-secret", auth_enabled=kw.get("auth", False),
                    open_registration=True, visitor_tracking=kw.get("tracking", True),
                    visitor_intake=kw.get("intake", True), visitor_intake_required=kw.get("required", False),
                    visitor_intake_repeat_days=kw.get("repeat_days", 7))


@pytest.fixture
def app(tmp_path):
    return create_app(_settings(tmp_path))


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def _csv(n=200, seed=3) -> bytes:
    rng = np.random.default_rng(seed)
    x = np.concatenate([rng.uniform(0, 200, n), [0, 200, 0, 200]])
    y = np.concatenate([rng.uniform(0, 100, n), [0, 0, 100, 100]])
    rows = ["PtNo,Easting,Northing,Elevation"] + [f"{i+1},{x[i]:.3f},{y[i]:.3f},{plane_z(x[i], y[i]):.3f}" for i in range(len(x))]
    return "\n".join(rows).encode()


def _project(c, name="Visitor test") -> str:
    r = c.post("/api/projects", json={"name": name, "crs": "local"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------------------------------------------------------- route table
def test_action_table_recognises_the_major_activities():
    assert match_action("POST", "/api/projects/ab12/tin")[0] == "tin.build"
    assert match_action("POST", "/api/projects/ab12/contours")[0] == "contour.generate"
    assert match_action("POST", "/api/projects/ab12/designs/3/road/corridor")[:1] == ("road.corridor",)
    assert match_action("GET", "/api/projects/ab12/designs/3/road/export/design.xlsx")[0] == "export.xlsx"
    # ordinary reads are not activities
    assert match_action("GET", "/api/projects/ab12/tin") is None
    assert match_action("GET", "/api/projects/ab12/points") is None


# ---------------------------------------------------------------- first visit
def test_first_visit_is_detected_and_the_cookie_is_issued(client):
    r = client.get("/api/visitor/me")
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["tracking"] and st["first_visit"] and st["needs_intake"]
    assert st["visitor"]["ip"]
    assert "plm_visitor" in client.cookies


def test_a_returning_visitor_is_not_a_first_visit(client):
    first = client.get("/api/visitor/me").json()["visitor"]["id"]
    again = client.get("/api/visitor/me").json()
    assert again["visitor"]["id"] == first
    assert not again["first_visit"]


def test_a_known_address_without_a_cookie_is_recognised(app):
    with TestClient(app) as a:
        a.post("/api/visitor/intake", json={"name": "Ram Thapa", "email": "ram@example.np", "phone": "9800000000",
                                            "designation": "Divisional Engineer"})
    with TestClient(app) as b:  # same address, cookies cleared
        st = b.get("/api/visitor/me").json()
        assert st["visitor"]["name"] == "Ram Thapa"
        assert not st["needs_intake"]


def test_the_form_is_stored_and_not_asked_again(client):
    r = client.post("/api/visitor/intake", json={"name": "Sita Gurung", "email": "sita@example.np",
                                                 "phone": "9811111111", "designation": "Sub-engineer",
                                                 "organisation": "DoLI", "district": "Kaski",
                                                 "purpose": "Rural road alignment"})
    assert r.status_code == 200, r.text
    assert r.json()["visitor"]["registered"] is True
    st = client.get("/api/visitor/me").json()
    assert not st["needs_intake"] and st["visitor"]["designation"] == "Sub-engineer"


def test_not_now_silences_the_form_for_the_repeat_period(client):
    client.get("/api/visitor/me")
    assert client.post("/api/visitor/skip").json()["ok"] is True
    assert client.get("/api/visitor/me").json()["needs_intake"] is False


def test_the_form_is_rejected_without_a_name_or_email(client):
    assert client.post("/api/visitor/intake", json={"name": "X", "email": "a@b"}).status_code == 422


def test_tracking_can_be_switched_off(tmp_path):
    with TestClient(create_app(_settings(tmp_path, tracking=False))) as c:
        st = c.get("/api/visitor/me").json()
        assert st["tracking"] is False and st["needs_intake"] is False and st["visitor"] is None


# ---------------------------------------------------------------- usage log
def test_major_activities_are_logged_but_reads_are_not(client):
    pid = _project(client)
    assert client.post(f"/api/projects/{pid}/import", files={"file": ("p.csv", io.BytesIO(_csv()))}).status_code == 200
    assert client.post(f"/api/projects/{pid}/tin", json={}).status_code in (200, 202)
    client.get(f"/api/projects/{pid}/points")  # a read: must not appear

    rows = client.get("/api/visitor/admin/usage").json()["rows"]
    actions = [r["action"] for r in rows]
    assert "project.create" in actions and "data.import" in actions and "tin.build" in actions
    assert all(not a.startswith("read") for a in actions)
    created = next(r for r in rows if r["action"] == "project.create")
    assert created["project_id"] == pid  # the new project's id is taken from the reply
    row = next(r for r in rows if r["action"] == "tin.build")
    assert row["project_id"] == pid and row["label"] == "Built a TIN surface" and row["ip"]
    assert row["ms"] >= 0 and row["status"] < 400


def test_the_log_carries_the_visitor_details_and_counts_up(client):
    client.post("/api/visitor/intake", json={"name": "Hari Bhandari", "email": "hari@example.np",
                                             "designation": "Consultant"})
    _project(client, "Counted")
    rows = client.get("/api/visitor/admin/usage").json()["rows"]
    assert rows[0]["visitor_name"] == "Hari Bhandari"
    v = client.get("/api/visitor/admin/visitors").json()[0]
    assert v["actions"] >= 2 and v["registered"] == 1


def test_a_failed_action_is_not_logged(client):
    client.post("/api/projects/does-not-exist/tin", json={})
    assert not [r for r in client.get("/api/visitor/admin/usage").json()["rows"] if r["action"] == "tin.build"]


def test_summary_and_csv_downloads(client):
    client.post("/api/visitor/intake", json={"name": "Gita Rai", "email": "gita@example.np", "phone": "9822222222"})
    _project(client, "Summary")
    s = client.get("/api/visitor/admin/summary").json()
    assert s["visitors"] == 1 and s["registered"] == 1 and s["logged_actions"] >= 2
    assert any(a["action"] == "project.create" for a in s["by_action"])

    csv_v = client.get("/api/visitor/admin/visitors.csv")
    assert csv_v.status_code == 200 and "Gita Rai" in csv_v.text and "9822222222" in csv_v.text
    csv_u = client.get("/api/visitor/admin/usage.csv")
    assert csv_u.status_code == 200 and "Created a project" in csv_u.text


def test_the_address_comes_from_the_proxy_header(client):
    st = client.get("/api/visitor/me", headers={"X-Forwarded-For": "202.79.32.10, 10.0.0.1"}).json()
    assert st["visitor"]["ip"] == "202.79.32.10"


# ---------------------------------------------------------------- enforcement
def test_when_required_the_form_blocks_major_actions_until_it_is_filled(tmp_path):
    with TestClient(create_app(_settings(tmp_path, required=True))) as c:
        r = c.post("/api/projects", json={"name": "Blocked", "crs": "local"})
        assert r.status_code == 428 and r.json()["code"] == "visitor_intake"
        c.post("/api/visitor/intake", json={"name": "Bikash K.C.", "email": "bikash@example.np"})
        assert c.post("/api/projects", json={"name": "Allowed", "crs": "local"}).status_code == 201


# ---------------------------------------------------------------- access control
def test_the_register_is_admin_only(tmp_path):
    app = create_app(_settings(tmp_path, auth=True))
    with TestClient(app) as admin:
        admin.post("/api/auth/register", json={"username": "admin1", "password": "password123"})
        assert admin.get("/api/visitor/admin/visitors").status_code == 200
    with TestClient(app) as guest:
        assert guest.get("/api/visitor/admin/visitors").status_code == 403
        assert guest.get("/api/visitor/me").status_code == 200  # but anyone may see their own state


def test_signing_in_links_the_account_to_the_visitor(tmp_path):
    app = create_app(_settings(tmp_path, auth=True))
    with TestClient(app) as c:
        c.post("/api/auth/register", json={"username": "engineer", "password": "password123"})
        c.post("/api/visitor/intake", json={"name": "Nabin Shrestha", "email": "nabin@example.np"})
        v = c.get("/api/visitor/admin/visitors").json()[0]
        assert v["user_id"] and v["name"] == "Nabin Shrestha"
        rows = c.get("/api/visitor/admin/usage").json()["rows"]
        assert any(r["action"] == "account.register" for r in rows)
