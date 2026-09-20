"""DXF writers: drawing sheets (millimetres, one paper-space layout per sheet) and the model-space
design drawing in project coordinates (metres)."""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np

from ...engine.alignment import format_chainage
from .frame import DrawingInputs
from .model import LAYERS, Circle, Hatch, Polyline, Sheet, Text, layer_info

_LW = {0.13: 13, 0.18: 18, 0.25: 25, 0.35: 35, 0.5: 50, 0.7: 70}
_ROW = {"plan": 0, "profile": 1, "sections": 2}


def _lineweight(mm: float) -> int:
    return min(_LW.items(), key=lambda kv: abs(kv[0] - mm))[1]


def _align(align: str, valign: str):
    from ezdxf.enums import TextEntityAlignment as A

    table = {
        ("left", "baseline"): A.LEFT, ("center", "baseline"): A.CENTER, ("right", "baseline"): A.RIGHT,
        ("left", "middle"): A.MIDDLE_LEFT, ("center", "middle"): A.MIDDLE_CENTER, ("right", "middle"): A.MIDDLE_RIGHT,
        ("left", "top"): A.TOP_LEFT, ("center", "top"): A.TOP_CENTER, ("right", "top"): A.TOP_RIGHT,
        ("left", "bottom"): A.BOTTOM_LEFT, ("center", "bottom"): A.BOTTOM_CENTER, ("right", "bottom"): A.BOTTOM_RIGHT,
    }
    return table.get((align, valign), A.LEFT)


def _new_doc(units: int):
    import ezdxf

    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = units
    doc.header["$LTSCALE"] = 1.0 if units == 4 else 5.0
    for name, font in (("ROMANS", "romans.shx"), ("ROMANT", "romant.shx")):
        if name not in doc.styles:
            doc.styles.add(name, font=font)
    for name, (aci, _, lw) in LAYERS.items():
        if name not in doc.layers:
            doc.layers.add(name, color=aci, lineweight=_lineweight(lw))
    return doc


def _emit(msp, e, ox: float, oy: float) -> None:
    if isinstance(e, Polyline):
        attribs = {"layer": e.layer}
        if e.dashed:
            attribs["linetype"] = "DASHED2"
        if e.width:
            attribs["lineweight"] = _lineweight(e.width)
        msp.add_lwpolyline([(x + ox, y + oy) for x, y in e.pts], close=e.closed, dxfattribs=attribs)
    elif isinstance(e, Text):
        msp.add_text(e.text, height=e.height, rotation=e.rotation, dxfattribs={"layer": e.layer, "style": "ROMANT" if e.bold else "ROMANS"}).set_placement(
            (e.x + ox, e.y + oy), align=_align(e.align, e.valign))
    elif isinstance(e, Circle):
        msp.add_circle((e.x + ox, e.y + oy), e.r, dxfattribs={"layer": e.layer})
    elif isinstance(e, Hatch):
        aci = layer_info(e.layer)[0]
        h = msp.add_hatch(color=aci, dxfattribs={"layer": e.layer})
        h.paths.add_polyline_path([(x + ox, y + oy) for x, y in e.pts], is_closed=True)
        if e.pattern:
            try:
                h.set_pattern_fill(e.pattern.upper(), color=aci, scale=0.25)
            except Exception:  # noqa: BLE001 - unknown pattern name: keep the solid fill
                h.set_solid_fill(color=aci)
        else:
            h.set_solid_fill(color=aci)


def sheets_to_dxf(sheets: Sequence[Sheet], path: str | Path, *, gap: float = 60.0) -> Path:
    """All sheets side by side in model space (mm; one row per drawing kind) plus a paper-space
    layout per sheet that plots the sheet at 1:1, so AutoCAD prints them directly."""
    doc = _new_doc(4)
    msp = doc.modelspace()
    codes = {"plan": "P", "profile": "L", "sections": "X"}
    for s in sheets:
        W, H = s.paper
        ox = s.index * (W + gap)
        oy = -_ROW.get(s.kind, 3) * (H + gap)
        for e in s.entities:
            _emit(msp, e, ox, oy)
        try:
            lay = doc.layouts.new(f"{codes.get(s.kind, s.kind[:1].upper())}-{s.index + 1:02d}")
            lay.page_setup(size=(W, H), margins=(0, 0, 0, 0), units="mm")
            lay.add_viewport(center=(W / 2, H / 2), size=(W, H), view_center_point=(ox + W / 2, oy + H / 2), view_height=H)
        except Exception:  # noqa: BLE001 - the model-space copy is the deliverable; layouts are a convenience
            pass
    p = Path(path)
    doc.saveas(str(p))
    return p


def model_dxf(inp: DrawingInputs, path: str | Path, *, chainage_interval: float = 20.0, text_height: float = 2.5) -> Path:
    """The design in project coordinates: centreline (arcs as bulges), IPs and tangents, chainage
    ticks, curve key points, 3-D design centreline, formation and daylight lines, cross-section
    lines and 3-D section polylines, structures."""
    al = inp.al
    doc = _new_doc(6)
    msp = doc.modelspace()
    th = text_height

    def layer(name: str, color: int) -> str:
        if name not in doc.layers:
            doc.layers.add(name, color=color)
        return name

    from ezdxf.enums import TextEntityAlignment as A

    def text(lay: str, s: str, x: float, y: float, h: float = th, rot: float = 0.0, align=A.LEFT) -> None:
        msp.add_text(s, height=h, rotation=rot, dxfattribs={"layer": lay, "style": "ROMANS"}).set_placement((x, y), align=align)

    # centreline with bulges, tangents, IPs, key points, chainages (legacy layer names)
    pts = [(x, y, 0.0, 0.0, b) for x, y, b in al.polyline_with_bulges()]
    msp.add_lwpolyline(pts, format="xyseb", dxfattribs={"layer": layer("H_ALIGN", 1)})
    for g in al.geometry:
        text(layer("IPN", 7), f"IP-{g.label}", g.x, g.y + th * 1.2)
        if g.curve_length > 0 and g.valid:
            msp.add_lwpolyline([g.bc, (g.x, g.y), g.ec], dxfattribs={"layer": layer("IPLN", 4), "linetype": "DASHED2"})
            text(layer("IPN", 7), f"R={g.radius:g}" + (f" Ls={g.transition:g}" if g.transition else ""), g.x, g.y - th * 0.4, th * 0.8, 0, A.TOP_LEFT)
    for m in al.chainage_marks(chainage_interval, tick_length=th * 2):
        msp.add_line((m["x"], m["y"]), (m["x2"], m["y2"]), dxfattribs={"layer": layer("Chainage", 7)})
        text("Chainage", m["label"], m["x2"], m["y2"], th * 0.8, m["angle_deg"])
    for kp in al.key_points():
        if kp["kind"] == "IP":
            continue
        x, y, d = al.point_and_direction(kp["chainage"])
        x2, y2 = al.offset_point(kp["chainage"], -th * 2)
        msp.add_line((x, y), (x2, y2), dxfattribs={"layer": layer("HORZ_CURVE", 1)})
        text("HORZ_CURVE", f"{kp['kind']} {format_chainage(kp['chainage'])}", x2, y2, th * 0.8, float(np.degrees(d)) - 90.0 + 180.0, A.RIGHT)

    # 3-D design centreline
    if inp.va is not None:
        chs = al.stations(max(chainage_interval / 4, 1.0), include_curve_points=True)
        chs = chs[(chs >= inp.va.start_chainage - 1e-6) & (chs <= inp.va.end_chainage + 1e-6)]
        if len(chs) >= 2:
            z = inp.va.elevation_at(chs)
            xyz = [(*al.point_at(float(c)), float(zz)) for c, zz in zip(chs, z)]
            msp.add_polyline3d(xyz, dxfattribs={"layer": layer("DESIGN_CL_3D", 1)})

    # corridor lines
    secs = sorted(inp.sections, key=lambda s: s["chainage"])
    if secs:
        def world(sec: dict, o: float, z: float) -> tuple[float, float, float]:
            d = sec["direction"]
            return sec["x"] + o * np.sin(d), sec["y"] - o * np.cos(d), z

        for side, sgn in (("left", "L"), ("right", "R")):
            hinge = [world(s, s[side]["hinge_offset"], s[side]["hinge_z"]) for s in secs]
            msp.add_polyline3d(hinge, dxfattribs={"layer": layer(f"FORMATION_{sgn}", 8)})
            catch = []
            for s in secs:
                co = s[side].get("catch_offset")
                if co is None or not s.get("design"):
                    continue
                zc = float(np.interp(co, [p[0] for p in s["design"]], [p[1] for p in s["design"]]))
                catch.append(world(s, co, zc))
            if len(catch) >= 2:
                msp.add_polyline3d(catch, dxfattribs={"layer": layer(f"DAYLIGHT_{sgn}", 3)})
        for s in secs:
            if not s.get("design"):
                continue
            d = s["design"]
            msp.add_polyline3d([world(s, o, z) for o, z in d], dxfattribs={"layer": layer("DESIGN_SECTIONS_3D", 5)})
            a, b = world(s, d[0][0], 0.0), world(s, d[-1][0], 0.0)
            msp.add_line((a[0], a[1]), (b[0], b[1]), dxfattribs={"layer": layer("Cross_Section", 7)})
            text("Cross_Section", format_chainage(s["chainage"]), b[0], b[1], th * 0.75, float(np.degrees(s["direction"])) - 90.0)

    # structures: one layer per kind, labelled with the catalogue type
    from ..structures import STRUCTURE_KINDS, type_spec

    for s in inp.structures:
        c0, c1 = float(s["from"]), float(s["to"])
        kind = s["kind"]
        spec = STRUCTURE_KINDS.get(kind, {})
        group = spec.get("group", "wall")
        tspec = type_spec(kind, str((s.get("params") or {}).get("type") or ""))
        lay = layer(f"STRUCT_{kind.upper()}", 6 if group == "wall" else (4 if group == "drain" else 5))
        label = f"{spec.get('label', kind).split(' (')[0].upper()} - {tspec.get('label', '')}"
        if group == "cross":
            chm = min(max((c0 + c1) / 2, al.start_chainage), al.end_chainage)
            p1, p2 = al.offset_point(chm, -6.0), al.offset_point(chm, 6.0)
            msp.add_line(p1, p2, dxfattribs={"layer": lay, "lineweight": 50})
            text(lay, f"{label} {s['params'].get('cells', 1)}x{s['params'].get('span', '')}m {format_chainage(chm)}", p2[0], p2[1], th * 0.8)
        else:
            for side in (("left", "right") if (s.get("side") or "left") == "both" else ((s.get("side") or "left"),)):
                sgn = -1.0 if side == "left" else 1.0
                off = 4.5 + (float((s.get("params") or {}).get("offset") or 0.0) if kind == "catch_drain" else 0.0)
                chs = np.linspace(max(c0, al.start_chainage), min(c1, al.end_chainage), max(3, int((c1 - c0) / 5) + 2))
                pts2 = [al.offset_point(float(c), sgn * off) for c in chs]
                if len(pts2) >= 2:
                    msp.add_lwpolyline(pts2, dxfattribs={"layer": lay, "lineweight": 70 if group == "wall" else 35})
                    h = (s.get("params") or {}).get("height")
                    text(lay, f"{label}{f' H={h:g}m' if h else ''} {format_chainage(c0)}-{format_chainage(c1)}", pts2[0][0], pts2[0][1], th * 0.8)
    p = Path(path)
    doc.saveas(str(p))
    return p
