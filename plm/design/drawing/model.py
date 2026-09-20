"""Drawing primitives in sheet millimetres (origin bottom-left, y up)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

# landscape paper sizes in mm
PAPERS: dict[str, tuple[float, float]] = {
    "A4": (297.0, 210.0), "A3": (420.0, 297.0), "A2": (594.0, 420.0), "A1": (841.0, 594.0), "A0": (1189.0, 841.0),
}

# layer -> (AutoCAD colour index, SVG colour, lineweight mm, dashed)
LAYERS: dict[str, tuple[int, str, float]] = {
    "FRAME": (7, "#000000", 0.50),
    "TITLE": (7, "#000000", 0.25),
    "TITLE_TEXT": (7, "#000000", 0.18),
    "NOTE": (7, "#333333", 0.13),
    "GRID": (8, "#b3b3b3", 0.13),
    "CONTOUR": (32, "#c9a27e", 0.13),
    "INDEX_CONTOUR": (30, "#a3653a", 0.25),
    "CONTOUR_TEXT": (30, "#a3653a", 0.13),
    "H_ALIGN": (1, "#d00000", 0.50),
    "TANGENT": (4, "#0077b6", 0.18),
    "IP": (1, "#d00000", 0.25),
    "CHAINAGE": (7, "#000000", 0.18),
    "KEYPOINT": (5, "#1d4ed8", 0.25),
    "FORMATION": (8, "#6b7280", 0.18),
    "DAYLIGHT_CUT": (3, "#15803d", 0.25),
    "DAYLIGHT_FILL": (30, "#c2410c", 0.25),
    "STRUCTURE": (6, "#7e22ce", 0.35),
    "WALL": (6, "#7e22ce", 0.50),
    "DRAIN": (4, "#0e7490", 0.35),
    "CULVERT": (5, "#1d4ed8", 0.50),
    "GROUND": (3, "#15803d", 0.35),
    "DESIGN": (1, "#d00000", 0.50),
    "BAND": (7, "#000000", 0.18),
    "BAND_TEXT": (7, "#000000", 0.13),
    "CUT_HATCH": (3, "#22c55e", 0.13),
    "FILL_HATCH": (30, "#f97316", 0.13),
    "TABLE": (7, "#000000", 0.18),
    "TEXT": (7, "#000000", 0.18),
    "MATCH": (6, "#7e22ce", 0.35),
}


def layer_info(name: str) -> tuple[int, str, float]:
    return LAYERS.get(name, (7, "#000000", 0.18))


@dataclass
class Polyline:
    pts: list[tuple[float, float]]
    layer: str = "TEXT"
    closed: bool = False
    dashed: bool = False
    width: float | None = None      # lineweight override (mm)


@dataclass
class Text:
    x: float
    y: float
    text: str
    height: float = 2.5
    rotation: float = 0.0           # degrees, counter-clockwise
    layer: str = "TEXT"
    align: str = "left"             # left | center | right
    valign: str = "baseline"        # baseline | middle | top | bottom
    bold: bool = False


@dataclass
class Circle:
    x: float
    y: float
    r: float
    layer: str = "TEXT"


@dataclass
class Hatch:
    pts: list[tuple[float, float]]
    layer: str = "CUT_HATCH"
    pattern: str | None = "ANSI31"  # None = solid
    opacity: float = 0.35


Entity = Polyline | Text | Circle | Hatch


@dataclass
class Sheet:
    kind: str                        # plan | profile | sections
    index: int                       # 0-based within its kind
    title: str
    number: str = ""
    paper: tuple[float, float] = (420.0, 297.0)
    entities: list[Entity] = field(default_factory=list)
    info: dict = field(default_factory=dict)   # start / end chainage, scale text, count ...

    # ------------------------------------------------------------------ builders
    def add(self, *ents: Entity) -> None:
        self.entities.extend(ents)

    def line(self, x1: float, y1: float, x2: float, y2: float, layer: str = "TEXT", dashed: bool = False, width: float | None = None) -> None:
        self.entities.append(Polyline([(x1, y1), (x2, y2)], layer, False, dashed, width))

    def poly(self, pts: Iterable[Sequence[float]], layer: str = "TEXT", closed: bool = False, dashed: bool = False, width: float | None = None) -> None:
        p = [(float(a), float(b)) for a, b in pts]
        if len(p) >= 2:
            self.entities.append(Polyline(p, layer, closed, dashed, width))

    def rect(self, x: float, y: float, w: float, h: float, layer: str = "TEXT", width: float | None = None) -> None:
        self.entities.append(Polyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], layer, True, False, width))

    def text(self, x: float, y: float, s: str, height: float = 2.5, rotation: float = 0.0, layer: str = "TEXT",
             align: str = "left", valign: str = "baseline", bold: bool = False) -> None:
        if s is None or s == "":
            return
        self.entities.append(Text(float(x), float(y), str(s), height, rotation, layer, align, valign, bold))

    def circle(self, x: float, y: float, r: float, layer: str = "TEXT") -> None:
        self.entities.append(Circle(float(x), float(y), r, layer))

    def hatch(self, pts: Iterable[Sequence[float]], layer: str, pattern: str | None = "ANSI31", opacity: float = 0.35) -> None:
        p = [(float(a), float(b)) for a, b in pts]
        if len(p) >= 3:
            self.entities.append(Hatch(p, layer, pattern, opacity))

    @property
    def width(self) -> float:
        return self.paper[0]

    @property
    def height(self) -> float:
        return self.paper[1]

    def summary(self) -> dict:
        return {"kind": self.kind, "index": self.index, "title": self.title, "number": self.number, "paper": list(self.paper),
                "entities": len(self.entities), **self.info}


# ---------------------------------------------------------------------- geometry helpers
def clip_polyline(pts: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> list[np.ndarray]:
    """Clip an open polyline to an axis-aligned rectangle; returns the pieces inside."""
    import shapely

    p = np.asarray(pts, float)
    if len(p) < 2:
        return []
    inside = (p[:, 0] >= x0) & (p[:, 0] <= x1) & (p[:, 1] >= y0) & (p[:, 1] <= y1)
    if inside.all():
        return [p]
    if not inside.any():
        # may still cross the window: fall through to shapely
        bx0, by0, bx1, by1 = p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max()
        if bx1 < x0 or bx0 > x1 or by1 < y0 or by0 > y1:
            return []
    g = shapely.clip_by_rect(shapely.LineString(p), x0, y0, x1, y1)
    out = []
    for part in getattr(g, "geoms", [g]):
        if part.is_empty or part.geom_type != "LineString":
            continue
        c = shapely.get_coordinates(part)
        if len(c) >= 2:
            out.append(c)
    return out


def readable(angle_deg: float, align: str = "left") -> tuple[float, str]:
    """Flip text that would be upside down (angles in (90, 270)) and swap its alignment."""
    a = angle_deg % 360.0
    if 90.0 < a <= 270.0:
        a = (a + 180.0) % 360.0
        align = {"left": "right", "right": "left"}.get(align, align)
    return a, align


def nice_step(span: float, target_mm: float, k: float) -> float:
    """Grid step (model units) so that lines are at least `target_mm` apart at `k` mm per unit."""
    raw = target_mm / max(k, 1e-12)
    for s in (0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if s >= raw:
            return float(s)
    return float(raw)
