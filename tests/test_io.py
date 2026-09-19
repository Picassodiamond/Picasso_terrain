import json

import numpy as np

from plm.engine import build_tin, contour_tin, contour_labels, PointSet
from plm.engine.io import (
    detect_delimiter,
    read_dxf,
    read_geojson,
    read_points_csv,
    write_dxf,
    write_points_csv,
)
from plm.engine.io.dxf_export import DxfStyle
from plm.engine.io.geojson_io import points_to_geojson


def test_csv_legacy_field_counts():
    assert len(read_points_csv("1 2\n3 4\n")) == 2
    ps = read_points_csv("10.5 20.5 100.25\n11 21 101\n")
    assert np.allclose(ps.z, [100.25, 101])
    ps = read_points_csv("7,10,20,100\n8,11,21,101\n")
    assert ps.ids == ["7", "8"] and np.allclose(ps.x, [10, 11])
    ps = read_points_csv("7\t10\t20\t100\tHOUSE corner\n8\t11\t21\t101\tRIVER\n")
    assert ps.remarks == ["HOUSE corner", "RIVER"]
    # comma + remark containing extra fields joins them (legacy)
    ps = read_points_csv("1,10,20,100,BL,left,edge\n")
    assert ps.remarks == ["BL left edge"]


def test_csv_header_mapping():
    txt = "PtNo,Easting,Northing,Elevation,Remark\n1,500.0,600.0,1400.5,TREE\n2,501,601,1401,\n"
    ps = read_points_csv(txt)
    assert len(ps) == 2 and ps.ids == ["1", "2"]
    assert np.allclose(ps.z, [1400.5, 1401]) and ps.remarks == ["TREE", ""]
    # x,y,z-only header
    ps2 = read_points_csv("x;y;z\n1;2;3\n")
    assert np.allclose(ps2.xyz, [[1, 2, 3]])
    # explicit mapping overrides
    ps3 = read_points_csv("a,b,c\n5,6,7\n", mapping={"x": 2, "y": 1, "z": 0}, has_header=True)
    assert np.allclose(ps3.xyz, [[7, 6, 5]])


def test_csv_bad_rows_and_delimiter_detect():
    ps = read_points_csv("1,2,3\nabc\n4,5,6\n")
    assert len(ps) == 2
    assert detect_delimiter("1,2,3\n4,5,6") == ","
    assert detect_delimiter("1\t2\n3\t4") == "\t"
    assert detect_delimiter("1 2 3\n4 5 6") is None


def test_csv_roundtrip(tmp_path):
    ps = PointSet.from_arrays([1.23456, 2], [3, 4], [5, 6], ids=["A", "B"], remarks=["r1", ""])
    p = tmp_path / "pts.csv"
    write_points_csv(ps, p)
    back = read_points_csv(p)
    assert back.ids == ["A", "B"] and back.remarks == ["r1", ""]
    assert np.allclose(back.xyz, np.round(ps.xyz, 3))
    write_points_csv(ps, tmp_path / "pts.xyz", fmt="x,y,z", header=False, delimiter=" ")
    xyz = read_points_csv(tmp_path / "pts.xyz")
    assert len(xyz) == 2 and xyz.ids == ["", ""]


def test_geojson_roundtrip(tmp_path):
    ps = PointSet.from_arrays([1, 2], [3, 4], [5, 6], ids=["1", "2"], remarks=["a", "b"])
    gj = points_to_geojson(ps, crs="EPSG:32645")
    gj["features"].append(
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0, 10], [1, 1, 11]]},
         "properties": {"kind": "feature", "layer": "Features"}}
    )
    gj["features"].append(
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [5, 0], [5, 5], [0, 5], [0, 0]]]},
         "properties": {"layer": "Boundary"}}
    )
    p = tmp_path / "in.geojson"
    p.write_text(json.dumps(gj))
    d = read_geojson(p)
    assert len(d.points) == 2 and d.points.remarks == ["a", "b"]
    assert len(d.lines_of_kind("feature")) == 1 and d.lines[0]["coords"].shape == (2, 3)
    assert len(d.polygons_of_kind("boundary")) == 1


def test_dxf_export_and_reimport(tmp_path, plane_grid):
    res = build_tin(plane_grid)
    lines = contour_tin(res.tin, 1.0, 5)
    labels = contour_labels(lines, every_m=30)
    fl = [np.array([[10, 10, 101.5], [50, 50, 107.5]])]
    out = tmp_path / "out.dxf"
    write_dxf(out, points=plane_grid, tin=res.tin, contours=lines, contour_labels=labels, feature_lines=fl,
              style=DxfStyle(arc_smoothing=False))
    import ezdxf

    doc = ezdxf.readfile(str(out))
    msp = doc.modelspace()
    assert len(msp.query("3DFACE")) == res.tin.n_triangles
    assert len(msp.query("INSERT[name=='POINTS']")) == len(plane_grid)
    lw = msp.query("LWPOLYLINE")
    assert len(lw) == len(lines)
    major = [e for e in lw if e.dxf.layer == "Index_Contour"]
    assert len(major) == sum(1 for c in lines if c.is_major)
    assert all(abs(e.dxf.elevation - round(e.dxf.elevation)) < 1e-9 for e in lw)
    assert len(msp.query("TEXT")) == len(labels)

    back = read_dxf(out, point_layers=["Points-Blk"])
    assert len(back.points) == len(plane_grid)
    assert back.points.ids[0] == "1"
    assert np.allclose(sorted(back.points.z), sorted(plane_grid.z), atol=1e-3)
    contours_back = back.lines_of_kind("contour")
    assert len(contours_back) == len(lines)
    feats = back.lines_of_kind("feature")
    assert any(np.allclose(f[:, :2], fl[0][:, :2]) for f in feats)


def test_dxf_arc_smoothing_writes_bulges(tmp_path, cone):
    res = build_tin(cone)
    lines = contour_tin(res.tin, 1.0, levels=[145.0], min_spacing=6.0)
    out = tmp_path / "arc.dxf"
    write_dxf(out, contours=lines, style=DxfStyle(arc_smoothing=True))
    import ezdxf

    msp = ezdxf.readfile(str(out)).modelspace()
    pl = msp.query("LWPOLYLINE")[0]
    bulges = [b for (_, _, _, _, b) in pl.get_points("xyseb")]
    assert any(abs(b) > 0 for b in bulges)
    assert pl.closed
    # re-import densifies the arcs back into a ring of roughly the right radius
    back = read_dxf(out)
    ring = back.lines_of_kind("contour")[0]
    r = np.hypot(ring[:, 0], ring[:, 1])
    assert np.allclose(r, 50.0, rtol=0.05)
