"""Longitudinal section sheets: ground and design grade lines over a datum with the usual data
bands (vertical grade, design level, ground level, cut / fill, chainage, horizontal alignment
diagram, superelevation) and a vertical curve data table in the title strip.

Every sheet covers the full drawing width at the horizontal scale. Where the levels do not fit the
plot height at the vertical scale, the sheet is divided into datum segments: each segment has its
own datum (labelled) and a break line separates it from the next, the way steep hill-road
longitudinal sections are drawn by hand.
"""
from __future__ import annotations

import numpy as np

from ...engine.alignment import format_chainage
from .frame import DrawingInputs, SheetSettings, chainage_stations, draw_frame, drawing_number
from .model import Sheet, nice_step

LABEL_COL = 38.0


def _z1(va, ch: float) -> float:
    return float(np.asarray(va.elevation_at(ch), float).ravel()[0])


def _bands(inp: DrawingInputs) -> list[tuple[str, str, float]]:
    b = [("grade", "VERTICAL GRADE", 9.0), ("design", "DESIGN LEVEL", 10.0), ("ground", "GROUND LEVEL", 10.0),
         ("depth", "CUT (-) / FILL (+)", 8.0), ("chainage", "CHAINAGE", 10.0), ("halign", "HORIZONTAL ALIGNMENT", 10.0)]
    if inp.supere is not None:
        b.append(("super", "SUPERELEVATION L / R %", 8.0))
    return b


def _z_range(inp: DrawingInputs, s0: float, s1: float) -> tuple[float, float]:
    chs = np.linspace(s0, s1, max(int((s1 - s0) / 2.0), 2) + 1)
    zs = [inp.ground_z(chs)]
    if inp.va is not None:
        zs.append(inp.va.elevation_at(np.clip(chs, inp.va.start_chainage, inp.va.end_chainage)))
    z = np.concatenate(zs)
    z = z[np.isfinite(z)]
    if len(z) == 0:
        return 0.0, 1.0
    return float(z.min()), float(z.max())


def _geometry(inp: DrawingInputs, st: SheetSettings) -> dict:
    ax0, ay0, ax1, ay1 = st.area
    bands = _bands(inp)
    bands_h = sum(h for _, _, h in bands)
    g = {"kh": 1000.0 / st.profile_h_scale, "kv": 1000.0 / st.profile_v_scale, "px0": ax0 + LABEL_COL, "px1": ax1 - 6,
         "bands": bands, "bands_h": bands_h, "datum_y": ay0 + bands_h + 8, "plot_top": ay1 - 16}
    g["span"] = (g["px1"] - g["px0"]) / g["kh"]
    g["plot_h"] = g["plot_top"] - g["datum_y"] - 8
    if g["span"] <= 1 or g["plot_h"] <= 10:
        raise ValueError("profile scales are too large for the paper")
    return g


def profile_windows(inp: DrawingInputs, st: SheetSettings) -> list[tuple[float, float]]:
    """Sheet chainage ranges: the full drawing width each (the last one shorter)."""
    al = inp.al
    span = _geometry(inp, st)["span"]
    out = []
    s = al.start_chainage
    while s < al.end_chainage - 1e-6:
        e = min(s + span, al.end_chainage)
        out.append((s, e))
        s = e
    return out


def datum_segments(inp: DrawingInputs, s0: float, s1: float, kv: float, plot_h: float) -> list[tuple[float, float, float, float]]:
    """(from, to, datum, grid step) segments so that the levels of each fit the plot height."""
    out = []
    s = s0
    while s < s1 - 1e-6:
        e = s1
        zmin, zmax = _z_range(inp, s, e)
        if (zmax - zmin) * kv > plot_h:
            lo, hi = s + min((s1 - s0) * 0.02, e - s), e
            for _ in range(24):
                m = (lo + hi) / 2
                zmin, zmax = _z_range(inp, s, m)
                if (zmax - zmin) * kv > plot_h:
                    hi = m
                else:
                    lo = m
            e = lo
            zmin, zmax = _z_range(inp, s, e)
        step = nice_step(max(zmax - zmin, plot_h / kv * 0.5), 10.0, kv)
        datum = float(np.floor((zmin - 0.3 * step) / step) * step)
        out.append((s, e, datum, step))
        s = e
    return out


def profile_sheets(inp: DrawingInputs, st: SheetSettings) -> list[Sheet]:
    if not inp.ground and inp.va is None:
        return []
    al, va = inp.al, inp.va
    t = st.text
    G = _geometry(inp, st)
    kh, kv, px0, px1, datum_y, plot_top = G["kh"], G["kv"], G["px0"], G["px1"], G["datum_y"], G["plot_top"]
    windows = profile_windows(inp, st)
    ax0, ay0, ax1, ay1 = st.area
    bands, bands_h = G["bands"], G["bands_h"]
    tiny, small = t.get("tiny", 1.5), t.get("small", 1.8)
    key = [p for p in al.key_points() if p["kind"] in ("BC", "EC", "TS", "SC", "CS", "ST")]
    vc_marks: list[tuple[float, str]] = []
    if va is not None:
        for c in va.curves:
            vc_marks += [(c.bvc, "BVC"), (c.evc, "EVC")]
    sheets: list[Sheet] = []

    for i, (s0, s1) in enumerate(windows):
        sheet = Sheet("profile", i, f"LONGITUDINAL SECTION CH {format_chainage(s0)} TO {format_chainage(s1)}", drawing_number(st, "L", i))
        segs = datum_segments(inp, s0, s1, kv, G["plot_h"])
        sheet.info = {"start": s0, "end": s1, "scale": f"H 1:{st.profile_h_scale:g} V 1:{st.profile_v_scale:g}", "datums": [d for _, _, d, _ in segs], "datum": segs[0][2]}
        X = lambda ch: px0 + (ch - s0) * kh  # noqa: E731

        def seg_at(ch: float) -> tuple[float, float, float, float]:
            for a, b, d, stp in segs:
                if ch <= b + 1e-9:
                    return a, b, d, stp
            return segs[-1]

        def Y(ch: float, z: float) -> float:
            return datum_y + (z - seg_at(ch)[2]) * kv

        # -- datum line, grids, datum labels and break lines per segment
        sheet.line(px0, datum_y, px1, datum_y, "BAND", width=0.35)
        sheet.line(px0, datum_y, px0, plot_top, "BAND")
        for k, (a, b, datum, step) in enumerate(segs):
            xa, xb = X(a), X(b)
            top_z = datum + np.ceil((plot_top - datum_y) / kv / step) * step
            for z in np.arange(datum, top_z + 1e-9, step):
                yy = datum_y + (z - datum) * kv
                if yy > plot_top:
                    break
                sheet.line(xa, yy, xb, yy, "GRID")
                if k == 0:
                    sheet.text(px0 - 1.5, yy, f"{z:.2f}", tiny, layer="BAND_TEXT", align="right", valign="middle")
                else:
                    sheet.text(xa + 1.2, yy + 0.3, f"{z:.2f}", tiny, layer="BAND_TEXT", align="left", valign="bottom")
            label_x = ax0 + 1.5 if k == 0 else xa + 1.2
            sheet.text(label_x, datum_y + 1.0 if k == 0 else datum_y + 1.0, f"DATUM RL {datum:.2f}", small, layer="TEXT", bold=True)
            if k < len(segs) - 1:
                # break line: a zigzag from the datum to the top of the plot
                zz = [(xb, datum_y)]
                yy = datum_y
                while yy < plot_top - 2.0:
                    zz += [(xb - 1.0, yy + 1.0), (xb + 1.0, yy + 3.0)]
                    yy += 4.0
                zz.append((xb, plot_top))
                sheet.poly(zz, "MATCH", width=0.35)
                sheet.text(xb + 1.6, plot_top - 3.0, f"DATUM CHANGE CH {format_chainage(b)}", tiny, 90.0, "MATCH", "right", "middle")

        # -- stations and band ticks
        extra = [p["chainage"] for p in key] + [c for c, _ in vc_marks] + ([p.chainage for p in va.pvis] if va else [])
        stations = chainage_stations(s0, s1, st.chainage_interval, extra)
        keyset = {round(float(c), 3) for c in extra}
        for ch in stations:
            x = X(ch)
            sheet.line(x, ay0, x, datum_y, "BAND")
            sheet.line(x, datum_y, x, plot_top, "GRID")

        # -- band frame and labels
        sheet.rect(ax0, ay0, px1 - ax0, bands_h, "BAND")
        yb = ay0 + bands_h
        band_y: dict[str, tuple[float, float]] = {}
        for bid, label, h in bands:
            band_y[bid] = (yb - h, yb)
            sheet.line(ax0, yb - h, px1, yb - h, "BAND")
            sheet.text(ax0 + 1.5, yb - h / 2, label, tiny, layer="BAND_TEXT", valign="middle", bold=True)
            yb -= h
        sheet.line(px0, ay0, px0, ay0 + bands_h, "BAND")

        # -- band values: drop stations closer than the rotated text can bear (all values are in the
        #    tables); a key point wins over an interval station drawn just before it
        gz = inp.ground_z(stations)
        dz = va.elevation_at(np.clip(stations, va.start_chainage, va.end_chainage)) if va is not None else np.full(len(stations), np.nan)
        if va is not None:
            dz = np.where((stations < va.start_chainage - 1e-6) | (stations > va.end_chainage + 1e-6), np.nan, dz)
        drawn_x: list[float] = []
        order = sorted(range(len(stations)), key=lambda j: (X(stations[j]), round(float(stations[j]), 3) not in keyset))
        skip: set[int] = set()
        for j in order:
            x = X(stations[j])
            if drawn_x and x - drawn_x[-1] < 2.4:
                skip.add(j)
                continue
            drawn_x.append(x)
        for j, ch in enumerate(stations):
            if j in skip:
                continue
            x = X(ch)

            def val(bid: str, s: str) -> None:
                y0b, y1b = band_y[bid]
                sheet.text(x, (y0b + y1b) / 2, s, tiny, 90.0, "BAND_TEXT", "center", "middle")

            val("chainage", format_chainage(float(ch)))
            if np.isfinite(gz[j]):
                val("ground", f"{gz[j]:.3f}")
            if np.isfinite(dz[j]):
                val("design", f"{dz[j]:.3f}")
                if np.isfinite(gz[j]):
                    val("depth", f"{dz[j] - gz[j]:+.2f}")

        # -- ground and design lines, per datum segment
        for a, b, datum, _ in segs:
            if inp.ground:
                g = np.asarray(inp.ground, float)
                m = (g[:, 0] > a) & (g[:, 0] < b)
                pts = [(a, float(inp.ground_z(a)))] + [(float(c), float(z)) for c, z in g[m]] + [(b, float(inp.ground_z(b)))]
                pts = [(c, z) for c, z in pts if np.isfinite(z)]
                sheet.poly([(X(c), datum_y + (z - datum) * kv) for c, z in pts], "GROUND")
            if va is not None:
                d0, d1 = max(a, va.start_chainage), min(b, va.end_chainage)
                if d1 > d0:
                    line = [(p["chainage"], p["z"]) for p in va.densify(2.0) if d0 < p["chainage"] < d1]
                    line = [(d0, _z1(va, d0))] + line + [(d1, _z1(va, d1))]
                    sheet.poly([(X(c), datum_y + (z - datum) * kv) for c, z in line], "DESIGN")

        # -- PVIs, vertical curves and the grade band
        if va is not None:
            for idx, p in enumerate(va.pvis):
                if p.chainage < s0 - 1e-6 or p.chainage > s1 + 1e-6:
                    continue
                x, y = X(p.chainage), Y(p.chainage, p.elevation)
                sheet.line(x, datum_y, x, y, "KEYPOINT", dashed=True)
                sheet.circle(x, y, 0.8, "KEYPOINT")
                c = next((c for c in va.curves if c.index == idx), None)
                lines = [f"PVI {idx}", f"CH {format_chainage(p.chainage)}", f"RL {p.elevation:.3f}"]
                if c:
                    lines.append(f"L={c.length:g}" + (f"  K={c.K:.1f}" if c.K else ""))
                yy = min(y + 3.0, plot_top - 2 - tiny * 1.5 * len(lines))
                for ln in lines[::-1]:
                    sheet.text(x + 1.0, yy, ln, tiny, layer="KEYPOINT")
                    yy += tiny * 1.5
            for chm, lab in vc_marks:
                if s0 - 1e-6 <= chm <= s1 + 1e-6:
                    x = X(chm)
                    y = Y(chm, _z1(va, chm))
                    sheet.line(x, y - 2.0, x, y + 2.0, "KEYPOINT")
                    sheet.text(x + 0.6, y - 2.2, f"{lab} {format_chainage(chm)}", tiny, 90.0, "KEYPOINT", "right", "middle")
            y0b, y1b = band_y["grade"]
            for j in range(len(va.pvis) - 1):
                a, b = va.pvis[j], va.pvis[j + 1]
                xa, xb = max(X(a.chainage), px0), min(X(b.chainage), px1)
                if xb - xa < 1:
                    continue
                gpct = float(va.grades[j])
                rising = gpct >= 0
                sheet.line(xa, y0b + (1.0 if rising else y1b - y0b - 1.0), xb, y0b + (y1b - y0b - 1.0 if rising else 1.0), "BAND")
                sheet.text((xa + xb) / 2, y1b - 1.0, f"{gpct:+.2f} %", tiny, layer="BAND_TEXT", align="center", valign="top")
                sheet.text((xa + xb) / 2, y0b + 1.0, f"{b.chainage - a.chainage:.1f} m", tiny, layer="BAND_TEXT", align="center")
            for c in va.curves:
                xa, xb = X(c.bvc), X(c.evc)
                if xb < px0 or xa > px1:
                    continue
                xa, xb = max(xa, px0), min(xb, px1)
                yb_ = y0b + 2.4
                sheet.poly([(xa, yb_ + 1.2), (xa, yb_), (xb, yb_), (xb, yb_ + 1.2)], "KEYPOINT")
                sheet.text((xa + xb) / 2, yb_ + 1.6, f"VC {c.kind.upper()} L={c.length:g}", tiny, layer="KEYPOINT", align="center", valign="bottom")

        # -- horizontal alignment diagram
        y0b, y1b = band_y["halign"]
        ym = (y0b + y1b) / 2
        sheet.line(px0, ym, px1, ym, "H_ALIGN", width=0.25)
        for g in al.geometry:
            if g.curve_length <= 0 or not g.valid or g.ec_chainage < s0 or g.bc_chainage > s1:
                continue
            off = 2.5 if g.deflection > 0 else -2.5
            sc, cs = (g.sc_chainage, g.cs_chainage) if g.transition else (g.bc_chainage, g.ec_chainage)
            pts = [(X(g.bc_chainage), ym), (X(sc), ym + off), (X(cs), ym + off), (X(g.ec_chainage), ym)]
            pts = [(min(max(x, px0), px1), y) for x, y in pts]
            sheet.poly(pts, "H_ALIGN", width=0.35)
            xm = min(max(X(g.mc_chainage), px0 + 10), px1 - 10)
            label = f"IP {g.label}  R={g.radius:g}  Lc={g.curve_length:.1f}" + (f"  Ls={g.transition:g}" if g.transition else "")
            sheet.text(xm, ym + off + (0.6 if off > 0 else -0.6), label, tiny, layer="BAND_TEXT", align="center", valign="bottom" if off > 0 else "top")
            for chn, lab in ((g.bc_chainage, "TS" if g.transition else "BC"), (sc, "SC" if g.transition else None), (cs, "CS" if g.transition else None), (g.ec_chainage, "ST" if g.transition else "EC")):
                if lab and s0 - 1e-6 <= chn <= s1 + 1e-6:
                    sheet.text(X(chn) + 0.4, y0b + 0.6, f"{lab} {format_chainage(chn)}", tiny * 0.9, 90.0, "BAND_TEXT", "left", "middle")

        # -- superelevation diagram
        if inp.supere is not None and "super" in band_y:
            y0b, y1b = band_y["super"]
            ym = (y0b + y1b) / 2
            emax = max([abs(c.e_pct) for c in inp.supere.curves] + [inp.supere.camber_pct, 1.0])
            ke = (y1b - y0b) / 2 * 0.85 / emax
            chs = np.arange(s0, s1 + 1e-6, 2.0)
            lft, rgt = zip(*[inp.supere.slopes_at(float(c)) for c in chs]) if len(chs) else ([], [])
            sheet.line(px0, ym, px1, ym, "GRID")
            sheet.poly([(X(c), ym + v * ke) for c, v in zip(chs, lft)], "DAYLIGHT_CUT", dashed=True)
            sheet.poly([(X(c), ym + v * ke) for c, v in zip(chs, rgt)], "DAYLIGHT_FILL")
            sheet.text(px0 + 1, y1b - 0.5, f"L dashed / R solid; normal camber {inp.supere.camber_pct:g} %", tiny * 0.9, layer="BAND_TEXT", valign="top")
            for c in inp.supere.curves:
                for chn, lab in ((c.start, "start"), (c.full_from, f"e={c.e_pct:g}%"), (c.full_to, ""), (c.end, "end")):
                    if lab and s0 <= chn <= s1:
                        sheet.text(X(chn) + 0.4, y0b + 0.6, f"{lab} {format_chainage(chn)}", tiny * 0.9, 90.0, "BAND_TEXT", "left", "middle")

        # -- match lines and headings
        for chm, other in ((s0, i - 1), (s1, i + 1)):
            if 0 <= other < len(windows):
                x = X(chm)
                sheet.line(x, ay0, x, plot_top, "MATCH", dashed=True)
                sheet.text(x + 0.8, plot_top - 2, f"MATCH LINE CH {format_chainage(chm)} - SEE SHEET {drawing_number(st, 'L', other)}", small, 90.0, "MATCH", "right", "middle")
        sheet.text(ax0 + 4, ay1 - 4, sheet.title, t.get("title", 3.5), layer="TEXT", valign="top", bold=True)
        datums = ", ".join(f"{d:.2f}" for d in sheet.info["datums"])
        sheet.text(ax0 + 4, ay1 - 4 - t.get("title", 3.5) * 1.6, f"SCALE {sheet.info['scale']}   DATUM RL {datums}", small, layer="TEXT", valign="top")

        rows = []
        if va is not None:
            for r in va.table():
                if r["chainage"] < s0 - 50 or r["chainage"] > s1 + 50:
                    continue
                rows.append([r["index"], format_chainage(r["chainage"]), f"{r['elevation']:.3f}", f"{r['grade_in']:+.2f}" if r["grade_in"] is not None else "-",
                             f"{r['grade_out']:+.2f}" if r["grade_out"] is not None else "-", f"{r['length']:g}" if r["length"] else "-",
                             f"{r['K']:.1f}" if r.get("K") else "-", format_chainage(r["bvc"]) if r.get("bvc") is not None else "-",
                             format_chainage(r["evc"]) if r.get("evc") is not None else "-", (r.get("kind") or "-").upper()])
        table = {"title": "VERTICAL CURVE DATA", "columns": ["PVI", "Chainage", "RL m", "g in %", "g out %", "L m", "K", "BVC", "EVC", "Type"],
                 "widths": [0.5, 1.1, 0.9, 0.8, 0.8, 0.6, 0.6, 1.1, 1.1, 0.7], "rows": rows}
        draw_frame(sheet, st, inp, title=sheet.title, number=sheet.number, scale_text=sheet.info["scale"], sheet_no=i + 1, sheet_count=len(windows), table=table)
        sheets.append(sheet)
    return sheets
