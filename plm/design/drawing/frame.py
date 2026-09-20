"""Sheet template (frame, title block, notes), settings and shared decorations.

The template is data (`plm/design/sheets/*.json`): paper, margins, a bottom strip holding notes,
a data table (curve / vertical curve data) and the title block whose rows are declared as cells
bound to fields. Per-design settings override paper, scales, intervals and the title block fields.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ...engine.alignment import HorizontalAlignment
from ...engine.contour import ContourLine
from ..superelevation import SuperelevationProfile
from ..vertical import VerticalAlignment
from .model import PAPERS, Sheet

SHEETS_DIR = Path(__file__).resolve().parent.parent / "sheets"


def load_template(template_id: str = "default") -> dict:
    p = SHEETS_DIR / f"{template_id}.json"
    if not p.exists():
        p = SHEETS_DIR / "default.json"
    return json.loads(p.read_text(encoding="utf-8"))


def list_templates() -> list[dict]:
    out = []
    for p in sorted(SHEETS_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({"id": d.get("id", p.stem), "name": d.get("name", p.stem), "paper": d.get("paper", "A3")})
        except Exception:  # noqa: BLE001 - a broken template file must not hide the others
            continue
    return out


TITLE_FIELDS = ("organisation", "project", "road", "drawing_prefix", "drawn_by", "checked_by", "approved_by", "date", "revision")


@dataclass
class SheetSettings:
    template: str = "default"
    paper: str = "A3"
    margins: dict = field(default_factory=lambda: {"left": 20, "right": 10, "top": 10, "bottom": 10})
    strip_height: float = 42.0
    notes_width: float = 78.0
    title_block: dict = field(default_factory=dict)
    plan_scale: float = 1000.0
    profile_h_scale: float = 1000.0
    profile_v_scale: float = 100.0
    section_scale: float = 200.0
    chainage_interval: float = 20.0
    swath: float = 15.0
    rotate: str = "along_road"       # along_road | north_up
    text: dict = field(default_factory=lambda: {"title": 3.5, "normal": 2.5, "small": 1.8, "tiny": 1.5})
    notes: list[str] = field(default_factory=list)
    fields: dict = field(default_factory=dict)   # title block values
    kinds: tuple[str, ...] = ("plan", "profile", "sections")

    # ------------------------------------------------------------------ derived geometry (mm)
    @property
    def size(self) -> tuple[float, float]:
        return PAPERS.get(self.paper.upper(), PAPERS["A3"])

    @property
    def frame(self) -> tuple[float, float, float, float]:
        """Inner border (x0, y0, x1, y1)."""
        w, h = self.size
        m = self.margins
        return float(m["left"]), float(m["bottom"]), w - float(m["right"]), h - float(m["top"])

    @property
    def area(self) -> tuple[float, float, float, float]:
        """Drawing area above the title strip."""
        x0, y0, x1, y1 = self.frame
        return x0, y0 + self.strip_height, x1, y1

    def as_dict(self) -> dict:
        return {"template": self.template, "paper": self.paper, "plan_scale": self.plan_scale, "profile_h_scale": self.profile_h_scale,
                "profile_v_scale": self.profile_v_scale, "section_scale": self.section_scale, "chainage_interval": self.chainage_interval,
                "swath": self.swath, "rotate": self.rotate, "fields": dict(self.fields), "kinds": list(self.kinds), "notes": list(self.notes)}


def resolve_settings(doc: dict | None, *, project_name: str = "", design_name: str = "", organisation: str = "") -> SheetSettings:
    """Template defaults + stored per-design document -> settings."""
    doc = doc or {}
    tpl = load_template(str(doc.get("template") or "default"))
    sc = tpl.get("scales", {})
    st = SheetSettings(
        template=tpl.get("id", "default"), paper=str(doc.get("paper") or tpl.get("paper", "A3")).upper(),
        margins=dict(tpl.get("margins", {"left": 20, "right": 10, "top": 10, "bottom": 10})),
        strip_height=float(tpl.get("strip_height", 42)), notes_width=float(tpl.get("notes_width", 78)),
        title_block=dict(tpl.get("title_block", {})),
        plan_scale=float(doc.get("plan_scale") or sc.get("plan", 1000)), profile_h_scale=float(doc.get("profile_h_scale") or sc.get("profile_h", 1000)),
        profile_v_scale=float(doc.get("profile_v_scale") or sc.get("profile_v", 100)), section_scale=float(doc.get("section_scale") or sc.get("section", 200)),
        chainage_interval=float(doc.get("chainage_interval") or tpl.get("chainage_interval", 20)), swath=float(doc.get("swath") or tpl.get("swath", 15)),
        rotate=str(doc.get("rotate") or tpl.get("rotate", "along_road")), text=dict(tpl.get("text", {})),
        notes=list(doc.get("notes") or tpl.get("notes", [])),
    )
    if st.paper not in PAPERS:
        st.paper = "A3"
    f = {k: "" for k in TITLE_FIELDS}
    f.update({"organisation": organisation, "project": project_name, "road": design_name, "drawing_prefix": "RD", "date": date.today().isoformat(), "revision": "A"})
    f.update({k: str(v) for k, v in (doc.get("fields") or {}).items() if k in TITLE_FIELDS and v is not None})
    st.fields = f
    kinds = doc.get("kinds")
    if kinds:
        st.kinds = tuple(k for k in ("plan", "profile", "sections") if k in kinds) or st.kinds
    return st


# ---------------------------------------------------------------------- inputs
@dataclass
class DrawingInputs:
    """Everything the sheet builders read; produced by the API layer from the stores."""
    project_name: str
    design_name: str
    al: HorizontalAlignment
    curve_rows: list[dict]
    va: VerticalAlignment | None = None
    ground: list[tuple[float, float]] = field(default_factory=list)      # (chainage, z)
    sections: list[dict] = field(default_factory=list)                   # full corridor sections
    volumes: list[dict] = field(default_factory=list)
    mass_haul: list[dict] = field(default_factory=list)
    totals: dict = field(default_factory=dict)
    structures: list[dict] = field(default_factory=list)
    contours: list[ContourLine] = field(default_factory=list)
    supere: SuperelevationProfile | None = None
    standard: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    checks: list[dict] = field(default_factory=list)
    standard_params: list[dict] = field(default_factory=list)
    templates: dict = field(default_factory=dict)
    crs_name: str = ""
    corridor_params: dict = field(default_factory=dict)

    def ground_z(self, ch: np.ndarray | float) -> np.ndarray:
        if not self.ground:
            return np.full(np.shape(ch), np.nan)
        g = np.asarray(self.ground, float)
        return np.interp(ch, g[:, 0], g[:, 1], left=np.nan, right=np.nan)


# ---------------------------------------------------------------------- frame and title block
def _std_note(inp: DrawingInputs) -> str:
    sid = inp.standard.get("name") or inp.standard.get("id") or "-"
    status = inp.standard.get("status", "placeholder")
    ctx = inp.context
    s = f"Design standard: {sid}; class {ctx.get('road_class', '-')}, {ctx.get('terrain', '-')} terrain, V = {ctx.get('design_speed', '-')} km/h."
    if status != "verified":
        s += " Standard values are PLACEHOLDERS - verify before use."
    return s


def wrap(text: str, width_mm: float, height: float) -> list[str]:
    """Greedy word wrap using ~0.75 * height per character."""
    per = max(int(width_mm / (0.75 * height)), 8)
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > per and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def draw_frame(sheet: Sheet, st: SheetSettings, inp: DrawingInputs, *, title: str, number: str, scale_text: str, sheet_no: int, sheet_count: int,
               table: dict | None = None) -> None:
    """Border, bottom strip with notes, an optional data table, and the title block."""
    W, H = st.size
    sheet.paper = (W, H)
    x0, y0, x1, y1 = st.frame
    t = st.text
    sheet.rect(0.5, 0.5, W - 1.0, H - 1.0, "FRAME", width=0.25)         # paper edge helper (trim line)
    sheet.rect(x0, y0, x1 - x0, y1 - y0, "FRAME")
    ys = y0 + st.strip_height
    sheet.line(x0, ys, x1, ys, "FRAME")

    # -- title block (right part of the strip)
    tb = st.title_block
    tbw = min(float(tb.get("width", 190)), (x1 - x0) * 0.6)
    tx0 = x1 - tbw
    sheet.line(tx0, y0, tx0, ys, "TITLE")
    rows = tb.get("rows") or []
    rh = st.strip_height / max(len(rows), 1)
    values = {**st.fields, "title": title, "number": number, "scale": scale_text, "sheet": f"{sheet_no} of {sheet_count}",
              "project": st.fields.get("project") or inp.project_name}
    if st.fields.get("road"):
        values["title"] = f"{st.fields['road']} - {title}"
    for r, cells in enumerate(rows):
        yr1 = ys - r * rh
        yr0 = yr1 - rh
        if r:
            sheet.line(tx0, yr1, x1, yr1, "TITLE")
        spans = sum(int(c.get("span", 1)) for c in cells) or 1
        cx = tx0
        for c in cells:
            cw = tbw * int(c.get("span", 1)) / spans
            if cx > tx0 + 1e-6:
                sheet.line(cx, yr0, cx, yr1, "TITLE")
            sheet.text(cx + 1.5, yr1 - 1.0, str(c.get("label", "")).upper(), t.get("tiny", 1.5), layer="TITLE_TEXT", valign="top")
            val = str(values.get(c.get("field", ""), "") or "")
            h = t.get("normal", 2.5) if c.get("field") not in ("title", "project") else t.get("normal", 2.5)
            # shrink long values to fit the cell width
            while h > 1.2 and len(val) * 0.72 * h > cw - 3:
                h -= 0.2
            sheet.text(cx + 1.5, yr0 + 1.2, val, h, layer="TITLE_TEXT", bold=c.get("field") in ("title", "project"))
            cx += cw

    # -- notes (left part of the strip)
    nx0 = x0
    nw = min(st.notes_width, tx0 - x0)
    sheet.line(nx0 + nw, y0, nx0 + nw, ys, "TITLE")
    hs = t.get("small", 1.8)
    yy = ys - 1.5
    sheet.text(nx0 + 1.5, yy, "NOTES", hs, layer="NOTE", valign="top", bold=True)
    yy -= hs * 1.9
    notes = list(st.notes) + [_std_note(inp)]
    for i, n in enumerate(notes):
        for ln in wrap(f"{i + 1}. {n}", nw - 3, hs * 0.9):
            if yy < y0 + hs:
                break
            sheet.text(nx0 + 1.5, yy, ln, hs * 0.9, layer="NOTE", valign="top")
            yy -= hs * 1.45

    # -- data table (middle part of the strip)
    if table and tx0 - (nx0 + nw) > 40:
        draw_table(sheet, nx0 + nw, y0, tx0 - (nx0 + nw), st.strip_height, table, t)


def draw_table(sheet: Sheet, x: float, y: float, w: float, h: float, table: dict, t: dict) -> None:
    """A boxed table: {"title", "columns": [names], "rows": [[values]], "widths": [relative]}."""
    cols: list[str] = table["columns"]
    rows: list[list[Any]] = table.get("rows", [])
    widths = table.get("widths") or [1.0] * len(cols)
    tot = float(sum(widths))
    hs = t.get("tiny", 1.5)
    title_h = 4.0 if table.get("title") else 0.0
    row_h = min(4.0, max(2.6, (h - title_h - 1.0) / (len(rows) + 1))) if rows else 4.0
    max_rows = max(int((h - title_h - 1.0) / row_h) - 1, 1)
    if table.get("title"):
        sheet.text(x + 1.5, y + h - 1.2, str(table["title"]), t.get("small", 1.8), layer="TABLE", valign="top", bold=True)
    top = y + h - title_h - 0.5
    # header
    cx = x
    for name, wf in zip(cols, widths):
        cw = w * wf / tot
        sheet.text(cx + cw / 2, top - row_h / 2, str(name), hs, layer="TABLE", align="center", valign="middle", bold=True)
        cx += cw
    sheet.line(x, top, x + w, top, "TABLE")
    sheet.line(x, top - row_h, x + w, top - row_h, "TABLE")
    shown = rows[:max_rows]
    for r, row in enumerate(shown):
        yr = top - row_h * (r + 1)
        cx = x
        for val, wf in zip(row, widths):
            cw = w * wf / tot
            sheet.text(cx + cw / 2, yr - row_h / 2, "" if val is None else str(val), hs, layer="TABLE", align="center", valign="middle")
            cx += cw
        sheet.line(x, yr - row_h, x + w, yr - row_h, "TABLE")
    if len(rows) > len(shown):
        sheet.text(x + 1.5, top - row_h * (len(shown) + 1) - 0.5, f"... {len(rows) - len(shown)} more (see the Excel export)", hs, layer="TABLE", valign="top")
    # column separators
    cx = x
    for wf in widths[:-1]:
        cx += w * wf / tot
        sheet.line(cx, top, cx, top - row_h * (len(shown) + 1), "TABLE")


def north_arrow(sheet: Sheet, x: float, y: float, north_deg: float, size: float = 14.0) -> None:
    """Arrow pointing to `north_deg` (sheet angle in degrees, 90 = up)."""
    a = np.radians(north_deg)
    ux, uy = np.cos(a), np.sin(a)
    px, py = -uy, ux
    tip = (x + ux * size, y + uy * size)
    base_l = (x + px * size * 0.22, y + py * size * 0.22)
    base_r = (x - px * size * 0.22, y - py * size * 0.22)
    sheet.poly([base_l, tip, base_r, (x, y)], "TEXT", closed=True, width=0.35)
    sheet.hatch([base_l, tip, (x, y)], "TEXT", pattern=None, opacity=1.0)
    sheet.text(tip[0] + ux * 3.5, tip[1] + uy * 3.5, "N", 3.5, layer="TEXT", align="center", valign="middle", bold=True)


def scale_bar(sheet: Sheet, x: float, y: float, scale: float, length_m: float | None = None, t: dict | None = None) -> None:
    k = 1000.0 / scale
    L = length_m or nice_len(60.0 / k)
    n = 4
    seg = L * k / n
    for i in range(n):
        sheet.rect(x + i * seg, y, seg, 1.6, "TEXT")
        if i % 2 == 0:
            sheet.hatch([(x + i * seg, y), (x + (i + 1) * seg, y), (x + (i + 1) * seg, y + 1.6), (x + i * seg, y + 1.6)], "TEXT", pattern=None, opacity=1.0)
        sheet.text(x + i * seg, y - 0.8, f"{L * i / n:g}", (t or {}).get("tiny", 1.5), layer="TEXT", align="center", valign="top")
    sheet.text(x + L * k, y - 0.8, f"{L:g} m", (t or {}).get("tiny", 1.5), layer="TEXT", align="center", valign="top")
    sheet.text(x, y + 2.6, f"SCALE 1:{scale:g}", (t or {}).get("small", 1.8), layer="TEXT")


def nice_len(raw: float) -> float:
    for s in (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000):
        if s >= raw:
            return float(s)
    return float(round(raw, -3))


def drawing_number(st: SheetSettings, code: str, i: int) -> str:
    return f"{st.fields.get('drawing_prefix') or 'RD'}-{code}-{i + 1:02d}"


def chainage_stations(s0: float, s1: float, interval: float, extra: Sequence[float] = ()) -> np.ndarray:
    first = np.ceil(s0 / interval) * interval
    chs = list(np.arange(first, s1 + 1e-6, interval)) + [s0, s1] + [e for e in extra if s0 - 1e-6 <= e <= s1 + 1e-6]
    return np.unique(np.round(np.asarray(chs, float), 4))
