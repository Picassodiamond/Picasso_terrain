"""Cross-section sheets: every corridor section in a grid, ground and design lines over a datum,
cut / fill hatching, offset and level bands, areas and flags."""
from __future__ import annotations

import numpy as np

from ...engine.alignment import format_chainage
from ..structures import section_structures, structures_at  # noqa: F401  (structures_at re-exported)
from .frame import DrawingInputs, SheetSettings, draw_frame, drawing_number
from .model import Sheet

BANDS = (("GL", 9.5), ("DL", 9.5), ("OFF", 6.5))     # bottom -> top: ground level, design level, offset
BANDS_H = sum(h for _, h in BANDS)
TITLE_H = 13.0
PAD_L = 9.0
PAD_R = 6.0


def split_regions(design: list, ground: list) -> list[tuple[str, list[tuple[float, float]]]]:
    """Polygons between the design and ground lines, split at their crossings: ('cut' | 'fill', ring)."""
    d = np.asarray(design, float)
    g = np.asarray(ground, float)
    if len(d) < 2 or len(g) < 2:
        return []
    o0, o1 = max(d[:, 0].min(), g[:, 0].min()), min(d[:, 0].max(), g[:, 0].max())
    if o1 <= o0:
        return []
    o = np.unique(np.concatenate([d[:, 0], g[:, 0]]))
    o = o[(o >= o0) & (o <= o1)]
    zd, zg = np.interp(o, d[:, 0], d[:, 1]), np.interp(o, g[:, 0], g[:, 1])
    diff = zd - zg
    O, D, G = [], [], []
    for i in range(len(o)):
        O.append(o[i]); D.append(zd[i]); G.append(zg[i])
        if i + 1 < len(o) and diff[i] * diff[i + 1] < 0:
            f = diff[i] / (diff[i] - diff[i + 1])
            oc = o[i] + f * (o[i + 1] - o[i])
            zc = zd[i] + f * (zd[i + 1] - zd[i])
            O.append(oc); D.append(zc); G.append(zc)
    O, D, G = np.array(O), np.array(D), np.array(G)
    S = np.sign(np.round(D - G, 9))
    out = []
    start = 0

    def close(a: int, b: int) -> None:
        sg = S[a:b + 1]
        nz = sg[sg != 0]
        if len(nz) and b > a:
            ring = list(zip(O[a:b + 1], D[a:b + 1])) + list(zip(O[a:b + 1][::-1], G[a:b + 1][::-1]))
            out.append(("fill" if nz[0] > 0 else "cut", [(float(x), float(y)) for x, y in ring]))

    for i in range(1, len(O)):
        if S[i] == 0 and i > start:
            close(start, i)
            start = i
    close(start, len(O) - 1)
    return out


def _annotation_offsets(design: list, k: float, min_mm: float = 3.2) -> list[float]:
    """Design vertices to annotate: keep the ends, the centre line, and points at least `min_mm` apart."""
    offs = [float(p[0]) for p in design]
    if not offs:
        return []
    keep = [offs[0]]
    for o in offs[1:-1]:
        if (o - keep[-1]) * k >= min_mm or abs(o) < 1e-9:
            if abs(o) < 1e-9 and (o - keep[-1]) * k < min_mm and abs(keep[-1]) > 1e-9:
                keep[-1] = o      # prefer the centre line over a crowded neighbour
            else:
                keep.append(o)
    if len(offs) > 1 and (offs[-1] - keep[-1]) * k >= min_mm * 0.6:
        keep.append(offs[-1])
    elif len(offs) > 1:
        keep[-1] = offs[-1]
    if not any(abs(o) < 1e-9 for o in keep) and offs[0] < 0 < offs[-1]:
        keep.append(0.0)
    return sorted(set(keep))


def trim_section(sec: dict, margin: float = 2.0, half_min: float = 6.0) -> dict:
    """Copy of a section with the ground line cut to the design extent plus a margin (what the
    drawing shows), so a wide corridor swath does not blow up the cell."""
    d = np.asarray(sec["design"], float)
    g = np.asarray(sec["ground"], float)
    o0, o1 = min(d[:, 0].min() - margin, -half_min), max(d[:, 0].max() + margin, half_min)
    o0, o1 = max(o0, g[:, 0].min()), min(o1, g[:, 0].max())
    m = (g[:, 0] > o0) & (g[:, 0] < o1)
    pts = [(o0, float(np.interp(o0, g[:, 0], g[:, 1])))] + [(float(o), float(z)) for o, z in g[m]] + [(o1, float(np.interp(o1, g[:, 0], g[:, 1])))]
    out = dict(sec)
    out["ground"] = pts
    out["design"] = [(float(o), float(z)) for o, z in d]
    return out


def _cell_size(sec: dict, k: float) -> tuple[float, float, float, float, float]:
    """(width, height, o_min, o_max, datum) of the cell for a (trimmed) section."""
    pts = np.asarray(sec["design"] + sec["ground"], float)
    o_min, o_max = pts[:, 0].min(), pts[:, 0].max()
    zmin, zmax = pts[:, 1].min(), pts[:, 1].max()
    datum = float(np.floor(zmin - 0.5))
    w = (o_max - o_min) * k + PAD_L + PAD_R
    h = TITLE_H + BANDS_H + 4.0 + (zmax - datum) * k + 6.0
    return w, h, float(o_min), float(o_max), datum


def _draw_section(sheet: Sheet, sec: dict, cx: float, cy: float, k: float, o_min: float, datum: float, t: dict, scale_note: str = "",
                  structures=()) -> None:
    tiny, small = t.get("tiny", 1.5), t.get("small", 1.8)
    X = lambda o: cx + PAD_L + (o - o_min) * k  # noqa: E731
    base = cy + TITLE_H + BANDS_H + 4.0
    Y = lambda z: base + (z - datum) * k  # noqa: E731
    design, ground = sec["design"], sec["ground"]
    pts = np.asarray(design + ground, float)
    o_max = pts[:, 0].max()
    zmax = pts[:, 1].max()

    # title, areas and flags
    sheet.text(cx + PAD_L, cy + TITLE_H - 1.0, f"CH {format_chainage(sec['chainage'])}", t.get("normal", 2.5), layer="TEXT", valign="top", bold=True)
    sheet.text(cx + PAD_L, cy + TITLE_H - 1.0 - t.get("normal", 2.5) * 1.5, f"CUT {sec['cut_area']:.2f} m2   FILL {sec['fill_area']:.2f} m2   template {sec.get('template_id', '')}{scale_note}",
               tiny, layer="TEXT", valign="top")
    if sec.get("flags"):
        sheet.text(cx + PAD_L, cy + 1.2, ", ".join(sec["flags"]).replace("_", " "), tiny, layer="STRUCTURE")

    # bands: ground RL, design RL, offset (bottom -> top)
    xb0, xb1 = X(o_min) - 1.0, X(o_max) + 1.0
    yb = cy + TITLE_H
    for lab, bh in BANDS:
        sheet.line(xb0, yb, xb1, yb, "BAND")
        sheet.text(xb0 - 0.8, yb + bh / 2, lab, tiny, layer="BAND_TEXT", align="right", valign="middle")
        yb += bh
    sheet.line(xb0, yb, xb1, yb, "BAND")
    # datum
    sheet.line(xb0, base, xb1, base, "BAND", width=0.35)
    sheet.text(xb0, base + 0.6, f"DATUM {datum:.2f}", tiny, layer="TEXT")
    g = np.asarray(ground, float)
    for o in _annotation_offsets(design, k):
        x = X(o)
        zd = float(np.interp(o, [p[0] for p in design], [p[1] for p in design]))
        zg = float(np.interp(o, g[:, 0], g[:, 1])) if g[:, 0].min() - 1e-6 <= o <= g[:, 0].max() + 1e-6 else None
        sheet.line(x, cy + TITLE_H, x, base, "GRID")
        yb = cy + TITLE_H
        vals = [f"{zg:.3f}" if zg is not None else "-", f"{zd:.3f}", f"{o:+.2f}"]
        for v, (_, bh) in zip(vals, BANDS):
            sheet.text(x, yb + bh / 2, v, tiny, 90.0, "BAND_TEXT", "center", "middle")
            yb += bh

    # centre line, hatches, ground and design
    sheet.line(X(0.0), base, X(0.0), Y(zmax) + 3.0, "CHAINAGE", dashed=True)
    sheet.text(X(0.0), Y(zmax) + 3.4, "CL", small, layer="TEXT", align="center")
    for kind, ring in split_regions(design, ground):
        sheet.hatch([(X(o), Y(z)) for o, z in ring], "CUT_HATCH" if kind == "cut" else "FILL_HATCH", "ANSI31" if kind == "cut" else "ANSI37", 0.35)
    sheet.poly([(X(o), Y(z)) for o, z in ground], "GROUND")
    sheet.poly([(X(o), Y(z)) for o, z in design], "DESIGN")
    if structures:
        draw_structures(sheet, sec, structures, X, Y, t)
    for side in ("left", "right"):
        s = sec.get(side) or {}
        co = s.get("catch_offset")
        if co is not None and s.get("kind") in ("cut", "fill"):
            zc = float(np.interp(co, [p[0] for p in design], [p[1] for p in design]))
            sheet.text(X(co) + (-0.8 if side == "left" else 0.8), Y(zc) + 1.2, f"{'C' if s['kind'] == 'cut' else 'F'} {abs(s.get('height', 0)):.2f}", tiny, layer="BAND_TEXT",
                       align="right" if side == "left" else "left")


def draw_structures(sheet: Sheet, sec: dict, structures, X, Y, t: dict) -> None:
    """Wall bodies and drain channels on one cross-section. The placement is worked out by
    `plm.design.structures.section_structures`, which the browser's cross-section pane also calls,
    so the drawing and the screen cannot disagree."""
    tiny = t.get("tiny", 1.5)
    for item in section_structures(sec, structures):
        pts = [(X(o), Y(z)) for o, z in item["points"]]
        if item["group"] == "wall":
            sheet.poly(pts, "WALL", closed=True, width=0.5)
            sheet.hatch(pts, "WALL", pattern="ANSI31", opacity=0.3)
            if item.get("foundation"):
                sheet.poly([(X(o), Y(z)) for o, z in item["foundation"]], "WALL", closed=True, dashed=True)
            lo, lz = item["label_at"]
            sheet.text(X(lo), Y(lz), item["label"], tiny, 0.0, "WALL", "left" if item["side"] == "right" else "right", "middle")
        else:
            sheet.poly(pts, "DRAIN", width=0.35)
            lo, lz = item["label_at"]
            sheet.text(X(lo), Y(lz), item["label"], tiny, 0.0, "DRAIN", "center", "top")


def section_sheets(inp: DrawingInputs, st: SheetSettings) -> list[Sheet]:
    secs = sorted([trim_section(s) for s in inp.sections if len(s.get("design") or []) >= 2 and len(s.get("ground") or []) >= 2], key=lambda s: s["chainage"])
    if not secs:
        return []
    k = 1000.0 / st.section_scale
    ax0, ay0, ax1, ay1 = st.area
    t = st.text
    head = 12.0
    area_w, area_h = ax1 - ax0 - 4, ay1 - ay0 - head - 2
    # a section too big for the paper at the sheet scale is drawn at the largest scale that fits (noted in its title)
    ks: list[float] = []
    sizes = []
    for s in secs:
        w, h, *_ = _cell_size(s, k)
        kk = k
        if w > area_w or h > area_h:
            kk = k * min(area_w / w, area_h / h) * 0.97
        ks.append(kk)
        sizes.append(_cell_size(s, kk))

    # pack: rows of equal-height cells, sheets of rows
    pages: list[list[tuple[int, float, float]]] = []   # (section index, cx, cy)
    page: list[tuple[int, float, float]] = []
    y_top = ay1 - head
    row: list[int] = []
    row_w = 0.0

    def place_row() -> None:
        nonlocal y_top, page
        if not row:
            return
        rh = max(sizes[j][1] for j in row)
        if y_top - rh < ay0 + 2:
            pages.append(page)
            page = []
            y_top = ay1 - head
        x = ax0 + 2
        for j in row:
            page.append((j, x, y_top - rh))
            x += sizes[j][0]
        y_top -= rh + 2

    for j, (w, h, *_) in enumerate(sizes):
        if row and row_w + w > area_w:
            place_row()
            row, row_w = [], 0.0
        row.append(j)
        row_w += w
    place_row()
    if page:
        pages.append(page)

    sheets: list[Sheet] = []
    for i, cells in enumerate(pages):
        chs = [secs[j]["chainage"] for j, *_ in cells]
        sheet = Sheet("sections", i, f"CROSS SECTIONS CH {format_chainage(min(chs))} TO {format_chainage(max(chs))}", drawing_number(st, "X", i))
        sheet.info = {"start": min(chs), "end": max(chs), "scale": f"1:{st.section_scale:g}", "sections": len(cells)}
        for j, cx, cy in cells:
            w, h, o_min, o_max, datum = sizes[j]
            note = f"   (drawn 1:{1000.0 / ks[j]:.0f} to fit)" if abs(ks[j] - k) > 1e-9 else ""
            _draw_section(sheet, secs[j], cx, cy, ks[j], o_min, datum, t, note, inp.structures)
        sheet.text(ax0 + 4, ay1 - 4, sheet.title, t.get("title", 3.5), layer="TEXT", valign="top", bold=True)
        sheet.text(ax0 + 4, ay1 - 4 - t.get("title", 3.5) * 1.6, f"SCALE 1:{st.section_scale:g} (H = V)   {len(cells)} sections   offsets from the centre line, left negative",
                   t.get("small", 1.8), layer="TEXT", valign="top")
        legend = {"title": "LEGEND", "columns": ["Symbol", "Meaning"], "widths": [0.6, 2.0],
                  "rows": [["GL / DL / OFF", "ground level / design level / offset (m)"], ["hatch ANSI31", "cut area"], ["hatch ANSI37", "fill area"],
                           ["C h / F h", "cut / fill height at the daylight point"], ["WALL layer", "wall body with its foundation (dashed)"],
                           ["DRAIN layer", "drain channel at its offset"]]}
        draw_frame(sheet, st, inp, title=sheet.title, number=sheet.number, scale_text=f"1:{st.section_scale:g}", sheet_no=i + 1, sheet_count=len(pages), table=legend)
        sheets.append(sheet)
    return sheets
