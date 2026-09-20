"""Plan sheets: the alignment strip in project coordinates, rotated along the road (or north up),
split into as many sheets as the paper and scale need, with contours, IPs and tangents, chainage
ticks, curve key points, corridor daylight lines, structures, match lines and a curve data table.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ...engine.alignment import HorizontalAlignment, format_chainage
from ..structures import STRUCTURE_KINDS, type_spec
from .frame import DrawingInputs, SheetSettings, draw_frame, drawing_number, north_arrow, scale_bar
from .model import Sheet, clip_polyline, readable

KP_LABEL = {"BC": "BC", "EC": "EC", "TS": "TS", "SC": "SC", "CS": "CS", "ST": "ST", "MC": "MC"}


@dataclass
class PlanFrame:
    """World (m) -> sheet (mm): rotate by -theta about p0, centre the window, scale."""
    p0: np.ndarray
    theta: float
    rc: np.ndarray
    k: float
    ac: tuple[float, float]
    area: tuple[float, float, float, float]
    s0: float
    s1: float

    def rot(self, xy: np.ndarray) -> np.ndarray:
        d = np.asarray(xy, float).reshape(-1, 2) - self.p0
        c, s = np.cos(-self.theta), np.sin(-self.theta)
        return np.column_stack([c * d[:, 0] - s * d[:, 1], s * d[:, 0] + c * d[:, 1]])

    def to_sheet(self, xy: np.ndarray) -> np.ndarray:
        r = self.rot(xy) - self.rc
        return np.column_stack([self.ac[0] + r[:, 0] * self.k, self.ac[1] + r[:, 1] * self.k])

    def pt(self, x: float, y: float) -> tuple[float, float]:
        s = self.to_sheet(np.array([[x, y]]))[0]
        return float(s[0]), float(s[1])

    def angle_deg(self, world_rad: float) -> float:
        return float(np.degrees(world_rad - self.theta))

    def inside(self, x: float, y: float, pad: float = 0.0) -> bool:
        x0, y0, x1, y1 = self.area
        return x0 - pad <= x <= x1 + pad and y0 - pad <= y <= y1 + pad


def _bearing(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.arctan2(b[1] - a[1], b[0] - a[0]))


def plan_windows(al: HorizontalAlignment, st: SheetSettings) -> list[PlanFrame]:
    """Greedy split of the alignment into sheet windows that fit the drawing area at the plan scale."""
    xy, ch = al.densify(max_segment=5.0)
    xy = np.asarray(xy, float)
    ch = np.asarray(ch, float)
    if len(xy) < 2:
        return []
    ax0, ay0, ax1, ay1 = st.area
    k = 1000.0 / st.plan_scale
    pad = st.swath + 18.0 / k                       # room for the corridor and the chainage labels
    w_m = (ax1 - ax0) / k - 2 * pad
    h_m = (ay1 - ay0) / k - 2 * pad
    if w_m <= 5 or h_m <= 5:
        raise ValueError(f"plan scale 1:{st.plan_scale:g} is too large for {st.paper}: nothing would fit beside the labels")
    along = st.rotate == "along_road"

    def frame_for(i0: int, j: int) -> tuple[bool, PlanFrame]:
        seg = xy[i0:j + 1]
        theta = _bearing(seg[0], seg[-1]) if along and j > i0 else 0.0
        c, s = np.cos(-theta), np.sin(-theta)
        d = seg - seg[0]
        r = np.column_stack([c * d[:, 0] - s * d[:, 1], s * d[:, 0] + c * d[:, 1]])
        w = float(r[:, 0].max() - r[:, 0].min())
        h = float(r[:, 1].max() - r[:, 1].min())
        rc = np.array([(r[:, 0].max() + r[:, 0].min()) / 2, (r[:, 1].max() + r[:, 1].min()) / 2])
        fr = PlanFrame(seg[0].copy(), theta, rc, k, ((ax0 + ax1) / 2, (ay0 + ay1) / 2), st.area, float(ch[i0]), float(ch[j]))
        return (w <= w_m and h <= h_m), fr

    out: list[PlanFrame] = []
    i0 = 0
    n = len(xy)
    while i0 < n - 1:
        ok, fr = frame_for(i0, n - 1)
        if ok:
            out.append(fr)
            break
        lo, hi = i0 + 1, n - 1
        while hi - lo > 1:
            m = (lo + hi) // 2
            if frame_for(i0, m)[0]:
                lo = m
            else:
                hi = m
        out.append(frame_for(i0, lo)[1])
        i0 = lo
    return out


def _normal(angle_s_rad: float) -> np.ndarray:
    """Right-hand normal of a direction in sheet coordinates."""
    return np.array([np.sin(angle_s_rad), -np.cos(angle_s_rad)])


def _section_world(sec: dict, offset: float) -> tuple[float, float]:
    d = sec["direction"]
    return sec["x"] + offset * np.sin(d), sec["y"] - offset * np.cos(d)


def _grouped_lines(sheet: Sheet, fr: PlanFrame, pts: list[tuple[float, float, str]], layer_for: dict[str, str], dashed: bool = False) -> None:
    """Consecutive points with the same class -> one clipped polyline per run."""
    run: list[tuple[float, float]] = []
    cur = None
    x0, y0, x1, y1 = fr.area

    def flush():
        if len(run) >= 2 and cur in layer_for:
            for piece in clip_polyline(fr.to_sheet(np.array(run)), x0, y0, x1, y1):
                sheet.poly(piece, layer_for[cur], dashed=dashed)

    for x, y, cls in pts:
        if cls != cur and run:
            run.append((x, y))       # share the vertex so runs join
            flush()
            run = []
        cur = cls
        run.append((x, y))
    flush()


def plan_sheets(inp: DrawingInputs, st: SheetSettings, *, with_terrain: bool = True) -> list[Sheet]:
    al = inp.al
    frames = plan_windows(al, st)
    sheets: list[Sheet] = []
    t = st.text
    k = 1000.0 / st.plan_scale
    dense_xy, dense_ch = al.densify(1.0)
    dense_xy = np.asarray(dense_xy, float)
    invalid = {g.index for g in al.geometry if not g.valid}
    key_points = [p for p in al.key_points() if p["kind"] != "IP" and p.get("index") not in invalid]
    secs = sorted(inp.sections, key=lambda s: s["chainage"])
    interval = st.chainage_interval
    label_every = interval if interval * k >= 12 else interval * int(np.ceil(12 / (interval * k)))

    for i, fr in enumerate(frames):
        ax0, ay0, ax1, ay1 = fr.area
        sheet = Sheet("plan", i, f"PLAN CH {format_chainage(fr.s0)} TO {format_chainage(fr.s1)}", drawing_number(st, "P", i))
        sheet.info = {"start": fr.s0, "end": fr.s1, "scale": f"1:{st.plan_scale:g}", "rotation_deg": round(float(np.degrees(fr.theta)), 2)}

        # -- contours (terrain snapshot)
        if with_terrain:
            for cl in inp.contours:
                c = np.asarray(cl.coords, float)
                if len(c) < 2:
                    continue
                sxy = fr.to_sheet(c[:, :2])
                if sxy[:, 0].max() < ax0 or sxy[:, 0].min() > ax1 or sxy[:, 1].max() < ay0 or sxy[:, 1].min() > ay1:
                    continue
                pieces = clip_polyline(sxy, ax0, ay0, ax1, ay1)
                lay = "INDEX_CONTOUR" if cl.is_major else "CONTOUR"
                for piece in pieces:
                    sheet.poly(piece, lay)
                    if cl.is_major and len(piece) > 2:
                        seg_len = np.hypot(*np.diff(piece, axis=0).T)
                        if seg_len.sum() > 40:
                            m = len(piece) // 2
                            ang = float(np.degrees(np.arctan2(piece[m][1] - piece[m - 1][1], piece[m][0] - piece[m - 1][0])))
                            ang, _ = readable(ang)
                            sheet.text(piece[m][0], piece[m][1] + 0.4, f"{cl.level:g}", t.get("tiny", 1.5), ang, "CONTOUR_TEXT", "center", "bottom")

        # -- corridor: formation edges and daylight lines
        if secs:
            for side in ("left", "right"):
                hinge = [(*_section_world(s, s[side]["hinge_offset"]), "f") for s in secs]
                _grouped_lines(sheet, fr, hinge, {"f": "FORMATION"})
                catch = []
                for s in secs:
                    co = s[side].get("catch_offset")
                    kind = s[side].get("kind", "")
                    if co is None or kind not in ("cut", "fill", "level"):
                        catch.append((*_section_world(s, s[side]["hinge_offset"]), "skip"))
                    else:
                        catch.append((*_section_world(s, co), "cut" if kind == "cut" else "fill"))
                _grouped_lines(sheet, fr, catch, {"cut": "DAYLIGHT_CUT", "fill": "DAYLIGHT_FILL", "level": "FORMATION"}, dashed=True)

        # -- tangents and IPs
        for g in al.geometry:
            if g.curve_length > 0 and g.valid:
                pts = fr.to_sheet(np.array([g.bc, (g.x, g.y), g.ec]))
                for piece in clip_polyline(pts, ax0, ay0, ax1, ay1):
                    sheet.poly(piece, "TANGENT", dashed=True)
            px, py = fr.pt(g.x, g.y)
            if fr.inside(px, py, 3):
                sheet.circle(px, py, 1.0, "IP")
                lbl = f"IP {g.label}" if g.index not in (0, len(al.ips) - 1) else ("START" if g.index == 0 else "END")
                sheet.text(px + 1.6, py + 1.6, lbl, t.get("small", 1.8), layer="IP")
                if g.radius > 0 and g.index not in (0, len(al.ips) - 1):
                    sheet.text(px + 1.6, py - 0.6, f"R={g.radius:g}", t.get("tiny", 1.5), layer="IP", valign="top")

        # -- centreline
        for piece in clip_polyline(fr.to_sheet(dense_xy), ax0, ay0, ax1, ay1):
            sheet.poly(piece, "H_ALIGN")

        # -- chainage ticks and labels (right side)
        for chn in al.stations(interval, include_curve_points=False):
            x, y, d = al.point_and_direction(float(chn))
            px, py = fr.pt(x, y)
            if not fr.inside(px, py, -1.0):
                continue
            a = np.radians(fr.angle_deg(d))
            nrm = _normal(a)
            major = abs((chn / label_every) - round(chn / label_every)) < 1e-6
            L = 3.0 if major else 1.8
            sheet.line(px, py, px + nrm[0] * L, py + nrm[1] * L, "CHAINAGE")
            if major:
                ang, align = readable(np.degrees(a) - 90.0, "left")
                sheet.text(px + nrm[0] * (L + 0.8), py + nrm[1] * (L + 0.8), format_chainage(float(chn)), t.get("small", 1.8), ang, "CHAINAGE", align, "middle")

        # -- curve key points (left side)
        for kp in key_points:
            px, py = fr.pt(kp["x"], kp["y"])
            if not fr.inside(px, py, -1.0):
                continue
            _, _, d = al.point_and_direction(float(kp["chainage"]))
            a = np.radians(fr.angle_deg(d))
            nrm = -_normal(a)
            sheet.line(px, py, px + nrm[0] * 3.0, py + nrm[1] * 3.0, "KEYPOINT")
            ang, align = readable(np.degrees(a) - 90.0, "right")
            sheet.text(px + nrm[0] * 3.8, py + nrm[1] * 3.8, f"{KP_LABEL.get(kp['kind'], kp['kind'])} {format_chainage(kp['chainage'])}",
                       t.get("small", 1.8), ang, "KEYPOINT", align, "middle")

        # -- structures: culverts across the road, walls and drains along one or both sides
        for s in inp.structures:
            c0, c1 = float(s["from"]), float(s["to"])
            if c1 < fr.s0 - st.swath or c0 > fr.s1 + st.swath:
                continue
            kind = s["kind"]
            spec = STRUCTURE_KINDS.get(kind, {})
            group = spec.get("group", "wall")
            tspec = type_spec(kind, str((s.get("params") or {}).get("type") or ""))
            code = spec.get("label", kind).upper().split(" (")[0]
            if group == "cross":
                chm = (c0 + c1) / 2
                if chm < al.start_chainage or chm > al.end_chainage:
                    continue
                x, y, d = al.point_and_direction(chm)
                if secs:
                    nearest = min(secs, key=lambda q: abs(q["chainage"] - chm))
                    hl, hr = nearest["left"]["hinge_offset"] - 1.5, nearest["right"]["hinge_offset"] + 1.5
                else:
                    hl, hr = -4.0, 4.0
                for dc in (-0.6, 0.6):
                    ch2 = min(max(chm + dc, al.start_chainage), al.end_chainage)
                    sheet.poly(fr.to_sheet(np.array([al.offset_point(ch2, hl), al.offset_point(ch2, hr)])), "CULVERT", width=0.5)
                px, py = fr.pt(*al.offset_point(chm, hr + 1.0))
                a = np.radians(fr.angle_deg(d))
                ang, align = readable(np.degrees(a) - 90.0, "left")
                span = (s.get("params") or {}).get("span")
                cells = int((s.get("params") or {}).get("cells") or 1)
                lab = f"{code} {tspec.get('label', '')} {cells}x{span} m  CH {format_chainage(chm)}" if span else f"{code}  CH {format_chainage(chm)}"
                sheet.text(px, py, lab, t.get("small", 1.8), ang, "CULVERT", align, "middle")
            else:
                sides = ("left", "right") if (s.get("side") or "left") == "both" else ((s.get("side") or "left"),)
                for side in sides:
                    sgn = -1.0 if side == "left" else 1.0
                    off_extra = float((s.get("params") or {}).get("offset") or 0.0) if kind == "catch_drain" else 0.0
                    pts = []
                    for chn in np.linspace(max(c0, al.start_chainage), min(c1, al.end_chainage), max(3, int((c1 - c0) / 5) + 2)):
                        if secs:
                            nearest = min(secs, key=lambda q: abs(q["chainage"] - chn))
                            base = nearest[side].get("catch_offset") if group == "drain" and nearest[side].get("catch_offset") is not None else nearest[side]["hinge_offset"]
                            off = float(base) + sgn * (0.6 + off_extra)
                        else:
                            off = sgn * (4.0 + off_extra)
                        pts.append(al.offset_point(float(chn), off))
                    if len(pts) < 2:
                        continue
                    layer = "DRAIN" if group == "drain" else "WALL"
                    for piece in clip_polyline(fr.to_sheet(np.array(pts)), ax0, ay0, ax1, ay1):
                        sheet.poly(piece, layer, dashed=(group == "drain"), width=0.7 if group == "wall" else 0.35)
                    chm = min(max((c0 + c1) / 2, al.start_chainage), al.end_chainage)
                    _, _, d = al.point_and_direction(chm)
                    a = np.radians(fr.angle_deg(d))
                    nrm = _normal(a) * sgn                     # outward, away from the road
                    px, py = fr.pt(*pts[len(pts) // 2])
                    px, py = px + nrm[0] * 2.4, py + nrm[1] * 2.4
                    if fr.inside(px, py, -1.0):
                        ang, align = readable(fr.angle_deg(d), "center")
                        h = (s.get("params") or {}).get("height")
                        detail = f" H={h:g} m" if h else ""
                        sheet.text(px, py, f"{code} - {tspec.get('label', '')}{detail} {format_chainage(c0)}-{format_chainage(c1)}",
                                   t.get("tiny", 1.5), ang, layer, "center", "middle")

        # -- match lines
        for chm, other in ((fr.s0, i - 1), (fr.s1, i + 1)):
            if other < 0 or other >= len(frames):
                continue
            x, y, d = al.point_and_direction(chm)
            px, py = fr.pt(x, y)
            a = np.radians(fr.angle_deg(d))
            nrm = _normal(a)
            L = (st.swath + 6) * k
            sheet.poly([(px - nrm[0] * L, py - nrm[1] * L), (px + nrm[0] * L, py + nrm[1] * L)], "MATCH", dashed=True)
            ang, align = readable(np.degrees(a) - 90.0, "left")
            sheet.text(px + nrm[0] * (L + 1), py + nrm[1] * (L + 1), f"MATCH LINE CH {format_chainage(chm)} - SEE SHEET {drawing_number(st, 'P', other)}",
                       t.get("small", 1.8), ang, "MATCH", align, "middle")

        # -- decorations
        sheet.text(ax0 + 4, ay1 - 4, sheet.title, t.get("title", 3.5), layer="TEXT", valign="top", bold=True)
        sheet.text(ax0 + 4, ay1 - 4 - t.get("title", 3.5) * 1.6, f"SCALE 1:{st.plan_scale:g}   {'rotated along the road' if st.rotate == 'along_road' else 'north up'}",
                   t.get("small", 1.8), layer="TEXT", valign="top")
        north_arrow(sheet, ax1 - 16, ay1 - 26, float(np.degrees(np.pi / 2 - fr.theta)))
        scale_bar(sheet, ax0 + 6, ay0 + 8, st.plan_scale, t=t)

        rows = []
        for r in inp.curve_rows:
            if r["radius"] <= 0 or r["chainage"] is None:
                continue
            if r["chainage"] < fr.s0 - 50 or r["chainage"] > fr.s1 + 50:
                continue
            rows.append([r["label"], f"{r['deflection_deg']:.3f}", f"{r['radius']:g}", f"{r['transition']:g}" if r["transition"] else "-", f"{r['tangent']:.2f}", f"{r['arc_length']:.2f}",
                         f"{r['external']:.2f}", format_chainage(r["ts"]) if r.get("ts") is not None else "-", format_chainage(r["sc"]) if r.get("sc") is not None else "-",
                         format_chainage(r["cs"]) if r.get("cs") is not None else "-", format_chainage(r["st"]) if r.get("st") is not None else "-",
                         f"{r['superelevation_pct']:g}" if r.get("superelevation_pct") is not None else "-"])
        table = {"title": "HORIZONTAL CURVE DATA", "columns": ["IP", "Delta deg", "R m", "Ls m", "T m", "Lc m", "E m", "TS/BC", "SC", "CS", "ST/EC", "e %"],
                 "widths": [0.6, 1, 0.8, 0.7, 0.9, 0.9, 0.8, 1.1, 1.1, 1.1, 1.1, 0.6], "rows": rows}
        draw_frame(sheet, st, inp, title=sheet.title, number=sheet.number, scale_text=f"1:{st.plan_scale:g}", sheet_no=i + 1, sheet_count=len(frames), table=table)
        sheets.append(sheet)
    return sheets
