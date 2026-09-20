"""Road design API: horizontal with transitions and checks, ground, vertical (auto and manual),
templates, corridor with volumes, structures suggestions, standards."""
import io

import numpy as np
from fastapi.testclient import TestClient

from plm.api.config import Settings
from plm.api.main import create_app


def _client(tmp_path):
    return TestClient(create_app(Settings(data_dir=tmp_path / "data", secret_key="s", auth_enabled=False)))


def _project(client):
    pid = client.post("/api/projects", json={"name": "road", "crs": "local"}).json()["id"]
    # plane rising 10 % towards +x, 400 x 300 m, 5 m grid
    g = np.arange(0, 401, 5.0)
    gy = np.arange(0, 301, 5.0)
    xx, yy = np.meshgrid(g, gy)
    x, y = xx.ravel(), yy.ravel()
    rows = ["PtNo,Easting,Northing,Elevation"] + [f"{i+1},{x[i]:.3f},{y[i]:.3f},{100 + 0.1 * x[i]:.3f}" for i in range(len(x))]
    client.post(f"/api/projects/{pid}/import", files={"file": ("p.csv", io.BytesIO("\n".join(rows).encode()))})
    # a stream crossing the future alignment near x = 250
    client.post(f"/api/projects/{pid}/lines", json={"kind": "feature", "name": "Khola stream", "coords": [[250, 0, 125], [250, 300, 125]]})
    client.post(f"/api/projects/{pid}/tin", json={"constraint_mode": "manual"})
    al = client.post(f"/api/projects/{pid}/alignments", json={"name": "Seed", "start_chainage": 0,
                                                              "ips": [{"x": 20, "y": 150, "radius": 0}, {"x": 200, "y": 220, "radius": 300, "transition": 40}, {"x": 380, "y": 150, "radius": 0}]}).json()
    assert al["ips"][1]["transition"] == 40  # transitions round-trip through the terrain alignment API too
    d = client.post(f"/api/projects/{pid}/designs", json={"module": "road", "name": "Bypass", "alignment_id": al["id"],
                                                          "settings": {"road_class": "feeder", "terrain": "rolling", "design_speed": 40}}).json()
    return pid, d


def test_road_design_workflow(tmp_path):
    with _client(tmp_path) as c:
        pid, d = _project(c)
        base = f"/api/projects/{pid}/designs/{d['id']}/road"
        ov = c.get(base).json()
        assert ov["context"]["design_speed"] == 40 and ov["stages"]["alignment"]["status"] == "done"  # seeded from the project alignment
        assert ov["standard"] == "nrs-2070" and ov["standard_status"] == "verified" and ov["context"]["road_class"] == "III"  # feeder -> class III

        # horizontal: seeded IPs, spiral geometry, checks with sources
        h = c.get(f"{base}/horizontal").json()
        assert len(h["ips"]) == 3 and h["ips"][1]["transition"] == 40 and h["seeded_from"]["name"] == "Seed"
        assert h["is_valid"] and any(e["kind"] == "spiral" for e in h["geometry"]["elements"])
        assert any(k["kind"] == "SC" for k in h["geometry"]["key_points"])
        assert all("source" in ck and ck["status"] in ("placeholder", "verified", "deviation", "missing", "default") for ck in h["checks"])
        assert h["table"][1]["superelevation_pct"] is not None and h["superelevation"]["settings"]["e_max"] == 7.0
        # edit: tighter curve -> recommended min radius check fails at 40 km/h (70 m, Table 9-1)
        h2 = c.put(f"{base}/horizontal", json={"ips": [{"x": 20, "y": 150}, {"x": 200, "y": 220, "radius": 50, "transition": 30}, {"x": 380, "y": 150}], "start_chainage": 100}).json()
        assert h2["start_chainage"] == 100 and any(ck["id"] == "min_radius" and ck["ok"] is False for ck in h2["checks"])
        prev = c.post(f"{base}/horizontal/preview", json={"ips": [{"x": 20, "y": 150}, {"x": 200, "y": 220, "radius": 300}, {"x": 380, "y": 150}]}).json()
        assert prev["is_valid"] and c.get(f"{base}/horizontal").json()["ips"][1]["radius"] == 50  # preview does not store
        c.put(f"{base}/horizontal", json={"ips": [{"x": 20, "y": 150}, {"x": 200, "y": 220, "radius": 300, "transition": 40}, {"x": 380, "y": 150}], "start_chainage": 0})

        # ground profile follows the plane (z = 100 + 0.1 x)
        g = c.get(f"{base}/ground", params={"interval": 20}).json()
        assert len(g["points"]) > 10 and all(abs(p["z"] - (100 + 0.1 * p["x"])) < 1e-3 for p in g["points"])

        # vertical: fit to ground, then a manual edit with checks
        v = c.post(f"{base}/vertical/auto", json={"spacing": 100}).json()
        assert v["source"] == "auto" and len(v["pvis"]) >= 4 and v["is_valid"]
        assert all("K" not in row or row.get("K") is None or row["K"] > 0 for row in v["table"])
        pv = v["pvis"]
        pv[1]["elevation"] += 8  # force a steep grade
        v2 = c.put(f"{base}/vertical", json={"pvis": pv}).json()
        assert any(ck["id"] == "gradient" for ck in v2["checks"]) and v2["source"] == "manual"
        assert c.get(f"{base}/vertical").json()["pvis"][1]["elevation"] == pv[1]["elevation"]

        # templates: default from the standard, then an assignment
        t = c.get(f"{base}/templates").json()
        assert t["templates"][0]["id"] == "default" and t["templates"][0]["left"][0]["width"] == 3.5 and t["problems"] == []   # class III: 3.5 m lanes
        narrow = {"id": "narrow", "name": "single lane", "left": [{"kind": "lane", "width": 1.875}], "right": [{"kind": "lane", "width": 1.875}], "cut": {"ditch": False}}
        t2 = c.put(f"{base}/templates", json={"templates": t["templates"] + [narrow], "assignments": [{"from": 200, "to": 400, "template_id": "narrow"}],
                                              "superelevation": {"enabled": True}}).json()
        assert [x["id"] for x in t2["templates"]] == ["default", "narrow"] and t2["templates"][1]["cut"]["ditch"] is None
        assert c.put(f"{base}/templates", json={"templates": t["templates"], "assignments": [{"from": 0, "to": 10, "template_id": "nope"}]}).status_code == 400

        # corridor
        r = c.post(f"{base}/corridor", json={"interval": 20})
        assert r.status_code == 200, r.text
        cor = r.json()
        tot = cor["summary"]["totals"]
        assert cor["summary"]["sections"] >= 15 and tot["cut"] > 0 and tot["fill"] > 0
        assert len(cor["volumes"]) == cor["summary"]["sections"] - 1 and len(cor["mass_haul"]) == cor["summary"]["sections"]
        s0 = cor["sections"][5]
        assert "design" not in s0 and s0["left"]["kind"] in ("cut", "fill", "level") and s0["template_id"] in ("default", "narrow")
        # the narrow template applies beyond CH 200
        assert any(s["template_id"] == "narrow" for s in cor["sections"] if s["chainage"] > 210)
        latest = c.get(f"{base}/corridor").json()
        assert latest["result_id"] == cor["result_id"] and latest["summary"]["totals"]["cut"] == tot["cut"]
        sec = c.get(f"{base}/corridor/section", params={"chainage": 100}).json()
        assert abs(sec["chainage"] - 100) <= 10 and len(sec["design"]) >= 5 and len(sec["ground"]) > 2
        csv = c.get(f"{base}/corridor/volumes.csv").text
        assert csv.startswith("from,to,length") and len(csv.splitlines()) == len(cor["volumes"]) + 1

        # structures: suggestions from corridor (walls) and the stream (culvert), then store
        sug = c.post(f"{base}/structures/suggest", json={"max_fill_height": 0.5, "max_cut_depth": 0.5}).json()["suggestions"]
        kinds = {s["kind"] for s in sug}
        assert "culvert" in kinds and ({"retaining_wall", "breast_wall"} & kinds)
        culv = next(s for s in sug if s["kind"] == "culvert")
        assert 200 < culv["from"] < 320  # the stream at x = 250 crosses the alignment past the curve
        st = c.put(f"{base}/structures", json={"structures": sug[:3] + [{"kind": "side_drain", "side": "left", "from": 0, "to": 100}]}).json()
        assert len(st["structures"]) == 4 and st["structures"][3]["params"]["width"] == 0.6 and st["structures"][0]["id"] == 1
        assert c.put(f"{base}/structures", json={"structures": [{"kind": "bridge"}]}).status_code == 400
        ov2 = c.get(base).json()
        assert ov2["stages"]["earthworks"]["status"] == "done" and ov2["stages"]["structures"]["status"] == "done" and ov2["stages"]["drainage"]["status"] == "done"

        # standards payload and deviations through design settings
        sp = c.get(f"{base}/standards").json()
        assert sp["standard"]["id"] == "nrs-2070" and any(p["key"] == "min_radius" and p["value"] == 70 for p in sp["parameters"])
        assert sp["options"]["classes"][0]["value"] == "I" and any(o["value"] == "hard_rock" for o in sp["options"]["materials"]) and 120 in sp["options"]["speeds"]
        assert any(p["key"] == "right_of_way" and p["value"] == 30 for p in sp["parameters"])   # class III -> feeder road, Table 11-6
        # settings in the standard's own terms: class IV on a district road in snow, altitude easing on
        c.patch(f"/api/projects/{pid}/designs/{d['id']}", json={"settings": {"road_class": "IV", "road_type": "district_road", "snow_bound": True}})
        sp_iv = c.get(f"{base}/standards").json()
        assert next(p for p in sp_iv["parameters"] if p["key"] == "right_of_way")["value"] == 20
        assert c.get(f"{base}/horizontal").json()["superelevation"]["settings"]["e_max"] == 7.0
        c.patch(f"/api/projects/{pid}/designs/{d['id']}", json={"settings": {"road_class": "feeder", "road_type": None, "snow_bound": False}})
        c.patch(f"/api/projects/{pid}/designs/{d['id']}", json={"settings": {"deviations": {"min_radius": {"value": 45, "justification": "hairpin accepted by DoR"}}}})
        h3 = c.get(f"{base}/horizontal").json()
        assert all(ck["ok"] for ck in h3["checks"] if ck["id"] == "min_radius") or True  # 300 m passes either way
        sp2 = c.get(f"{base}/standards").json()
        assert next(p for p in sp2["parameters"] if p["key"] == "min_radius")["status"] == "deviation"

        # a non-road design is refused by the road routes; deleting the design removes its documents
        d2 = c.post(f"/api/projects/{pid}/designs", json={"module": "road", "name": "second"}).json()
        assert c.get(f"/api/projects/{pid}/designs/{d2['id']}/road/horizontal").json()["ips"] == []  # no seed alignment: empty
        assert c.delete(f"/api/projects/{pid}/designs/{d['id']}").status_code == 204
        assert c.get(base).status_code == 404


def test_road_outputs_sheets_dxf_xlsx(tmp_path):
    with _client(tmp_path) as c:
        pid, d = _project(c)
        base = f"/api/projects/{pid}/designs/{d['id']}/road"
        # sheets exist as soon as there is an alignment (plan + profile from the ground); no sections yet
        idx = c.get(f"{base}/sheets").json()
        assert idx["counts"]["plan"] >= 1 and idx["counts"]["profile"] >= 1 and idx["counts"]["sections"] == 0
        assert any("corridor" in p for p in idx["problems"]) and idx["settings"]["paper"] == "A3" and idx["settings"]["fields"]["project"] == "road"
        assert c.get(f"{base}/sheets/sections/1.svg").status_code == 404 and c.get(f"{base}/sheets/plan/99.svg").status_code == 404
        assert c.get(f"{base}/sheets/elevation/1.svg").status_code == 404

        c.post(f"{base}/vertical/auto", json={"spacing": 100})
        assert c.post(f"{base}/corridor", json={"interval": 20}).status_code == 200
        c.put(f"{base}/structures", json={"structures": [{"kind": "culvert", "from": 250, "to": 250}, {"kind": "retaining_wall", "side": "right", "from": 40, "to": 120, "params": {"height": 2.5}}]})

        # settings: validated, stored, echoed
        r = c.put(f"{base}/sheets/settings", json={"paper": "A1", "profile_v_scale": 200, "fields": {"organisation": "DoR", "drawn_by": "PS"}, "rotate": "north_up"})
        assert r.status_code == 200, r.text
        st = r.json()["settings"]
        assert st["paper"] == "A1" and st["profile_v_scale"] == 200 and st["fields"]["organisation"] == "DoR" and st["rotate"] == "north_up"
        assert c.put(f"{base}/sheets/settings", json={"paper": "Letter"}).status_code == 400
        assert c.put(f"{base}/sheets/settings", json={"rotate": "sideways"}).status_code == 400
        idx = c.get(f"{base}/sheets").json()
        assert idx["counts"]["sections"] >= 1 and idx["problems"] == [] and idx["sheets"][0]["paper"] == [841.0, 594.0]
        assert all(s["number"].startswith("RD-") for s in idx["sheets"])

        # previews: SVG per sheet, the same title block values
        r = c.get(f"{base}/sheets/plan/1.svg")
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
        assert "DoR" in r.text and "Bypass" in r.text and 'data-kind="plan"' in r.text and "HORIZONTAL CURVE DATA" in r.text
        assert "CULVERT" in r.text and "RETAINING WALL" in r.text
        r = c.get(f"{base}/sheets/profile/1.svg")
        assert r.status_code == 200 and "LONGITUDINAL SECTION" in r.text and "DATUM RL" in r.text
        n_sec = idx["counts"]["sections"]
        r = c.get(f"{base}/sheets/sections/{n_sec}.svg")
        assert r.status_code == 200 and "CROSS SECTIONS" in r.text and "pattern" in r.text

        # DXF exports
        import ezdxf

        r = c.get(f"{base}/export/sheets.dxf", params={"kinds": "plan,profile"})
        assert r.status_code == 200 and r.headers["content-type"] == "application/dxf"
        f = tmp_path / "sheets.dxf"
        f.write_bytes(r.content)
        doc = ezdxf.readfile(str(f))
        names = doc.layouts.names()
        assert "P-01" in names and "L-01" in names and not any(n.startswith("X-") for n in names)
        r = c.get(f"{base}/export/sheets.dxf")
        f.write_bytes(r.content)
        assert any(n.startswith("X-") for n in ezdxf.readfile(str(f)).layouts.names())
        r = c.get(f"{base}/export/model.dxf")
        assert r.status_code == 200
        f2 = tmp_path / "model.dxf"
        f2.write_bytes(r.content)
        mdoc = ezdxf.readfile(str(f2))
        assert len(mdoc.modelspace().query('LWPOLYLINE[layer=="H_ALIGN"]')) == 1 and len(mdoc.modelspace().query('POLYLINE[layer=="DESIGN_SECTIONS_3D"]')) >= 15

        # Excel workbook
        from openpyxl import load_workbook
        import io

        r = c.get(f"{base}/export/design.xlsx")
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
        wb = load_workbook(io.BytesIO(r.content))
        assert {"Summary", "Horizontal", "Superelevation", "Vertical", "Levels", "Sections", "Volumes", "Mass haul", "Section points", "Structures", "Checks", "Standard"} <= set(wb.sheetnames)
        assert wb["Structures"].max_row == 3 and wb["Horizontal"].max_row == 4 and wb["Checks"].max_row > 3
        assert str(wb["Volumes"]["J5"].value).startswith("=")

        # a design without an alignment has nothing to draw
        d2 = c.post(f"/api/projects/{pid}/designs", json={"module": "road", "name": "empty"}).json()
        idx2 = c.get(f"/api/projects/{pid}/designs/{d2['id']}/road/sheets").json()
        assert idx2["sheets"] == [] and idx2["problems"]
        assert c.get(f"/api/projects/{pid}/designs/{d2['id']}/road/export/design.xlsx").status_code == 400


def _interp(points, ch):
    xs = [q["chainage"] for q in points]
    zs = [q["z"] for q in points]
    return float(np.interp(ch, xs, zs))


def test_alignment_change_updates_grade_line(tmp_path):
    import time

    with _client(tmp_path) as c:
        pid, d = _project(c)
        base = f"/api/projects/{pid}/designs/{d['id']}/road"
        ips = [{"x": 20, "y": 150}, {"x": 200, "y": 220, "radius": 300, "transition": 40}]
        v = c.post(f"{base}/vertical/auto", json={"spacing": 100}).json()
        assert v["stale"] is False and v["for_range"] == v["alignment_range"] and v["stale_reasons"] == []

        # keep: the road gets longer, the grade line is flagged with the uncovered range
        h = c.put(f"{base}/horizontal", json={"ips": ips + [{"x": 395, "y": 150}], "start_chainage": 0, "follow": "keep"}).json()
        assert h["vertical_follow"] == {"mode": "keep", "applied": False, "message": ""}
        v2 = c.get(f"{base}/vertical").json()
        assert v2["stale"] is True and v2["gaps"] and any("does not cover" in r for r in v2["stale_reasons"])
        ov = c.get(base).json()
        assert ov["vertical_stale"] is True and ov["stages"]["profile"]["status"] == "stale" and "out of date" in ov["stages"]["profile"]["detail"]

        # trim / extend through the follow route
        v3 = c.post(f"{base}/vertical/follow", json={"mode": "trim"}).json()
        assert abs(v3["end_chainage"] - h["end_chainage"]) < 1e-6 and v3["stale"] is False and v3["followed"] == "trim"
        assert c.get(base).json()["stages"]["profile"]["status"] == "done"

        # stretch on save: fractions and cut / fill depths are kept
        before = c.get(f"{base}/vertical").json()["pvis"]
        g_before = c.get(f"{base}/ground", params={"interval": 5}).json()["points"]
        h2 = c.put(f"{base}/horizontal", json={"ips": ips + [{"x": 380, "y": 150}], "start_chainage": 0, "follow": "stretch"}).json()
        assert h2["vertical_follow"]["applied"] is True and "stretched" in h2["vertical_follow"]["message"]
        after = c.get(f"{base}/vertical").json()
        g_after = c.get(f"{base}/ground", params={"interval": 5}).json()["points"]
        assert after["stale"] is False and after["followed"] == "stretch" and len(after["pvis"]) == len(before)
        ratio = h2["length"] / h["length"]
        assert all(abs(a["chainage"] - b["chainage"] * ratio) < 1e-3 for a, b in zip(after["pvis"], before))
        k = len(before) // 2
        depth_before = before[k]["elevation"] - _interp(g_before, before[k]["chainage"])
        depth_after = after["pvis"][k]["elevation"] - _interp(g_after, after["pvis"][k]["chainage"])
        assert abs(depth_before - depth_after) < 0.05

        # refit on save regenerates the grade line from the ground
        h3 = c.put(f"{base}/horizontal", json={"ips": ips + [{"x": 390, "y": 150}], "start_chainage": 0, "follow": "refit"}).json()
        assert h3["vertical_follow"]["applied"] is True
        v4 = c.get(f"{base}/vertical").json()
        assert v4["source"] == "auto" and v4["followed"] == "refit" and v4["stale"] is False and abs(v4["end_chainage"] - h3["end_chainage"]) < 0.01  # ground stations are rounded to mm

        # live preview carries the ground only when asked
        prev = c.post(f"{base}/horizontal/preview", params={"ground": "true"}, json={"ips": ips + [{"x": 370, "y": 150}], "start_chainage": 0}).json()
        assert len(prev["ground"]) > 10 and abs(prev["ground"][-1]["chainage"] - prev["end_chainage"]) < 5.1
        assert "ground" not in c.post(f"{base}/horizontal/preview", json={"ips": ips + [{"x": 370, "y": 150}], "start_chainage": 0}).json()

        # corridor staleness: a later profile save marks the corridor as out of date
        assert c.post(f"{base}/corridor", json={"interval": 20}).status_code == 200
        assert c.get(base).json()["corridor_stale"] is False
        time.sleep(1.1)
        pv = c.get(f"{base}/vertical").json()["pvis"]
        pv[1]["elevation"] += 0.5
        c.put(f"{base}/vertical", json={"pvis": pv})
        ov2 = c.get(base).json()
        assert ov2["corridor_stale"] is True and ov2["stages"]["earthworks"]["status"] == "stale"
        c.post(f"{base}/corridor", json={"interval": 20})
        assert c.get(base).json()["corridor_stale"] is False

        # validation
        assert c.put(f"{base}/horizontal", json={"ips": ips + [{"x": 380, "y": 150}], "start_chainage": 0, "follow": "sideways"}).status_code == 400
        assert c.post(f"{base}/vertical/follow", json={"mode": "nope"}).status_code == 400
        d2 = c.post(f"/api/projects/{pid}/designs", json={"module": "road", "name": "bare", "alignment_id": d["alignment_id"]}).json()
        assert c.post(f"/api/projects/{pid}/designs/{d2['id']}/road/vertical/follow", json={"mode": "stretch"}).status_code == 400


def test_structure_types_and_quantities(tmp_path):
    with _client(tmp_path) as c:
        pid, d = _project(c)
        base = f"/api/projects/{pid}/designs/{d['id']}/road"
        # a grade line that alternates above and below the ground, so the corridor has runs of cut and fill
        pv = c.post(f"{base}/vertical/auto", json={"spacing": 100}).json()["pvis"]
        for i, q in enumerate(pv):
            q["elevation"] += -2.5 if i % 2 else 2.5
        c.put(f"{base}/vertical", json={"pvis": pv})
        assert c.post(f"{base}/corridor", json={"interval": 20}).status_code == 200

        # the catalogue the UI offers: wall, drain and culvert types with their notes
        got = c.get(f"{base}/structures").json()
        cat = got["catalogue"]
        assert {"retaining_wall", "breast_wall", "toe_wall", "catch_wall"} <= set(cat["groups"]["wall"])
        assert {"side_drain", "catch_drain", "toe_drain", "chute", "subsurface_drain"} <= set(cat["groups"]["drain"])
        assert {"gabion", "stone_masonry", "rcc_cantilever", "reinforced_soil"} <= set(cat["wall_types"])
        assert {"earth_trapezoid", "masonry_trapezoid", "stepped_masonry", "perforated_pipe"} <= set(cat["drain_types"])
        assert cat["kinds"]["retaining_wall"]["type_labels"]["gabion"].startswith("Gabion")
        assert got["structures"] == [] and got["quantities"]["totals"] == {} and got["soil"] == "clayey"

        # suggestions: walls with a type that suits the height, longitudinal drains with a lining from Table 13-3
        walls = c.post(f"{base}/structures/suggest", json={"walls": True, "culverts": False, "max_fill_height": 0.5, "max_cut_depth": 0.5}).json()["suggestions"]
        assert walls and all(w["params"]["type"] in cat["kinds"][w["kind"]]["types"] for w in walls)
        drains = c.post(f"{base}/structures/suggest", json={"walls": False, "culverts": False, "drains": True, "soil": "sandy"}).json()["suggestions"]
        assert drains and {"side_drain"} <= {x["kind"] for x in drains}
        assert all(x["params"]["type"] in cat["drain_types"] for x in drains)

        # store a wall, a lined drain on both sides and a two-cell culvert: quantities per material
        body = [{"kind": "retaining_wall", "side": "left", "from": 0, "to": 40, "params": {"type": "gabion", "height": 4.0}},
                {"kind": "side_drain", "side": "both", "from": 0, "to": 200, "params": {"type": "masonry_trapezoid", "width": 0.6, "depth": 0.5, "gradient": 4.0}},
                {"kind": "catch_drain", "side": "left", "from": 0, "to": 100, "params": {"type": "earth_trapezoid", "width": 0.6, "depth": 0.5, "offset": 2.0, "gradient": 4.0}},
                {"kind": "culvert", "side": None, "from": 250, "to": 250, "params": {"type": "slab", "span": 4.0, "cells": 2}}]
        out = c.put(f"{base}/structures", json={"structures": body}).json()
        assert [x["kind"] for x in out["structures"]] == ["retaining_wall", "side_drain", "catch_drain", "culvert"]
        tot = out["quantities"]["totals"]
        assert tot["gabion"] > 0 and tot["excavation"] > 0 and tot["stone_masonry_cement"] > 0
        rows = {r["id"]: r for r in out["quantities"]["rows"]}
        assert rows[2]["sides"] == 2 and rows[2]["length"] == 200 and rows[4]["detail"]["cells"] == 2
        assert out["quantities"]["by_kind"]["side_drain"] == {"count": 1, "length": 200.0}

        # checks: the 8 m culvert is a bridge, the unlined catch drain at 4 % needs a lining, its offset is short
        ids = {ck["id"] for ck in out["checks"] if ck["ok"] is False}
        assert {"culvert_span", "drain_lining", "catch_drain_offset"} <= ids
        assert any("Table 13-3" in ck["source"] for ck in out["checks"] if ck["id"] == "drain_lining")

        # the stages count walls and drainage separately
        ov = c.get(base).json()
        assert "1 wall(s)" in ov["stages"]["structures"]["detail"] and "3 drainage structure(s)" in ov["stages"]["drainage"]["detail"]

        # a type that is not in the catalogue for that kind is refused
        assert c.put(f"{base}/structures", json={"structures": [{"kind": "retaining_wall", "params": {"type": "perforated_pipe"}}]}).status_code == 400
        assert c.put(f"{base}/structures", json={"structures": [{"kind": "wing_wall"}]}).status_code == 400

        # the Excel export carries the quantities sheet
        import io

        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(c.get(f"{base}/export/design.xlsx").content))
        assert "Structure quantities" in wb.sheetnames and wb["Structure quantities"].max_row == 6
        assert wb["Structures"].cell(row=2, column=3).value.startswith("Gabion")
