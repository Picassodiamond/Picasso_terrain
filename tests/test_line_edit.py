"""Editing the vertices of a constraint line: breaklines, boundaries and voids."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from plm.api.config import Settings
from plm.api.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path / "data", secret_key="test-secret", auth_enabled=False))) as c:
        yield c


@pytest.fixture
def project(client):
    pid = client.post("/api/projects", json={"name": "lines", "crs": "local"}).json()["id"]
    csv = "PtNo,Easting,Northing,Elevation\n" + "\n".join(
        f"{i},{x},{y},{100 + x * 0.1}" for i, (x, y) in enumerate([(0, 0), (100, 0), (100, 100), (0, 100), (50, 50)], 1))
    r = client.post(f"/api/projects/{pid}/import", files={"file": ("p.csv", io.BytesIO(csv.encode()))})
    assert r.status_code == 200, r.text
    return pid


def _add(client, pid, kind, coords, name="test"):
    r = client.post(f"/api/projects/{pid}/lines", json={"kind": kind, "name": name, "coords": coords})
    assert r.status_code in (200, 201), r.text
    fc = client.get(f"/api/projects/{pid}/lines.geojson").json()
    return fc["features"][-1]["properties"]["fid"]


def _coords(client, pid, fid):
    fc = client.get(f"/api/projects/{pid}/lines.geojson").json()
    f = next(x for x in fc["features"] if x["properties"]["fid"] == fid)
    return f["geometry"]["coordinates"], f["properties"]


# ---------------------------------------------------------------- breaklines
def test_a_breakline_vertex_can_be_moved(client, project):
    fid = _add(client, project, "breakline", [[10, 10], [50, 20], [90, 10]])
    r = client.patch(f"/api/projects/{project}/lines/{fid}", json={"coords": [[10, 10], [50, 75], [90, 10]]})
    assert r.status_code == 200, r.text
    assert r.json()["rebuild_tin"] is True
    coords, props = _coords(client, project, fid)
    assert [round(c[0], 3) for c in coords] == [10, 50, 90]
    assert round(coords[1][1], 3) == 75          # the moved vertex
    assert len(coords) == 3 and props["kind"] == "feature"


def test_vertices_can_be_added_and_removed(client, project):
    fid = _add(client, project, "breakline", [[0, 0], [100, 100]])
    client.patch(f"/api/projects/{project}/lines/{fid}", json={"coords": [[0, 0], [40, 60], [70, 80], [100, 100]]})
    coords, _ = _coords(client, project, fid)
    assert len(coords) == 4
    client.patch(f"/api/projects/{project}/lines/{fid}", json={"coords": [[0, 0], [100, 100]]})
    coords, _ = _coords(client, project, fid)
    assert len(coords) == 2


def test_a_vertex_keeps_its_level_when_one_is_given(client, project):
    fid = _add(client, project, "breakline", [[10, 10], [90, 90]])
    client.patch(f"/api/projects/{project}/lines/{fid}", json={"coords": [[10, 10, 123.5], [90, 90, 130.25]]})
    coords, _ = _coords(client, project, fid)
    assert [round(c[2], 3) for c in coords] == [123.5, 130.25]


def test_a_breakline_cannot_be_reduced_to_one_vertex(client, project):
    fid = _add(client, project, "breakline", [[0, 0], [50, 50], [100, 100]])
    r = client.patch(f"/api/projects/{project}/lines/{fid}", json={"coords": [[0, 0]]})
    assert r.status_code == 400
    assert "two vertices" in r.json()["detail"]


# ---------------------------------------------------------------- closed kinds
def test_a_boundary_is_closed_for_you(client, project):
    fid = _add(client, project, "boundary", [[0, 0], [80, 0], [80, 80], [0, 80], [0, 0]])
    r = client.patch(f"/api/projects/{project}/lines/{fid}", json={"coords": [[5, 5], [90, 5], [90, 90], [5, 90]]})
    assert r.status_code == 200, r.text
    coords, props = _coords(client, project, fid)
    assert coords[0][:2] == coords[-1][:2], "the ring should come back closed"
    assert len(coords) == 5 and props["closed"] is True


def test_a_boundary_needs_three_corners(client, project):
    fid = _add(client, project, "boundary", [[0, 0], [80, 0], [80, 80], [0, 0]])
    r = client.patch(f"/api/projects/{project}/lines/{fid}", json={"coords": [[0, 0], [80, 0], [0, 0]]})
    assert r.status_code == 400
    assert "three corners" in r.json()["detail"]


def test_a_void_is_treated_the_same_way(client, project):
    fid = _add(client, project, "hole", [[20, 20], [40, 20], [40, 40], [20, 20]])
    r = client.patch(f"/api/projects/{project}/lines/{fid}", json={"coords": [[20, 20], [60, 20], [60, 60], [20, 60]]})
    assert r.status_code == 200, r.text
    coords, props = _coords(client, project, fid)
    assert props["kind"] == "void" and props["closed"] is True and len(coords) == 5


def test_changing_the_kind_to_boundary_closes_the_ring(client, project):
    fid = _add(client, project, "breakline", [[0, 0], [70, 0], [70, 70]])
    r = client.patch(f"/api/projects/{project}/lines/{fid}", json={"kind": "boundary", "coords": [[0, 0], [70, 0], [70, 70]]})
    assert r.status_code == 200, r.text
    coords, props = _coords(client, project, fid)
    assert props["kind"] == "boundary" and coords[0][:2] == coords[-1][:2]


# ---------------------------------------------------------------- housekeeping
def test_bad_numbers_are_refused(client, project):
    fid = _add(client, project, "breakline", [[0, 0], [50, 50]])
    r = client.patch(f"/api/projects/{project}/lines/{fid}", json={"coords": [[0, 0], [None, 5]]})
    assert r.status_code == 422        # the schema rejects a null coordinate


def test_editing_a_line_that_is_not_there(client, project):
    r = client.patch(f"/api/projects/{project}/lines/99999", json={"coords": [[0, 0], [1, 1]]})
    assert r.status_code == 404


def test_the_old_query_parameter_form_still_works(client, project):
    fid = _add(client, project, "breakline", [[0, 0], [50, 50]])
    r = client.patch(f"/api/projects/{project}/lines/{fid}?kind=contour")
    assert r.status_code == 200, r.text
    _, props = _coords(client, project, fid)
    assert props["kind"] == "contour"


def test_a_moved_vertex_is_used_by_the_next_tin(client, project):
    """The terrain is a snapshot: an edit changes what the *next* build sees, not the last one."""
    fid = _add(client, project, "breakline", [[10, 10], [90, 90]])
    before = client.post(f"/api/projects/{project}/tin", json={}).json()
    assert before["status"] in ("done", "pending", "running")
    client.patch(f"/api/projects/{project}/lines/{fid}", json={"coords": [[10, 10], [50, 90], [90, 90]]})
    lines = client.get(f"/api/projects/{project}/lines.geojson").json()["features"]
    assert len(next(f for f in lines if f["properties"]["fid"] == fid)["geometry"]["coordinates"]) == 3
    after = client.post(f"/api/projects/{project}/tin", json={}).json()
    assert after["id"] != before["id"]


# ---------------------------------------------------------------- plan editing keeps levels
def test_a_plan_edit_keeps_the_levels_of_vertices_that_did_not_move(client, project):
    """Constraint lines are edited in plan. A breakline imported with surveyed levels must not lose
    them because a different vertex moved; a vertex that did move gets 0, which the engine reads as
    "interpolate me from the survey"."""
    fid = _add(client, project, "breakline", [[10, 10, 201.5], [50, 50, 202.5], [90, 90, 203.5]])
    r = client.patch(f"/api/projects/{project}/lines/{fid}", json={"coords": [[10, 10], [60, 40], [90, 90]]})
    assert r.status_code == 200, r.text
    coords, _ = _coords(client, project, fid)
    assert round(coords[0][2], 3) == 201.5, "the untouched first vertex kept its level"
    assert round(coords[2][2], 3) == 203.5, "the untouched last vertex kept its level"
    assert round(coords[1][2], 3) == 0.0, "the moved vertex is left for the engine to interpolate"


def test_an_explicit_level_still_wins(client, project):
    fid = _add(client, project, "breakline", [[0, 0, 100.0], [80, 80, 110.0]])
    client.patch(f"/api/projects/{project}/lines/{fid}", json={"coords": [[0, 0, 150.0], [80, 80]]})
    coords, _ = _coords(client, project, fid)
    assert round(coords[0][2], 3) == 150.0 and round(coords[1][2], 3) == 110.0
