import numpy as np
import pytest

from plm.engine import build_tin
from plm.engine.alignment import IP, HorizontalAlignment, format_chainage, parse_chainage
from plm.engine.sections import (
    cross_sections_to_csv,
    generate_cross_sections,
    generate_profile,
    profile_to_csv,
)

from conftest import plane_z


def test_chainage_format():
    assert format_chainage(0) == "0+000.00"
    assert format_chainage(1234.567) == "1+234.57"
    assert format_chainage(999.999) == "1+000.00"
    assert format_chainage(20, 0) == "0+020"
    assert parse_chainage("1+234.5") == 1234.5
    assert parse_chainage("  350 ") == 350.0


@pytest.fixture
def simple_alignment():
    # two tangents at right angle with a 50 m radius curve at the corner
    return HorizontalAlignment([IP(0, 0, 0, "A"), IP(100, 0, 50, "B"), IP(100, 100, 0, "C")], start_chainage=1000)


def test_curve_geometry(simple_alignment):
    al = simple_alignment
    g = al.geometry[1]
    assert np.isclose(g.deflection, np.pi / 2)          # left turn
    assert np.isclose(g.tangent_length, 50.0)          # T = R tan(45)
    assert np.isclose(g.curve_length, np.pi * 50 / 2)  # quarter circle
    assert np.isclose(g.bc_chainage, 1050.0)
    assert np.isclose(g.ec_chainage, 1050.0 + np.pi * 25)
    assert np.allclose(g.bc, (50, 0)) and np.allclose(g.ec, (100, 50))
    assert np.allclose(g.centre, (50, 50))
    assert al.is_valid
    total = 50 + np.pi * 25 + 50
    assert np.isclose(al.length, total)
    # stationing: on the first tangent, mid-curve and second tangent
    assert np.allclose(al.point_at(1025), (25, 0))
    mx, my = al.point_at(g.mc_chainage)
    assert np.allclose((mx, my), (50 + 50 * np.cos(-np.pi / 4), 50 + 50 * np.sin(-np.pi / 4)))
    ex, ey, d = al.point_and_direction(al.end_chainage)
    assert np.allclose((ex, ey), (100, 100)) and np.isclose(d, np.pi / 2)
    # offsets: right of travel on the first tangent (heading +X) is -Y
    assert np.allclose(al.offset_point(1025, 10), (25, -10))
    assert np.allclose(al.offset_point(1025, -10), (25, 10))


def test_stations_and_marks(simple_alignment):
    al = simple_alignment
    st = al.stations(20)
    assert st[0] == 1000 and np.isclose(st[-1], al.end_chainage)
    assert 1020 in st and 1040 in st
    g = al.geometry[1]
    assert any(np.isclose(st, g.bc_chainage)) and any(np.isclose(st, g.mc_chainage))
    assert np.all(np.diff(st) > 0)
    marks = al.chainage_marks(50)
    assert marks[0]["label"] == "1+000.00"
    dense, ch = al.densify(1.0)
    assert np.all(np.diff(ch) > 0) and np.allclose(dense[0], (0, 0)) and np.allclose(dense[-1], (100, 100))
    pl = al.polyline_with_bulges()
    # start, BC (with bulge), EC, end
    assert len(pl) == 4 and np.isclose(pl[1][2], np.tan(np.pi / 8)) and pl[2][2] == 0.0


def test_invalid_radius_reported():
    al = HorizontalAlignment([IP(0, 0), IP(30, 0, 100), IP(30, 30)])
    assert not al.is_valid
    assert any(i.kind == "overlap" for i in al.issues)
    al2 = HorizontalAlignment([IP(0, 0), IP(100, 0, 3), IP(100, 100)])
    assert any(i.kind == "min_radius" for i in al2.issues)


def test_aln_csv_roundtrip(simple_alignment):
    txt = simple_alignment.to_aln_csv()
    back = HorizontalAlignment.from_aln_csv(txt)
    assert back.start_chainage == 1000 and len(back.ips) == 3
    assert np.isclose(back.length, simple_alignment.length)


def test_from_polyline_with_bulge(simple_alignment):
    pl = simple_alignment.polyline_with_bulges()
    coords = np.array([[x, y] for x, y, _ in pl])
    bulges = [b for _, _, b in pl][:-1]
    back = HorizontalAlignment.from_polyline(coords, bulges, start_chainage=1000)
    assert len(back.ips) == 3
    assert np.allclose([(p.x, p.y) for p in back.ips], [(0, 0), (100, 0), (100, 100)], atol=1e-6)
    assert np.isclose(back.ips[1].radius, 50.0)


def test_profile_and_sections_on_plane(plane_random):
    tin = build_tin(plane_random).tin
    al = HorizontalAlignment([IP(10, 10), IP(100, 20, 60), IP(190, 90)], start_chainage=0)
    prof = generate_profile(tin, al, interval=10)
    ch = np.array([p.chainage for p in prof])
    assert np.all(np.diff(ch) >= 0)
    z = np.array([p.z for p in prof])
    xy = np.array([[p.x, p.y] for p in prof])
    assert np.allclose(z, plane_z(xy[:, 0], xy[:, 1]), atol=1e-6)
    assert any(p.source == "station" for p in prof) and any(p.source == "edge" for p in prof)
    assert ch[0] == 0 and np.isclose(ch[-1], al.end_chainage)
    csv = profile_to_csv(prof)
    assert csv.startswith("Chainage,RL,Remarks\n")

    xs = generate_cross_sections(tin, al, interval=20, left=15, right=10)
    assert len(xs) == len(al.stations(20))
    s = xs[1]
    assert np.isclose(s.offset[0], -15) and np.isclose(s.offset[-1], 10)
    assert "centre" in s.source and np.isclose(s.offset[s.source.index("centre")], 0.0)
    assert np.allclose(s.z, plane_z(s.xy[:, 0], s.xy[:, 1]), atol=1e-6)
    txt = cross_sections_to_csv(xs)
    lines = txt.splitlines()
    assert lines[0] == "Chainage,PD,RL,Remarks"
    assert lines[1].startswith("0.000,-15.00,")
    assert lines[2].startswith(",")  # chainage only on the first row of a section


def test_section_outside_tin_is_nan(plane_grid):
    tin = build_tin(plane_grid).tin
    al = HorizontalAlignment([IP(-50, 50), IP(150, 50)])
    prof = generate_profile(tin, al, interval=25, include_edge_crossings=False)
    z = np.array([p.z for p in prof])
    assert np.isnan(z[0]) and np.isnan(z[-1]) and not np.isnan(z[len(z) // 2])
    csv = profile_to_csv(prof)
    assert csv.count("\n") == 1 + int((~np.isnan(z)).sum())
