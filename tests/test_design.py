"""Road design engine: standards, spirals, superelevation, vertical alignment, templates, corridor,
earthworks, structures."""
import numpy as np
import pytest

from plm.design import earthworks
from plm.design.corridor import areas, build_corridor, daylight_side
from plm.design.horizontal import check_horizontal, curve_table
from plm.design.standards import list_standards, load_standard
from plm.design.structures import normalise_structure, suggest_culverts, suggest_walls
from plm.design.superelevation import build_profile, design_rate, required_rate
from plm.design.template import default_template, hinge_polyline, normalise_template, template_at, validate_templates
from plm.design.vertical import PVI, VerticalAlignment
from plm.engine import PointSet, build_tin
from plm.engine.alignment import IP, HorizontalAlignment

CTX = {"road_class": "feeder", "terrain": "rolling", "design_speed": 60, "surface": "bituminous", "material": "soil"}


# ------------------------------------------------------------------ standards
def test_standard_resolution_and_deviations():
    assert any(s["id"] == "nrs-2070" for s in list_standards())
    std = load_standard("nrs-2070")
    # legacy class names map to the NRS functional classes (feeder -> III): Table 7-1, class III rolling = 60 km/h
    p = std.resolve("design_speed", road_class="feeder", terrain="rolling")
    assert p.value == 60 and p.status == "verified" and "Table 7-1" in p.source
    assert std.resolve("design_speed", road_class="I", terrain="plain").value == 120 and std.resolve("design_speed", road_class="IV", terrain="steep").value == 20
    # numeric keys: nearest not-larger speed (Table 9-1, no superelevation column)
    assert std.resolve("min_radius", design_speed=60).value == 200
    assert std.resolve("min_radius", design_speed=70).value == 200
    assert std.resolve("min_radius", design_speed=10).value == 20  # below the smallest: smallest row
    assert std.resolve("min_radius_absolute", design_speed=60).value == 110 and std.resolve("min_radius_comfort", design_speed=60).value == 190
    # interpolated tables (Table 9-2 by radius, Table 10-1 by speed, Table 24-4 friction)
    assert std.resolve("min_transition_length", radius=100).value == 50 and std.resolve("min_transition_length", radius=70).value == 42.5
    assert std.resolve("min_transition_length", radius=5000).value == 120 and std.resolve("max_gradient", design_speed=50).value == 8
    assert abs(std.resolve("side_friction", design_speed=50).value - 0.20) < 1e-9
    # ceil lookup (Table 11-4 embankment slopes by height, Table 9-4 extra widening)
    assert std.resolve("fill_slope", fill_height=1.0).value == 4.0 and std.resolve("fill_slope", fill_height=1.5).value == 4.0
    assert std.resolve("fill_slope", fill_height=2.0).value == 3.0 and std.resolve("fill_slope", fill_height=5.0).value == 2.0 and std.resolve("fill_slope", fill_height=20).value == 2.0
    assert std.resolve("extra_widening_double", radius=20).value == 1.5 and std.resolve("extra_widening_double", radius=25).value == 1.5
    assert std.resolve("extra_widening_double", radius=50).value == 1.2 and std.resolve("extra_widening_double", radius=350).value == 0
    assert std.resolve("fill_slope").status == "missing" and std.resolve("min_radius").status == "missing"
    assert std.resolve("nonexistent").status == "missing"
    assert std.resolve("bench_height").status == "default"   # application default, not in the standard
    assert std.canonical("classes", "national") == "II" and std.canonical("materials", "rock") == "medium_rock" and std.canonical("classes", "IV") == "IV"
    dev = load_standard("nrs-2070", {"min_radius": {"value": 100, "justification": "client accepted 100 m at IP 4"}})
    p = dev.resolve("min_radius", design_speed=60)
    assert p.value == 100 and p.status == "deviation" and "client" in p.source


# ------------------------------------------------------------------ spirals
def test_transition_curve_geometry_is_continuous():
    al = HorizontalAlignment([IP(0, 0, 0), IP(400, 0, 200, transition=60), IP(700, 300, 0)])
    assert al.is_valid and not al.issues
    g = al.geometry[1]
    assert [e.kind for e in al.elements] == ["tangent", "spiral", "curve", "spiral", "tangent"]
    assert np.isclose(g.spiral_angle, 60 / (2 * 200))
    assert np.isclose(g.curve_length, 200 * (np.radians(45) - 2 * g.spiral_angle))
    assert np.isclose(g.total_length, 2 * 60 + g.curve_length)
    # positions and directions match across every element boundary; the end is the last IP
    for a, b in zip(al.elements[:-1], al.elements[1:]):
        pa, pb = al.point_and_direction(a.end_chainage - 1e-7), al.point_and_direction(b.start_chainage + 1e-7)
        assert np.hypot(pa[0] - pb[0], pa[1] - pb[1]) < 1e-5 and abs(pa[2] - pb[2]) < 1e-6
    assert np.allclose(al.point_at(al.end_chainage), (700, 300), atol=1e-6)
    # the arc really has radius 200 about the computed centre; the spiral end has zero curvature
    cx, cy = g.centre
    for ch in np.linspace(g.sc_chainage, g.cs_chainage, 7):
        x, y = al.point_at(ch)
        assert np.isclose(np.hypot(x - cx, y - cy), 200, atol=1e-6)
    # spiral end offset y = Ls * theta / 3 to first order
    sx, sy = al.point_at(g.sc_chainage)
    assert np.isclose(sy, 60 * g.spiral_angle / 3, atol=0.01)
    # key points carry TS / SC / CS / ST and stations include them
    kinds = [k["kind"] for k in al.key_points()]
    assert kinds[:6] == ["IP", "IP", "TS", "SC", "MC", "CS"] and "ST" in kinds
    st = al.stations(20)
    assert any(np.isclose(st, g.sc_chainage)) and any(np.isclose(st, g.cs_chainage))
    # 5-column csv round trip
    back = HorizontalAlignment.from_aln_csv(al.to_aln_csv())
    assert back.ips[1].transition == 60 and np.isclose(back.length, al.length)
    # four-column files still work and produce a plain arc
    plain = HorizontalAlignment.from_aln_csv("2,0\n0,0,0,0\n1,400,0,200\n2,700,300,0\n")
    assert plain.ips[1].transition == 0 and [e.kind for e in plain.elements] == ["tangent", "curve", "tangent"]


def test_transition_too_long_falls_back_with_issue():
    al = HorizontalAlignment([IP(0, 0), IP(400, 0, 200, transition=400), IP(700, 300)])
    assert any(i.kind == "transition" for i in al.issues)
    assert al.geometry[1].transition == 0 and al.geometry[1].curve_length > 0


def test_horizontal_checks_and_curve_table():
    std = load_standard("nrs-2070")
    al = HorizontalAlignment([IP(0, 0), IP(400, 0, 90), IP(700, 300, 300, transition=40), IP(1200, 300)])
    checks = check_horizontal(al, std, CTX)
    by = {(c.id, c.where): c for c in checks}
    # 90 m at 60 km/h: below the absolute minimum 110 m (Table 9-1 with e_max) and the recommended 200 m
    r = [c for c in checks if c.id == "min_radius"]
    assert r[0].ok is False and r[0].limit == 200 and r[0].severity == "warning" and r[1].ok is True
    ra = [c for c in checks if c.id == "min_radius_absolute"]
    assert ra[0].ok is False and ra[0].limit == 110 and ra[0].severity == "error" and ra[1].ok is True
    assert any(c.id == "comfort_radius" and c.severity == "info" for c in checks)
    # R = 90 without a transition: required (shift with the table length is well above 0.25 m)
    assert any(c.id == "transition_required" and c.ok is False for c in checks)
    # R = 300 with Ls = 40: Table 9-2 asks for 90 m
    mt = [c for c in checks if c.id == "min_transition"]
    assert mt[0].ok is False and mt[0].limit == 90
    assert any(c.id == "clothoid_parameter" for c in checks) and any(c.id == "transition_vs_arc" for c in checks)
    sup = [c for c in checks if c.id == "superelevation"]
    assert sup[0].ok is False and sup[1].ok is True
    rows = curve_table(al, std, CTX)
    assert rows[1]["radius"] == 90 and rows[1]["superelevation_pct"] == 7.0 and rows[2]["ts"] is not None and rows[2]["sc"] is not None
    assert rows[1]["extra_widening"] == 0.9 and rows[2]["extra_widening"] == 0.6 and rows[1]["setback"] > 0   # Table 9-4 double lane, eq. 9-1
    assert by  # table dict built without collisions
    # a very large radius needs no transition; a hair-pin bend is checked against Table 9-3
    big = check_horizontal(HorizontalAlignment([IP(0, 0), IP(2000, 0, 1500), IP(4000, 1500)]), std, CTX)
    assert not any(c.id == "transition_required" for c in big)
    hp = HorizontalAlignment([IP(0, 0), IP(100, 0, 12), IP(0, 25)])
    hchecks = check_horizontal(hp, std, {**CTX, "design_speed": 20})
    assert any(c.id == "hairpin_radius" and c.ok is False and c.limit == 15 for c in hchecks)
    assert any(c.id == "hairpin_transition" and c.ok is False for c in hchecks)
    # with superelevation disabled the recommended radius becomes an error
    off = check_horizontal(al, std, {**CTX, "superelevation_enabled": False})
    assert [c for c in off if c.id == "min_radius"][0].severity == "error"


# ------------------------------------------------------------------ superelevation
def test_superelevation_rate_and_profile():
    assert np.isclose(required_rate(200, 60, 0.15), 60 * 60 / (127 * 200) - 0.15)
    e, ok = design_rate(200, 60, 0.15, 7.0, 2.5)
    assert ok and 2.5 <= e <= 7.0
    e2, ok2 = design_rate(50, 80, 0.14, 7.0, 2.5)
    assert not ok2 and e2 == 7.0
    # R = 150 at 60 km/h needs e = 3600/(127*150) - 0.15 = 3.9 %, above the 2.5 % camber
    al = HorizontalAlignment([IP(0, 0), IP(400, 0, 150, transition=60), IP(700, 300)])
    g = al.geometry[1]
    prof = build_profile(al, speed_kmh=60, side_friction=0.15, e_max_pct=7, camber_pct=2.5, relative_gradient_pct=0.6, rotated_width=3.5)
    c = prof.curves[0]
    assert np.isclose(c.e_pct, (3600 / (127 * 150) - 0.15) * 100)
    assert c.runoff == 60 and np.isclose(c.start, g.bc_chainage) and np.isclose(c.full_from, g.sc_chainage)
    # normal camber on the tangent, full e on the arc, outer side (right for a left turn) rises
    assert prof.slopes_at(10.0) == (-2.5, -2.5)
    lft, rgt = prof.slopes_at(g.mc_chainage)
    assert g.deflection > 0 and np.isclose(rgt, c.e_pct) and np.isclose(lft, -c.e_pct)
    mid_l, mid_r = prof.slopes_at((c.start + c.full_from) / 2)
    assert -2.5 < mid_r < c.e_pct and -c.e_pct < mid_l < -2.5
    # without spirals the runoff comes from the relative gradient and starts before BC
    plain = HorizontalAlignment([IP(0, 0), IP(400, 0, 200), IP(700, 300)])
    p2 = build_profile(plain, speed_kmh=60, side_friction=0.15, e_max_pct=7, camber_pct=2.5, relative_gradient_pct=0.6, rotated_width=3.5)
    c2 = p2.curves[0]
    assert np.isclose(c2.runoff, (c2.e_pct / 100) * 3.5 / 0.006) and c2.start < plain.geometry[1].bc_chainage


# ------------------------------------------------------------------ vertical
def test_vertical_alignment_geometry():
    va = VerticalAlignment([PVI(0, 100, 0), PVI(300, 112, 120), PVI(600, 106, 100), PVI(800, 106, 0)])
    assert np.allclose(va.grades, [4.0, -2.0, 0.0])
    c = va.curves[0]
    assert c.kind == "crest" and np.isclose(c.A, -6.0) and np.isclose(c.K, 20.0) and (c.bvc, c.evc) == (240, 360)
    # tangent points sit on the grades; the curve's external at the PVI is A L / 800
    assert np.isclose(va.elevation_at([240])[0], 100 + 0.04 * 240)
    assert np.isclose(va.elevation_at([360])[0], 112 - 0.02 * 60)
    assert np.isclose(va.elevation_at([300])[0] - 112, c.A * c.length / 800)
    # high point where the grade is zero: x = -g1 L / A = 80 m from BVC
    assert np.isclose(c.turning_chainage, 320) and np.isclose(va.grade_at([320])[0], 0.0, atol=1e-9)
    assert va.curves[1].kind == "sag" and va.is_valid
    d = va.densify(50)
    assert d[0]["chainage"] == 0 and d[-1]["chainage"] == 800 and any(abs(p["chainage"] - 320) < 1e-6 for p in d)
    # overlapping curves are reported
    bad = VerticalAlignment([PVI(0, 100), PVI(100, 104, 150), PVI(200, 100, 150), PVI(300, 100)])
    assert not bad.is_valid and any(i.kind == "overlap" for i in bad.issues)


def test_vertical_checks_and_fit_from_ground():
    std = load_standard("nrs-2070")
    va = VerticalAlignment([PVI(0, 100, 0), PVI(100, 106.5, 40), PVI(300, 108.5, 60), PVI(500, 90.5, 0)])  # 6.5 %, 1 %, -9 %
    checks = va.check(std, CTX)
    grads = [c for c in checks if c.id == "gradient"]
    # Table 10-1: 7 % at 60 km/h. 6.5 % passes, 1 % passes, 9 % fails
    assert grads[0].ok is True and grads[0].limit == 7.0 and grads[0].status == "verified"
    assert grads[1].ok is True
    assert grads[2].ok is False and grads[2].severity == "error"
    # Table 10-2: 6.5 % may run for 350 m (interpolated between 400 m at 6 % and 300 m at 7 %); 380 m is too long
    crit = [c for c in checks if c.id == "critical_length"]
    assert crit[0].ok is True and abs(crit[0].limit - 350) < 1e-6
    long_ = VerticalAlignment([PVI(0, 100, 0), PVI(380, 124.7, 40), PVI(600, 126, 0)]).check(std, CTX)
    assert [c for c in long_ if c.id == "critical_length"][0].ok is False
    # cl. 10.1.2 a: 1.5 % easing at 1500-1999 m altitude lowers the limit to 5.5 %
    eased = va.check(std, {**CTX, "gradient_easing": 1.5})
    assert [c for c in eased if c.id == "gradient"][0].ok is False and [c for c in eased if c.id == "gradient"][0].limit == 5.5
    ks = [c for c in checks if c.id.startswith("k_")]
    assert ks and all(c.limit is not None for c in ks)
    assert [c for c in checks if c.id == "k_crest"][0].limit == 94   # Table 10-3 at 60 km/h
    sag = VerticalAlignment([PVI(0, 100, 0), PVI(200, 96, 60), PVI(400, 100, 0)]).check(std, CTX)
    assert [c for c in sag if c.id == "k_sag"][0].limit == 42        # Table 10-4 at 60 km/h
    ground = [(c, 100 + 0.02 * c + 3 * np.sin(c / 100)) for c in np.arange(0, 1001, 10.0)]
    fit = VerticalAlignment.from_ground(ground, spacing=200)
    assert fit.is_valid and fit.start_chainage == 0 and fit.end_chainage == 1000 and len(fit.pvis) == 6
    z = fit.elevation_at([500])[0]
    assert abs(z - (100 + 10 + 3 * np.sin(5))) < 4  # follows the ground loosely


# ------------------------------------------------------------------ templates
def test_default_template_and_hinge():
    std = load_standard("nrs-2070")
    t = default_template(std, CTX)
    # class III (feeder), rolling: 2 x 3.5 m carriageway, 2.0 m shoulders with 0.5 % extra crossfall, fill 1:2 for a 6 m fill (Table 11-4)
    assert t["left"][0]["width"] == 3.5 and t["left"][1]["width"] == 2.0 and t["cut"]["ditch"]["depth"] == 0.5
    assert t["left"][1]["slope"] == -3.0 and t["fill"]["slope"] == 2.0 and t["cut"]["slope"] == 1.0 and t["name"].startswith("Class III")
    pts = hinge_polyline(t, "right", 100.0, None)
    assert pts[0] == (0.0, 100.0) and np.isclose(pts[-1][0], 5.5)
    iv = default_template(std, {**CTX, "road_class": "IV", "terrain": "steep", "material": "hard_rock"})
    assert iv["left"][0]["width"] == 3.75 / 2 and iv["left"][1]["width"] == 0.75 and iv["cut"]["slope"] == 0.02
    assert np.isclose(pts[1][1], 100 - 3.5 * 0.025) and pts[-1][1] < pts[1][1]
    sup = hinge_polyline(t, "right", 100.0, 5.0)  # superelevated: rises outward
    assert sup[-1][1] > 100.0
    assert validate_templates([t], [{"from": 0, "to": 100, "template_id": "default"}]) == []
    assert validate_templates([t], [{"from": 0, "to": 100, "template_id": "x"}])
    t2 = normalise_template({"id": "narrow", "left": [{"kind": "lane", "width": 2}], "right": [{"kind": "lane", "width": 2}], "cut": {"ditch": False}})
    assert t2["cut"]["ditch"] is None and t2["fill"]["slope"] == 1.5
    assert template_at([t, t2], [{"from": 50, "to": 80, "template_id": "narrow"}], 60)["id"] == "narrow"
    assert template_at([t, t2], [{"from": 50, "to": 80, "template_id": "narrow"}], 10)["id"] == "default"


# ------------------------------------------------------------------ corridor
def test_daylight_and_areas_on_sloping_ground():
    # ground falls outward at 10 %: z = 110 - 0.1 o ; hinge at o=4, z=110.5 (fill)
    go = np.arange(0, 61, 1.0)
    gz = 110 - 0.1 * go
    kind, pts, catch, height, benches, gzh = daylight_side(4.0, 110.5, go, gz, {"slope": 1.0}, {"slope": 1.5}, 60.0)
    assert kind == "fill" and benches == 0
    # analytic: 110.5 - (o-4)/1.5 = 110 - 0.1 o  ->  o = (110.5 + 4/1.5 - 110) / (1/1.5 - 0.1)
    o_exp = (110.5 + 4 / 1.5 - 110) / (1 / 1.5 - 0.1)
    assert np.isclose(catch, o_exp, atol=1e-6) and np.isclose(pts[-1][1], 110 - 0.1 * o_exp, atol=1e-6)
    # cut with ditch and benches against ground rising at 1:2 from o=4 (design 1:1 is steeper, so it catches)
    gz2 = np.where(go < 4, 100.0, 100 + (go - 4) * 0.5)
    kind, pts, catch, height, benches, _ = daylight_side(4.0, 99.0, go, gz2, {"slope": 1.0, "bench_height": 3.0, "bench_width": 1.0,
                                                                                 "ditch": {"foreslope": 1.0, "depth": 0.5, "bottom": 0.5}}, {"slope": 1.5}, 60.0)
    assert kind == "cut" and height < 0 and catch is not None
    # ditch: down 0.5 over 0.5 m, flat 0.5 m; then 3 m rise to a bench at o=8, bench to o=9, catch at o=11 z=103.5
    assert np.allclose(pts[1], (4.5, 98.5)) and np.allclose(pts[2], (5.0, 98.5))
    assert benches == 1 and np.allclose(pts[3], (8.0, 101.5)) and np.allclose(pts[4], (9.0, 101.5))
    assert np.isclose(catch, 11.0) and np.isclose(pts[-1][1], 103.5)
    # a 1:1 slope against 45 deg ground is parallel: benches never reach it -> no catch
    kind_p, *_ = daylight_side(4.0, 99.0, go, np.where(go < 4, 100.0, 100 + (go - 4) * 1.0), {"slope": 1.0, "bench_height": 3.0, "bench_width": 1.0, "ditch": None}, {"slope": 1.5}, 60.0)
    assert kind_p == "no_catch_cut"
    # no catch when the ground keeps rising faster than the slope and no benches are allowed
    kind, pts, catch, *_ = daylight_side(4.0, 99.0, go, 100 + (go - 4) * 2.0, {"slope": 1.0, "bench_height": 0, "ditch": None}, {"slope": 1.5}, 30.0)
    assert kind == "no_catch_cut" and catch is None and pts[-1][0] == 30.0
    # areas: design 1 m above ground over 10 m -> fill 10, then 1 m below over 10 m -> cut 10 with an exact crossing
    design = [(-10.0, 1.0), (0.0, 1.0), (0.0001, -1.0), (10.0, -1.0)]
    cut, fill = areas(design, np.array([-10.0, 10.0]), np.array([0.0, 0.0]))
    assert np.isclose(fill, 10.0, atol=1e-3) and np.isclose(cut, 10.0, atol=1e-3)
    tri = [(-5.0, 1.0), (5.0, -1.0)]
    cut, fill = areas(tri, np.array([-5.0, 5.0]), np.array([0.0, 0.0]))
    assert np.isclose(cut, 2.5) and np.isclose(fill, 2.5)


def _plane_tin(slope_x=0.1):
    g = np.arange(0, 201, 5.0)
    xx, yy = np.meshgrid(g, g)
    x, y = xx.ravel(), yy.ravel()
    return build_tin(PointSet.from_arrays(x, y, 100 + slope_x * x)).tin


def test_corridor_on_a_plane():
    tin = _plane_tin(0.1)  # ground rises 10 % towards +x
    al = HorizontalAlignment([IP(100, 10), IP(100, 190)])  # straight north along x = 100: ground 110 at the centreline
    va = VerticalAlignment([PVI(0, 110.0), PVI(180, 110.0)])
    std = load_standard("nrs-2070")
    tmpl = default_template(std, CTX)
    res = build_corridor(tin, al, va, [tmpl], [], None, interval=20.0)
    assert len(res.sections) == 10 and res.params["stations"] == 10
    s = res.sections[4]
    # travelling north, right is +x (ground higher -> cut with ditch), left is -x (fill)
    assert s.right.kind == "cut" and s.left.kind == "fill"
    assert s.right.catch_offset is not None and s.left.catch_offset is not None and s.left.catch_offset < -5.5 < 5.5 < s.right.catch_offset
    assert s.cut_area > 0 and s.fill_area > 0
    # analytic fill catch on the left (class III template: 3.5 m lane at 2.5 %, 2.0 m shoulder at 3.0 %, fill 1:2): ground z = 110 - 0.1 o
    hz = 110 - 3.5 * 0.025 - 2.0 * 0.030
    o_exp = (hz + 5.5 / 2.0 - 110) / (1 / 2.0 - 0.1)
    assert np.isclose(-s.left.catch_offset, o_exp, atol=0.02)
    # design polyline is monotonic left -> right and ends at the daylight points
    offs = [p[0] for p in s.design]
    assert all(b >= a - 1e-9 for a, b in zip(offs[:-1], offs[1:])) and np.isclose(offs[0], s.left.catch_offset) and np.isclose(offs[-1], s.right.catch_offset)
    # volumes: identical sections -> end-area volume = area x length; mass haul cumulative
    v = res.volumes[3]
    assert np.isclose(v["cut"], (res.sections[3].cut_area + res.sections[4].cut_area) / 2 * v["length"])
    assert res.totals["cut"] > 0 and res.totals["fill"] > 0 and len(res.mass_haul) == len(res.sections)
    # prismoidal option computes mid sections
    res2 = build_corridor(tin, al, va, [tmpl], [], None, interval=40.0, prismoidal=True)
    assert res2.volumes[0]["method"] == "prismoidal" and np.isclose(res2.totals["cut"], res.totals["cut"], rtol=0.05)
    d = s.as_dict()
    assert d["left"]["kind"] == "fill" and len(d["design"]) == len(s.design)


# ------------------------------------------------------------------ earthworks
def test_volumes_and_mass_haul():
    vols = earthworks.volumes([0, 20, 40], [10, 20, 0], [0, 0, 30])
    assert vols[0] == {"from": 0.0, "to": 20.0, "length": 20.0, "cut": 300.0, "fill": 0.0, "method": "end_area"}
    assert vols[1]["cut"] == 200.0 and vols[1]["fill"] == 300.0
    pv = earthworks.volumes([0, 20], [10, 20], [0, 0], mid_cut=[15], mid_fill=[0])
    assert np.isclose(pv[0]["cut"], 20 * (10 + 60 + 20) / 6) and pv[0]["method"] == "prismoidal"
    mh = earthworks.mass_haul(vols, cut_factor=0.9, fill_factor=1.0)
    assert [round(m["cumulative"], 3) for m in mh] == [0.0, 270.0, 150.0]
    t = earthworks.totals(vols, mh)
    assert t["cut"] == 500.0 and t["fill"] == 300.0 and np.isclose(t["net"], 150.0) and t["balance_points"] == []


# ------------------------------------------------------------------ structures
def test_wall_and_culvert_suggestions():
    secs = []
    for i, ch in enumerate(range(0, 200, 20)):
        fill_h = 7.5 if 60 <= ch <= 100 else 2.0
        secs.append({"chainage": float(ch), "left": {"kind": "fill", "height": fill_h}, "right": {"kind": "no_catch_cut" if ch == 160 else "cut", "height": -3.0}})
    walls = suggest_walls(secs, max_fill_height=6.0, max_cut_depth=8.0)
    kinds = sorted((w["kind"], w["side"]) for w in walls)
    assert kinds == [("breast_wall", "right"), ("retaining_wall", "left")]
    rw = next(w for w in walls if w["kind"] == "retaining_wall")
    assert rw["from"] == 60 and rw["to"] == 100 and rw["params"]["height"] == 7.5 and rw["source"] == "suggested"
    # the suggested type is the lightest catalogue entry that covers the height (7.5 m -> gabion, 3 m -> dry stone)
    assert rw["params"]["type"] == "gabion" and "gabion" in rw["note"].lower()
    assert next(w for w in walls if w["kind"] == "breast_wall")["params"]["type"] == "dry_stone"
    al = HorizontalAlignment([IP(0, 0), IP(500, 0)])
    stream = np.array([[200, -50], [200, 50]])
    sug = suggest_culverts(al, [stream], sag_points=[205.0, 400.0])
    assert [s["kind"] for s in sug] == ["culvert", "cross_drain"] and np.isclose(sug[0]["from"], 200, atol=2.0) and "sag" in sug[0]["note"]
    s = normalise_structure({"kind": "culvert", "from": 300, "params": {"span": 1.2}}, 7)
    assert s["id"] == 7 and s["side"] is None and s["params"]["span"] == 1.2 and s["params"]["type"] == "pipe" and s["params"]["cells"] == 1
    with pytest.raises(ValueError):
        normalise_structure({"kind": "bridge"}, 1)
    with pytest.raises(ValueError):
        normalise_structure({"kind": "retaining_wall", "params": {"type": "brick"}}, 1)   # not in the catalogue


def test_wall_types_geometry_and_quantities():
    from plm.design.structures import WALL_TYPES, bill_of_quantities, quantities, wall_section

    # every catalogue type gives a closed polygon whose area and base width grow with the height
    for t in WALL_TYPES:
        a4, a6 = wall_section(t, 4.0), wall_section(t, 6.0)
        assert len(a4["points"]) >= 4 and a4["area"] > 0 and a6["area"] > a4["area"] and a6["base_width"] >= a4["base_width"]
        assert max(y for _, y in a4["points"]) == 4.0 and a4["foundation"]["depth"] > 0
    # gravity trapezoid: area = (top + base) / 2 x H
    m = wall_section("stone_masonry", 4.0)
    assert m["base_width"] == 2.0 and np.isclose(m["area"], (0.6 + 2.0) / 2 * 4.0)
    # gabion steps back one 1 m box per course, so its back face is a staircase
    g = wall_section("gabion", 4.0)
    assert len(g["points"]) == 2 + 2 * 4 and g["base_width"] == 2.4
    # a cantilever uses far less concrete than a gravity wall of the same height
    assert wall_section("rcc_cantilever", 6.0)["area"] < wall_section("concrete_gravity", 6.0)["area"]
    q = quantities({"id": 1, "kind": "retaining_wall", "side": "left", "from": 0, "to": 30, "params": {"type": "gabion", "height": 4.0}})
    assert np.isclose(q["total"]["gabion"], g["area"] * 30) and q["total"]["excavation"] > 0 and q["sides"] == 1
    both = quantities({"id": 2, "kind": "breast_wall", "side": "both", "from": 0, "to": 10, "params": {"type": "stone_masonry", "height": 2.0}})
    assert both["sides"] == 2 and np.isclose(both["total"]["stone_masonry_cement"], wall_section("stone_masonry", 2.0)["area"] * 20)
    steel = quantities({"id": 3, "kind": "retaining_wall", "side": "left", "from": 0, "to": 10, "params": {"type": "rcc_cantilever", "height": 6.0}})
    assert steel["total"]["steel"] > 0 and np.isclose(steel["total"]["steel"], steel["total"]["concrete"] * 90.0)
    boq = bill_of_quantities([{"id": 1, "kind": "retaining_wall", "side": "left", "from": 0, "to": 30, "params": {"type": "gabion", "height": 4.0}}])
    assert boq["by_kind"]["retaining_wall"] == {"count": 1, "length": 30.0} and "gabion" in boq["materials"]


def test_drain_types_lining_rule_and_suggestions():
    from plm.design.structures import DRAIN_TYPES, drain_section, drain_type_for, lining_class, quantities, suggest_drains

    std = load_standard("nrs-2070")
    # NRS 2070 Table 13-3: unlined below 2 % in clayey soil (1 % in sandy), turf to 3 %, hard lining to 5 %, stepped above
    assert [lining_class(g, "clayey", std)[0] for g in (0.5, 2.5, 4.0, 6.0)] == [0, 1, 2, 3]
    assert [lining_class(g, "sandy", std)[0] for g in (0.5, 1.5, 4.0)] == [0, 1, 2]
    assert drain_type_for(0.5, "clayey", std)[0] == "earth_trapezoid" and drain_type_for(6.0, "clayey", std)[0] == "stepped_masonry"
    # every drain type gives a channel with a flow area, a wetted perimeter and an excavation area
    for t in DRAIN_TYPES:
        d = drain_section(t, 0.6, 0.5)
        assert d["flow_area"] > 0 and d["perimeter"] > 0 and d["excavation"] >= d["flow_area"] and len(d["points"]) >= 3
    trap = drain_section("earth_trapezoid", 0.6, 0.5)
    assert np.isclose(trap["flow_area"], (0.6 + 1.0 * 0.5) * 0.5) and np.isclose(trap["top_width"], 0.6 + 2 * 0.5)
    lined = quantities({"id": 1, "kind": "side_drain", "side": "both", "from": 0, "to": 100, "params": {"type": "masonry_trapezoid", "width": 0.6, "depth": 0.5}})
    assert lined["sides"] == 2 and lined["total"]["lining"] > 0 and lined["total"]["stone_masonry_cement"] > 0
    # suggestions: a side drain along the cut, a catch drain behind the deep cut, a toe drain on the low fill
    secs = []
    for ch in range(0, 400, 20):
        if ch < 150:
            side = {"kind": "cut", "height": -4.0}
        elif ch < 250:
            side = {"kind": "fill", "height": 0.5}
        else:
            side = {"kind": "fill", "height": 4.0}
        secs.append({"chainage": float(ch), "left": side, "right": {"kind": "level", "height": 0.0}})
    drains = suggest_drains(secs, grade_at=lambda ch: 4.0, soil="clayey", std=std)
    kinds = {d["kind"] for d in drains}
    assert {"side_drain", "catch_drain", "toe_drain"} <= kinds
    sd = next(d for d in drains if d["kind"] == "side_drain")
    assert sd["side"] == "left" and sd["from"] == 0 and sd["to"] == 140 and sd["params"]["type"] == "masonry_trapezoid"   # 4 % -> hard lining
    assert next(d for d in drains if d["kind"] == "catch_drain")["params"]["offset"] == 5.0
    flat = suggest_drains(secs, grade_at=lambda ch: 0.5, soil="clayey", std=std)
    assert next(d for d in flat if d["kind"] == "side_drain")["params"]["type"] == "earth_trapezoid"


def test_structure_checks():
    from plm.design.structures import check_structures

    std = load_standard("nrs-2070")
    items = [
        {"id": 1, "kind": "culvert", "side": None, "from": 100, "to": 100, "params": {"type": "slab", "span": 4.0, "cells": 2}},
        {"id": 2, "kind": "side_drain", "side": "left", "from": 0, "to": 200, "params": {"type": "earth_trapezoid", "width": 0.6, "depth": 0.5, "gradient": 4.5}},
        {"id": 3, "kind": "catch_drain", "side": "left", "from": 0, "to": 100, "params": {"type": "masonry_trapezoid", "width": 0.6, "depth": 0.5, "offset": 2.0, "gradient": 4.0}},
        {"id": 4, "kind": "retaining_wall", "side": "right", "from": 0, "to": 40, "params": {"type": "dry_stone", "height": 5.0}},
        {"id": 5, "kind": "cross_drain", "side": None, "from": 900, "to": 900, "params": {"type": "pipe", "span": 0.6}},
    ]
    by: dict[str, list] = {}
    for c in check_structures(items, std, CTX, grade_at=None, soil="clayey"):
        by.setdefault(c.id, []).append(c)
    # 2 x 4 m = 8 m total span is a bridge (cl. 17.1 a)
    assert by["culvert_span"][0].ok is False and by["culvert_span"][0].limit == 6.0
    # an unlined drain at 4.5 % needs a hard lining, the masonry catch drain at 4 % is fine (Table 13-3)
    assert by["drain_lining"][0].ok is False and "Table 13-3" in by["drain_lining"][0].source
    assert by["drain_lining"][1].ok is True
    # the catch drain sits 2 m behind the cut, less than the 5 m of cl. 13.8 d
    assert by["catch_drain_offset"][0].ok is False and by["catch_drain_offset"][0].limit == 5.0
    # dry stone masonry is not used for a 5 m wall
    assert by["wall_height"][0].ok is False and by["wall_height"][0].status == "default"
    # 800 m between cross-drainage points exceeds the 500 m outlet spacing of cl. 13.8 i
    assert by["outlet_spacing"][0].ok is False and by["outlet_spacing"][0].limit == 500.0
    good = [{"id": 1, "kind": "side_drain", "side": "left", "from": 0, "to": 100, "params": {"type": "stepped_masonry", "width": 0.6, "depth": 0.5, "gradient": 6.0}}]
    assert all(c.ok is not False for c in check_structures(good, std, CTX, None, "clayey"))


def test_stretch_and_trim_pvis():
    from plm.design.vertical import stretch_pvis, trim_pvis

    pvis = [{"chainage": 0, "elevation": 100, "length": 0, "ground_z": 99}, {"chainage": 200, "elevation": 104, "length": 60, "ground_z": 105},
            {"chainage": 400, "elevation": 108, "length": 0, "ground_z": 108}]
    # longer road: PVIs keep their fraction of the length and their depth below / above the ground
    out = stretch_pvis(pvis, (0, 400), (0, 500), ground_new=lambda c: 200 + 0.01 * c)
    assert [q["chainage"] for q in out] == [0, 250, 500]
    assert abs(out[1]["elevation"] - (202.5 - 1.0)) < 1e-6 and out[1]["length"] == 60
    # shorter road without ground: elevations kept, curve lengths shrink with the road
    shorter = stretch_pvis(pvis, (0, 400), (0, 200), None)
    assert [q["chainage"] for q in shorter] == [0, 100, 200] and shorter[1]["length"] == 30 and shorter[1]["elevation"] == 104
    # trim: ends on the new range limits at the level of the extended grade line
    trimmed = trim_pvis([dict(q) for q in pvis], (50, 300))
    assert [q["chainage"] for q in trimmed] == [50, 200, 300] and abs(trimmed[0]["elevation"] - 101) < 1e-6
    extended = trim_pvis([dict(q) for q in pvis], (-100, 450))
    assert extended[0]["chainage"] == -100 and abs(extended[0]["elevation"] - 98) < 1e-6 and abs(extended[-1]["elevation"] - 109) < 1e-6
    assert [q["label"] for q in extended] == ["0", "1", "2", "3", "4"] and all("ground_z" not in q for q in extended)
    assert trim_pvis([pvis[0]], (0, 10)) == [pvis[0]]
