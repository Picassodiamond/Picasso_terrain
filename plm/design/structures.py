"""Structures along the road: retaining, breast, toe and catch walls, culverts and drains.

Three layers:

* **catalogue** - the kinds of structure (`STRUCTURE_KINDS`), the wall types (`WALL_TYPES`) and the
  drain types (`DRAIN_TYPES`) with their usual geometry, the heights or gradients they suit and the
  materials they consume. Everything a user can choose is data, so a new wall type is one entry.
* **geometry and quantities** - `wall_section()` and `drain_section()` return the cross-section
  polygon (drawn on the section sheets) and the area, from which `quantities()` works out the
  material per metre run and per structure.
* **suggestions and checks** - where the corridor and the terrain ask for a structure (high fills,
  slopes that do not catch, stream crossings, sag points, side-drain outlets) and whether what the
  user chose agrees with the standard (NRS 2070 cl. 13.8 and Table 13-3 for drains, cl. 17.1 for
  culvert spans, and the height ranges of each wall type).

Structural design of an individual element (stability, reinforcement, hydraulic sizing) plugs in
behind these records; nothing here claims to design a wall.
"""
from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np
import shapely

from ..engine.alignment import HorizontalAlignment, format_chainage
from .standards import Check, Standard

# ---------------------------------------------------------------------- wall types
# max_height: the height up to which the type is normally used without a special design.
# top_width / base_ratio / batter: gravity-wall proportions; base width = max(top, base_ratio x H).
# materials: quantity per metre run derived from the section area (see `quantities`).
WALL_TYPES: dict[str, dict] = {
    "dry_stone": {
        "label": "Dry stone masonry", "family": "gravity", "max_height": 3.0, "top_width": 0.6, "base_ratio": 0.5, "batter": 0.15,
        "foundation_depth": 0.5, "materials": {"stone_masonry_dry": 1.0}, "drainage": "free draining",
        "note": "Hand-packed stone without mortar. Cheap and flexible, used on low cut and fill slopes; keep below about 3 m.",
    },
    "stone_masonry": {
        "label": "Stone masonry in cement mortar", "family": "gravity", "max_height": 6.0, "top_width": 0.6, "base_ratio": 0.5, "batter": 0.15,
        "foundation_depth": 0.8, "materials": {"stone_masonry_cement": 1.0}, "weep_holes": True, "drainage": "weep holes with granular backfill",
        "note": "Random rubble gravity wall, the usual hill-road retaining wall. Weep holes and a granular back drain are required.",
    },
    "gabion": {
        "label": "Gabion (stepped wire crates)", "family": "flexible", "max_height": 8.0, "top_width": 1.0, "base_ratio": 0.6, "batter": 0.0,
        "foundation_depth": 0.5, "step": 1.0, "materials": {"gabion": 1.0, "geotextile": 1.0}, "drainage": "free draining",
        "note": "1 x 1 m crates stepped back one box per course. Tolerates settlement and drains freely, the standard choice on weak hill foundations.",
    },
    "concrete_gravity": {
        "label": "Mass concrete gravity", "family": "gravity", "max_height": 6.0, "top_width": 0.5, "base_ratio": 0.45, "batter": 0.1,
        "foundation_depth": 1.0, "materials": {"concrete": 1.0}, "weep_holes": True, "drainage": "weep holes with granular backfill",
        "note": "Plain concrete gravity section where stone is scarce or the wall is submerged.",
    },
    "rcc_cantilever": {
        "label": "RCC cantilever (stem and base slab)", "family": "cantilever", "min_height": 3.0, "max_height": 8.0, "top_width": 0.3, "base_ratio": 0.6,
        "batter": 0.0, "foundation_depth": 1.0, "stem_base": 0.45, "slab_thickness": 0.45, "steel_rate": 90.0,
        "materials": {"concrete": 1.0, "steel": 1.0}, "weep_holes": True, "drainage": "weep holes with granular backfill",
        "note": "Reinforced stem on a base slab: far less material than a gravity wall above about 4 m, but needs formwork and steel.",
    },
    "rcc_counterfort": {
        "label": "RCC counterfort", "family": "cantilever", "min_height": 7.0, "max_height": 12.0, "top_width": 0.3, "base_ratio": 0.65,
        "batter": 0.0, "foundation_depth": 1.2, "stem_base": 0.4, "slab_thickness": 0.6, "steel_rate": 110.0,
        "materials": {"concrete": 1.0, "steel": 1.0}, "weep_holes": True, "drainage": "weep holes with granular backfill",
        "note": "Counterforts at 3 to 4 m centres tie the stem to the base slab; for high walls where a cantilever becomes uneconomic.",
    },
    "crib": {
        "label": "Concrete crib", "family": "flexible", "max_height": 6.0, "top_width": 1.2, "base_ratio": 0.6, "batter": 0.2,
        "foundation_depth": 0.6, "crib_fraction": 0.35, "materials": {"concrete": 1.0, "granular_fill": 1.0}, "drainage": "free draining",
        "note": "Interlocking precast units filled with granular material. Quick to build, tolerates movement.",
    },
    "reinforced_soil": {
        "label": "Reinforced soil (geogrid)", "family": "flexible", "min_height": 3.0, "max_height": 12.0, "top_width": 1.0, "base_ratio": 0.7,
        "batter": 0.1, "foundation_depth": 0.5, "layer_spacing": 0.6, "materials": {"granular_fill": 1.0, "geogrid": 1.0, "facing": 1.0},
        "drainage": "free draining", "note": "Compacted fill reinforced with geogrid layers behind a gabion or block facing; economic for long high fills.",
    },
}

# ---------------------------------------------------------------------- drain types
# shape: trapezoid | rectangle | v | dish | stepped | pipe. lining: earth | turf | pitching | masonry | concrete.
# nrs_lining_class: 0 unlined, 1 turfed, 2 hard lining, 3 stepped (NRS 2070 Table 13-3).
DRAIN_TYPES: dict[str, dict] = {
    "earth_trapezoid": {"label": "Earthen trapezoidal", "shape": "trapezoid", "lining": "earth", "nrs_lining_class": 0, "side_slope": 1.0,
                        "note": "Unlined trapezoidal channel. NRS 2070 prefers the trapezoidal shape; only for flat gradients in stable soil."},
    "turfed_trapezoid": {"label": "Grass-turfed trapezoidal", "shape": "trapezoid", "lining": "turf", "nrs_lining_class": 1, "side_slope": 1.0,
                         "lining_thickness": 0.05, "note": "Turfed channel for gentle gradients (NRS 2070 Table 13-3)."},
    "pitched_trapezoid": {"label": "Stone-pitched trapezoidal", "shape": "trapezoid", "lining": "pitching", "nrs_lining_class": 2, "side_slope": 0.5,
                          "lining_thickness": 0.23, "note": "Dry or grouted stone pitching (rip-rap) on the bed and sides."},
    "masonry_trapezoid": {"label": "Stone masonry trapezoidal", "shape": "trapezoid", "lining": "masonry", "nrs_lining_class": 2, "side_slope": 0.5,
                          "lining_thickness": 0.23, "note": "Masonry-lined trapezoidal drain, the usual lined hill-road side drain."},
    "masonry_rect": {"label": "Stone masonry rectangular", "shape": "rectangle", "lining": "masonry", "nrs_lining_class": 2, "lining_thickness": 0.23,
                     "note": "Rectangular masonry drain where space at the toe of the cut is tight."},
    "concrete_rect": {"label": "RCC rectangular", "shape": "rectangle", "lining": "concrete", "nrs_lining_class": 2, "lining_thickness": 0.15,
                      "cover": True, "note": "Concrete channel, coverable with slabs through settlements and at bus bays."},
    "concrete_v": {"label": "Concrete V-shape", "shape": "v", "lining": "concrete", "nrs_lining_class": 2, "side_slope": 1.5, "lining_thickness": 0.12,
                   "note": "Shallow V channel used as a shoulder or median drain."},
    "dish": {"label": "Dished (saucer) drain", "shape": "dish", "lining": "concrete", "nrs_lining_class": 2, "lining_thickness": 0.12,
             "note": "Shallow saucer drain, safe for vehicles to cross at accesses."},
    "stepped_masonry": {"label": "Stepped masonry (cascade)", "shape": "stepped", "lining": "masonry", "nrs_lining_class": 3, "lining_thickness": 0.3,
                        "step_height": 0.3, "note": "Stepped channel that dissipates energy on gradients above 5 % (NRS 2070 Table 13-3)."},
    "perforated_pipe": {"label": "Perforated pipe in filter", "shape": "pipe", "lining": "pipe", "nrs_lining_class": 2, "diameter": 0.15,
                        "note": "150-200 mm perforated pipe wrapped in filter material and geotextile (NRS 2070 cl. 13.8 subsurface drainage)."},
}

CULVERT_TYPES: dict[str, dict] = {
    "pipe": {"label": "Hume pipe culvert", "min_span": 0.6, "max_span": 1.2, "note": "RCC pipe (NP2 / NP3) with masonry headwalls; the usual small cross drain."},
    "box": {"label": "RCC box culvert", "min_span": 1.0, "max_span": 6.0, "note": "Single or multiple cell box, used where the fill is shallow."},
    "slab": {"label": "RCC slab on masonry abutments", "min_span": 1.0, "max_span": 6.0, "note": "Slab culvert on masonry or concrete abutments."},
    "arch": {"label": "Masonry arch", "min_span": 1.0, "max_span": 6.0, "note": "Stone arch culvert, economic where stone and masons are local."},
    "causeway": {"label": "Causeway (simple)", "min_span": 0.0, "max_span": 0.0, "note": "Road dips through the stream bed; for wide seasonal streams on low-traffic roads."},
    "vented_causeway": {"label": "Vented causeway", "min_span": 0.6, "max_span": 1.2, "note": "Causeway with vents for the dry-weather flow."},
}

_WALL_KINDS = ("retaining_wall", "breast_wall", "toe_wall", "catch_wall")
_DRAIN_KINDS = ("side_drain", "catch_drain", "toe_drain", "chute", "subsurface_drain")
_CROSS_KINDS = ("culvert", "cross_drain")

STRUCTURE_KINDS: dict[str, dict] = {
    "retaining_wall": {
        "label": "Retaining wall (fill side)", "group": "wall", "sided": True, "types": list(WALL_TYPES), "default_type": "gabion",
        "params": {"height": 0.0, "type": "gabion", "foundation_depth": None, "batter": None},
        "note": "Carries the fill on the valley side so the embankment need not run out to a natural catch point.",
    },
    "breast_wall": {
        "label": "Breast wall (cut side)", "group": "wall", "sided": True, "types": ["dry_stone", "stone_masonry", "gabion", "concrete_gravity", "rcc_cantilever"],
        "default_type": "stone_masonry", "params": {"height": 0.0, "type": "stone_masonry", "foundation_depth": None, "batter": None},
        "note": "Holds the face of a cut so the slope can be steepened and the cut volume reduced.",
    },
    "toe_wall": {
        "label": "Toe wall", "group": "wall", "sided": True, "types": ["dry_stone", "stone_masonry", "gabion", "concrete_gravity"], "default_type": "stone_masonry",
        "params": {"height": 1.0, "type": "stone_masonry", "foundation_depth": None, "batter": None},
        "note": "Low wall at the toe of a fill slope that stops the toe spreading and protects it from scour.",
    },
    "catch_wall": {
        "label": "Catch wall (debris)", "group": "wall", "sided": True, "types": ["dry_stone", "stone_masonry", "gabion"], "default_type": "gabion",
        "params": {"height": 1.5, "type": "gabion", "foundation_depth": None, "batter": None},
        "note": "Wall at the toe of a cut slope that catches falling debris before it reaches the carriageway.",
    },
    "culvert": {
        "label": "Culvert", "group": "cross", "sided": False, "types": list(CULVERT_TYPES), "default_type": "pipe",
        "params": {"span": 0.9, "type": "pipe", "cells": 1, "skew_deg": 0.0, "headwall": "masonry"},
        "note": "Cross drainage under the road. Structures longer than 6 m are bridges (NRS 2070 cl. 17.1 a).",
    },
    "cross_drain": {
        "label": "Cross drain / relief culvert", "group": "cross", "sided": False, "types": ["pipe", "slab", "box"], "default_type": "pipe",
        "params": {"span": 0.6, "type": "pipe", "cells": 1, "skew_deg": 0.0, "headwall": "masonry"},
        "note": "Relief culvert that takes the side drain across the road; NRS 2070 cl. 13.8 i asks for an outlet at most every 500 m.",
    },
    "side_drain": {
        "label": "Side drain (longitudinal)", "group": "drain", "sided": True, "types": list(DRAIN_TYPES), "default_type": "masonry_trapezoid",
        "params": {"width": 0.6, "depth": 0.5, "type": "masonry_trapezoid", "gradient": None},
        "note": "Longitudinal drain in cut sections. The ditch shape used for earthworks belongs to the template; this record carries its type and lining.",
    },
    "catch_drain": {
        "label": "Catch water drain (above cut)", "group": "drain", "sided": True, "types": list(DRAIN_TYPES), "default_type": "masonry_trapezoid",
        "params": {"width": 0.6, "depth": 0.5, "type": "masonry_trapezoid", "offset": 5.0, "gradient": None},
        "note": "Intercepting drain behind the top of the cut, at least 5 m from the cut edge (NRS 2070 cl. 13.8 d).",
    },
    "toe_drain": {
        "label": "Toe drain (below fill)", "group": "drain", "sided": True, "types": list(DRAIN_TYPES), "default_type": "earth_trapezoid",
        "params": {"width": 0.5, "depth": 0.4, "type": "earth_trapezoid", "gradient": None},
        "note": "Drain at the toe of low fills that carries water away to a water course (NRS 2070 cl. 13.8 c).",
    },
    "chute": {
        "label": "Chute / cascade", "group": "drain", "sided": True, "types": ["stepped_masonry", "masonry_rect", "concrete_rect", "pitched_trapezoid"],
        "default_type": "stepped_masonry", "params": {"width": 0.5, "depth": 0.4, "type": "stepped_masonry", "gradient": None},
        "note": "Carries collected water down a deep cut or a high fill slope (NRS 2070 cl. 13.8 e).",
    },
    "subsurface_drain": {
        "label": "Subsurface drain", "group": "drain", "sided": True, "types": ["perforated_pipe"], "default_type": "perforated_pipe",
        "params": {"width": 0.6, "depth": 1.2, "type": "perforated_pipe", "gradient": None},
        "note": "Perforated pipe in a filter trench where the water table is within 1.0 to 1.2 m of the subgrade.",
    },
}

# material labels and units for the bill of quantities
MATERIALS: dict[str, dict] = {
    "stone_masonry_dry": {"label": "Dry stone masonry", "unit": "m3"},
    "stone_masonry_cement": {"label": "Stone masonry in cement mortar", "unit": "m3"},
    "gabion": {"label": "Gabion crates filled", "unit": "m3"},
    "concrete": {"label": "Concrete", "unit": "m3"},
    "steel": {"label": "Reinforcement steel", "unit": "kg"},
    "granular_fill": {"label": "Granular fill", "unit": "m3"},
    "geotextile": {"label": "Geotextile", "unit": "m2"},
    "geogrid": {"label": "Geogrid", "unit": "m2"},
    "facing": {"label": "Facing units", "unit": "m2"},
    "excavation": {"label": "Structural excavation", "unit": "m3"},
    "lining": {"label": "Drain lining", "unit": "m2"},
    "turf": {"label": "Grass turfing", "unit": "m2"},
    "pipe": {"label": "Pipe", "unit": "m"},
}


def catalogue() -> dict:
    """Everything the UI needs to offer structures, their types and their fields."""
    return {
        "kinds": {k: {**v, "type_labels": {t: (WALL_TYPES | DRAIN_TYPES | CULVERT_TYPES)[t]["label"] for t in v["types"]}} for k, v in STRUCTURE_KINDS.items()},
        "wall_types": WALL_TYPES, "drain_types": DRAIN_TYPES, "culvert_types": CULVERT_TYPES, "materials": MATERIALS,
        "groups": {"wall": _WALL_KINDS, "cross": _CROSS_KINDS, "drain": _DRAIN_KINDS},
    }


def type_spec(kind: str, type_id: str) -> dict:
    table = WALL_TYPES if kind in _WALL_KINDS else (CULVERT_TYPES if kind in _CROSS_KINDS else DRAIN_TYPES)
    return table.get(type_id) or table[STRUCTURE_KINDS[kind]["default_type"]]


# ---------------------------------------------------------------------- records
def normalise_structure(d: dict, next_id: int) -> dict:
    kind = str(d.get("kind", "retaining_wall"))
    if kind not in STRUCTURE_KINDS:
        raise ValueError(f"unknown structure kind {kind!r}; known: {sorted(STRUCTURE_KINDS)}")
    spec = STRUCTURE_KINDS[kind]
    frm = float(d.get("from", d.get("chainage", 0.0)))
    to = float(d.get("to", frm))
    if to < frm:
        frm, to = to, frm
    params = {**spec["params"], **(d.get("params") or {})}
    t = str(params.get("type") or spec["default_type"])
    if t not in spec["types"]:
        raise ValueError(f"{kind}: unknown type {t!r}; allowed: {', '.join(spec['types'])}")
    params["type"] = t
    return {"id": int(d.get("id") or next_id), "kind": kind, "side": d.get("side") if spec["sided"] else None,
            "from": frm, "to": to, "params": params, "source": str(d.get("source") or "manual"), "note": str(d.get("note") or "")}


# ---------------------------------------------------------------------- geometry and quantities
def wall_section(type_id: str, height: float, foundation_depth: float | None = None, batter: float | None = None) -> dict:
    """Cross-section of a wall, in metres, as a polygon in (x outward from the face, y up from the
    foundation base). Returns the polygon, the area of the wall body and the foundation block."""
    spec = WALL_TYPES.get(type_id) or WALL_TYPES["gabion"]
    H = max(float(height), 0.1)
    fd = float(spec.get("foundation_depth", 0.5) if foundation_depth is None else foundation_depth)
    bat = float(spec.get("batter", 0.1) if batter is None else batter)
    top = float(spec.get("top_width", 0.6))
    base = max(top, float(spec.get("base_ratio", 0.5)) * H)
    pts: list[tuple[float, float]] = []
    if spec.get("step"):                       # gabion: stepped courses, the back steps in one box per course
        step = float(spec["step"])
        n = max(1, int(np.ceil(H / step)))
        widths = [base if n == 1 else base - (base - top) * i / (n - 1) for i in range(n)]
        pts = [(0.0, 0.0), (0.0, H)]
        area = 0.0
        for i in range(n - 1, -1, -1):
            y_bot, y_top = i * step, min((i + 1) * step, H)
            pts.append((widths[i], y_top))
            pts.append((widths[i], y_bot))
            area += widths[i] * (y_top - y_bot)
    elif spec.get("family") == "cantilever":   # stem + base slab
        stem_top, stem_base = top, float(spec.get("stem_base", 0.45))
        th = float(spec.get("slab_thickness", 0.45))
        stem_h = max(H - th, 0.1)
        pts = [(0.0, 0.0), (0.0, H), (stem_top, H), (stem_base, th), (base, th), (base, 0.0)]
        area = (stem_top + stem_base) / 2 * stem_h + base * th
    else:                                      # gravity trapezoid with a battered face
        pts = [(0.0, 0.0), (bat * H, H), (bat * H + top, H), (base, 0.0)]
        area = (top + base) / 2 * H
    foundation = {"width": round(base + 0.2, 3), "depth": round(fd, 3), "area": round((base + 0.2) * fd, 3)}
    return {"type": type_id, "height": round(H, 3), "points": [(round(x, 3), round(y, 3)) for x, y in pts],
            "area": round(float(area), 3), "base_width": round(base, 3), "top_width": round(top, 3), "batter": bat, "foundation": foundation}


def drain_section(type_id: str, width: float, depth: float) -> dict:
    """Cross-section of a drain: polygon of the excavated channel (x from the channel centre, y up
    from the invert), flow area, wetted perimeter and the excavation area."""
    spec = DRAIN_TYPES.get(type_id) or DRAIN_TYPES["earth_trapezoid"]
    b, d = max(float(width), 0.1), max(float(depth), 0.1)
    shape = spec.get("shape", "trapezoid")
    z = float(spec.get("side_slope", 1.0))
    th = float(spec.get("lining_thickness", 0.0))
    if shape == "pipe":
        dia = float(spec.get("diameter", 0.15))
        trench_w, trench_d = max(b, dia + 0.3), d
        pts = [(-trench_w / 2, trench_d), (-trench_w / 2, 0.0), (trench_w / 2, 0.0), (trench_w / 2, trench_d)]
        return {"type": type_id, "shape": shape, "points": pts, "flow_area": round(np.pi * dia ** 2 / 4, 4), "perimeter": round(np.pi * dia, 3),
                "excavation": round(trench_w * trench_d, 3), "lining_thickness": 0.0, "top_width": round(trench_w, 3), "diameter": dia}
    if shape == "rectangle":
        pts = [(-b / 2, d), (-b / 2, 0.0), (b / 2, 0.0), (b / 2, d)]
        area, per, top_w = b * d, b + 2 * d, b
    elif shape == "v":
        pts = [(-z * d, d), (0.0, 0.0), (z * d, d)]
        area, per, top_w = z * d * d, 2 * d * np.hypot(1.0, z), 2 * z * d
    elif shape == "dish":
        xs = np.linspace(-b / 2, b / 2, 9)
        pts = [(float(x), float(d * (2 * x / b) ** 2)) for x in xs]
        area, per, top_w = 2.0 / 3.0 * b * d, b * 1.2, b
    elif shape == "stepped":
        sh = float(spec.get("step_height", 0.3))
        n = max(1, int(round(d / sh)))
        pts = [(-b / 2, d), (-b / 2, 0.0)] + [(b / 2, 0.0), (b / 2, d)]
        area, per, top_w = b * d, b + 2 * d + n * sh, b
    else:                                      # trapezoid (NRS 2070 cl. 13.8 g: preferred shape)
        pts = [(-(b / 2 + z * d), d), (-b / 2, 0.0), (b / 2, 0.0), (b / 2 + z * d, d)]
        area, per, top_w = (b + z * d) * d, b + 2 * d * np.hypot(1.0, z), b + 2 * z * d
    exc = area + th * per
    return {"type": type_id, "shape": shape, "points": [(round(x, 3), round(y, 3)) for x, y in pts], "flow_area": round(float(area), 4),
            "perimeter": round(float(per), 3), "excavation": round(float(exc), 3), "lining_thickness": th, "top_width": round(float(top_w), 3)}


def quantities(s: dict) -> dict:
    """Material quantities of one structure: per metre run and for its whole length."""
    kind, p = s["kind"], s.get("params") or {}
    length = max(float(s["to"]) - float(s["from"]), 0.0)
    sides = 2 if (s.get("side") == "both") else 1
    per: dict[str, float] = {}
    detail: dict = {}
    if kind in _WALL_KINDS:
        sec = wall_section(str(p.get("type")), float(p.get("height") or 0), p.get("foundation_depth"), p.get("batter"))
        spec = WALL_TYPES.get(str(p.get("type"))) or {}
        a = sec["area"]
        if spec.get("family") == "cantilever":
            per["concrete"] = a
            per["steel"] = a * float(spec.get("steel_rate", 90.0))
        elif "crib_fraction" in spec:
            per["concrete"] = a * float(spec["crib_fraction"])
            per["granular_fill"] = a * (1 - float(spec["crib_fraction"]))
        elif str(p.get("type")) == "gabion":
            per["gabion"] = a
            per["geotextile"] = sec["height"] * 1.1
        elif str(p.get("type")) == "reinforced_soil":
            per["granular_fill"] = a
            per["geogrid"] = sec["base_width"] * max(1, int(sec["height"] / float(spec.get("layer_spacing", 0.6))))
            per["facing"] = sec["height"] * 1.05
        else:
            per[next(iter(spec.get("materials", {"stone_masonry_cement": 1.0})))] = a
        per["excavation"] = sec["foundation"]["area"]
        detail = {"section_area": a, "base_width": sec["base_width"], "foundation": sec["foundation"]}
    elif kind in _DRAIN_KINDS:
        sec = drain_section(str(p.get("type")), float(p.get("width") or 0.5), float(p.get("depth") or 0.4))
        spec = DRAIN_TYPES.get(str(p.get("type"))) or {}
        per["excavation"] = sec["excavation"]
        if sec["shape"] == "pipe":
            per["pipe"] = 1.0
            per["granular_fill"] = sec["excavation"]
        elif spec.get("lining") == "turf":
            per["turf"] = sec["perimeter"]
        elif spec.get("lining") in ("masonry", "pitching"):
            per["stone_masonry_cement" if spec.get("lining") == "masonry" else "stone_masonry_dry"] = sec["perimeter"] * sec["lining_thickness"]
            per["lining"] = sec["perimeter"]
        elif spec.get("lining") == "concrete":
            per["concrete"] = sec["perimeter"] * sec["lining_thickness"]
            per["lining"] = sec["perimeter"]
        detail = {"flow_area": sec["flow_area"], "perimeter": sec["perimeter"], "shape": sec["shape"]}
    else:                                       # culverts: counted, with a barrel length across the road
        span = float(p.get("span") or 0.9)
        cells = max(1, int(p.get("cells") or 1))
        barrel = float(p.get("barrel_length") or 0.0)
        detail = {"span": span, "cells": cells, "barrel_length": barrel}
        return {"id": s.get("id"), "kind": kind, "type": p.get("type"), "length": round(length, 2), "sides": 1, "count": cells,
                "per_metre": {}, "total": {"pipe": round(barrel * cells, 2)} if barrel else {}, "detail": detail}
    total = {k: round(v * length * sides, 3) for k, v in per.items()}
    return {"id": s.get("id"), "kind": kind, "type": p.get("type"), "length": round(length, 2), "sides": sides, "count": 1,
            "per_metre": {k: round(v, 4) for k, v in per.items()}, "total": total, "detail": detail}


def bill_of_quantities(structures: Iterable[dict]) -> dict:
    """Quantities per structure and the totals per material."""
    rows = [quantities(s) for s in structures]
    totals: dict[str, float] = {}
    for r in rows:
        for k, v in r["total"].items():
            totals[k] = round(totals.get(k, 0.0) + v, 3)
    by_kind: dict[str, dict] = {}
    for r in rows:
        e = by_kind.setdefault(r["kind"], {"count": 0, "length": 0.0})
        e["count"] += 1
        e["length"] = round(e["length"] + r["length"], 2)
    return {"rows": rows, "totals": totals, "by_kind": by_kind,
            "materials": {k: MATERIALS.get(k, {"label": k, "unit": ""}) for k in totals}}


# ---------------------------------------------------------------------- NRS drainage rules
def lining_class(gradient_pct: float, soil: str = "clayey", std: Standard | None = None) -> tuple[int, str]:
    """NRS 2070 Table 13-3: the lining a drain needs at this longitudinal gradient.
    Returns (class, reason): 0 none, 1 grass turfing, 2 hard lining, 3 stepping."""
    g = abs(float(gradient_pct))
    turf = 2.0 if soil == "clayey" else 1.0
    hard, step = 3.0, 5.0
    if std is not None:
        turf = std.resolve("drain_turf_min_gradient", drain_soil=soil).value or turf
        hard = std.resolve("drain_hard_lining_min_gradient").value or hard
        step = std.resolve("drain_stepped_min_gradient").value or step
    if g >= step:
        return 3, f"gradient {g:.2f} % is above {step:g} %: stepping (NRS 2070 Table 13-3)"
    if g >= hard:
        return 2, f"gradient {g:.2f} % is {hard:g} to {step:g} %: stone pitching, masonry or concrete"
    if g >= turf:
        return 1, f"gradient {g:.2f} % is above {turf:g} % in {soil} soil: grass turfing"
    return 0, f"gradient {g:.2f} % is below {turf:g} % in {soil} soil: no lining required"


def drain_type_for(gradient_pct: float, soil: str = "clayey", std: Standard | None = None, prefer: str = "masonry") -> tuple[str, str]:
    """A drain type that satisfies Table 13-3 at this gradient."""
    cls, why = lining_class(gradient_pct, soil, std)
    pick = {0: "earth_trapezoid", 1: "turfed_trapezoid", 2: "masonry_trapezoid" if prefer == "masonry" else "pitched_trapezoid", 3: "stepped_masonry"}[cls]
    return pick, why


# ---------------------------------------------------------------------- suggestions
def _wall_type_for(kind: str, height: float) -> str:
    """The lightest catalogue type whose usual height range covers this wall; when the wall is taller
    than anything in the catalogue, the heaviest type (the check then asks for a special design)."""
    allowed = STRUCTURE_KINDS[kind]["types"]
    best = None
    for t in allowed:
        spec = WALL_TYPES[t]
        if height < float(spec.get("min_height", 0.0)) - 1e-9:
            continue
        if height <= float(spec.get("max_height", 1e9)) + 1e-9 and (best is None or float(spec.get("max_height", 1e9)) < float(WALL_TYPES[best].get("max_height", 1e9))):
            best = t
    if best is None and allowed:
        best = max(allowed, key=lambda t: float(WALL_TYPES[t].get("max_height", 0.0)))
    return best or STRUCTURE_KINDS[kind]["default_type"]


def suggest_walls(sections: Iterable[dict], max_fill_height: float, max_cut_depth: float, min_length: float = 10.0) -> list[dict]:
    """Group consecutive stations where a side needs a wall.

    Retaining wall: fill height at the hinge above `max_fill_height`, or the fill slope does not
    catch the ground within the search width. Breast wall: cut depth above `max_cut_depth`, or the
    cut slope does not catch. Each group becomes one structure with the maximum height and a type
    from the catalogue that suits that height."""
    out: list[dict] = []
    secs = list(sections)
    for side in ("left", "right"):
        run: list[dict] = []

        def flush():
            if not run:
                return
            chs = [r["chainage"] for r in run]
            frm, to = min(chs), max(chs)
            if len(run) == 1 or to - frm < min_length:
                half = max(min_length / 2, 5.0)
                frm, to = frm - half, to + half
            kind = run[0]["kind"]
            h = max(abs(r["height"]) for r in run)
            t = _wall_type_for(kind, h)
            spec = WALL_TYPES[t]
            usual = float(spec.get("max_height", 0))
            fits = h <= usual + 1e-9
            out.append({"kind": kind, "side": side, "from": round(frm, 2), "to": round(to, 2), "source": "suggested",
                        "params": {"height": round(h, 2), "type": t},
                        "note": f"{'fill' if kind == 'retaining_wall' else 'cut'} up to {h:.1f} m at "
                                f"{format_chainage(run[int(np.argmax([abs(r['height']) for r in run]))]['chainage'])}; "
                                + (f"{spec['label'].lower()} suits up to {usual:.0f} m"
                                   if fits else f"taller than any type in the catalogue ({spec['label'].lower()} reaches {usual:.0f} m): "
                                                "design it specially or lower the road here")})
            run.clear()

        for s in secs:
            sd = s[side] if isinstance(s.get(side), dict) else s[side].as_dict()
            kind = None
            if sd["kind"] in ("fill", "no_catch_fill") and (sd["height"] > max_fill_height or sd["kind"] == "no_catch_fill"):
                kind = "retaining_wall"
            elif sd["kind"] in ("cut", "no_catch_cut") and (-sd["height"] > max_cut_depth or sd["kind"] == "no_catch_cut"):
                kind = "breast_wall"
            if kind is None or (run and run[-1]["kind"] != kind):
                flush()
            if kind:
                run.append({"chainage": s["chainage"], "height": sd["height"], "kind": kind})
        flush()
    return out


def suggest_drains(sections: Iterable[dict], grade_at, soil: str = "clayey", std: Standard | None = None, min_length: float = 20.0,
                   catch_drain_offset: float = 5.0, toe_drain_max_fill: float = 0.8) -> list[dict]:
    """Longitudinal drains from the corridor: a side drain along every run of cut, a catch water
    drain behind deep cuts, a toe drain along low fills. The type follows NRS 2070 Table 13-3 for
    the gradient of the grade line over that run."""
    secs = sorted(sections, key=lambda s: s["chainage"])
    out: list[dict] = []
    for side in ("left", "right"):
        runs: dict[str, list[dict]] = {"side_drain": [], "catch_drain": [], "toe_drain": []}

        def flush(kind: str):
            run = runs[kind]
            if not run or run[-1]["chainage"] - run[0]["chainage"] < min_length:
                run.clear()
                return
            frm, to = run[0]["chainage"], run[-1]["chainage"]
            mid = (frm + to) / 2
            g = float(abs(grade_at(mid))) if grade_at else 0.0
            t, why = drain_type_for(g, soil, std)
            params = {"type": t, "width": 0.6 if kind != "toe_drain" else 0.5, "depth": 0.5 if kind != "toe_drain" else 0.4, "gradient": round(g, 3)}
            if kind == "catch_drain":
                params["offset"] = catch_drain_offset
            depth = max((abs(r["height"]) for r in run), default=0.0)
            out.append({"kind": kind, "side": side, "from": round(frm, 2), "to": round(to, 2), "source": "suggested", "params": params,
                        "note": f"{'cut' if kind != 'toe_drain' else 'low fill'} up to {depth:.1f} m; {why}"})
            run.clear()

        for s in secs:
            sd = s[side] if isinstance(s.get(side), dict) else s[side].as_dict()
            h = float(sd.get("height") or 0.0)
            is_cut = sd["kind"] in ("cut", "no_catch_cut")
            wants = {"side_drain": is_cut, "catch_drain": is_cut and -h >= 3.0,
                     "toe_drain": sd["kind"] in ("fill", "no_catch_fill") and 0 < h <= toe_drain_max_fill}
            for kind, want in wants.items():
                if want:
                    runs[kind].append({"chainage": s["chainage"], "height": h})
                else:
                    flush(kind)
        for kind in runs:
            flush(kind)
    return sorted(out, key=lambda s: (s["from"], s["kind"]))


def suggest_culverts(al: HorizontalAlignment, streams: Iterable[np.ndarray], sag_points: Iterable[float] = (), min_spacing: float = 20.0,
                     outlet_spacing: float | None = None) -> list[dict]:
    """Culverts where stream lines (drainage breaklines) cross the centreline; cross drains at sag
    low points of the vertical alignment. Suggestions closer than `min_spacing` are merged. With
    `outlet_spacing` (NRS 2070 cl. 13.8 i: side-drain outlets at most 500 m apart) extra cross
    drains are added wherever the gap between cross-drainage points would exceed it."""
    xy, ch = al.densify(max_segment=2.0)
    centre = shapely.LineString(xy)
    found: list[tuple[float, str, str]] = []
    for s in streams:
        s = np.asarray(s, dtype=float)
        if len(s) < 2:
            continue
        inter = shapely.intersection(centre, shapely.LineString(s[:, :2]))
        for p in shapely.get_coordinates(inter):
            k = int(np.argmin(np.hypot(xy[:, 0] - p[0], xy[:, 1] - p[1])))
            found.append((float(ch[k]), "culvert", "stream crossing"))
    for c in sag_points:
        found.append((float(c), "cross_drain", "sag low point of the vertical alignment"))
    found.sort()
    out: list[dict] = []
    for c, kind, why in found:
        if out and abs(out[-1]["from"] - c) < min_spacing:
            out[-1]["note"] += f"; {why}"
            continue
        out.append({"kind": kind, "side": None, "from": round(c, 2), "to": round(c, 2), "source": "suggested",
                    "params": dict(STRUCTURE_KINDS[kind]["params"]), "note": f"{why} at {format_chainage(c)}"})
    if outlet_spacing and outlet_spacing > 0:
        marks = sorted([al.start_chainage] + [s["from"] for s in out] + [al.end_chainage])
        extra: list[dict] = []
        for a, b in zip(marks, marks[1:]):
            gap = b - a
            if gap <= outlet_spacing + 1e-6:
                continue
            n = int(np.ceil(gap / outlet_spacing))          # n - 1 extra outlets split the gap evenly
            for k in range(1, n):
                c = a + gap * k / n
                extra.append({"kind": "cross_drain", "side": None, "from": round(c, 2), "to": round(c, 2), "source": "suggested",
                              "params": dict(STRUCTURE_KINDS["cross_drain"]["params"]),
                              "note": f"side-drain outlet at {format_chainage(c)}: no cross drainage within {outlet_spacing:.0f} m (NRS 2070 cl. 13.8 i)"})
        out = sorted(out + extra, key=lambda s: s["from"])
    return out


# ---------------------------------------------------------------------- checks
def check_structures(structures: Sequence[dict], std: Standard, ctx: dict, grade_at=None, soil: str = "clayey") -> list[Check]:
    """Structure and drainage checks: culvert span against the 6 m bridge limit (cl. 17.1 a), drain
    lining against Table 13-3, drain gradient (cl. 13.8 f), catch-drain offset (cl. 13.8 d), outlet
    spacing (cl. 13.8 i) and each wall type against the height it is normally used for."""
    checks: list[Check] = []
    p_bridge = std.resolve("culvert_max_length")
    p_gmin = std.resolve("drain_min_gradient")
    p_offset = std.resolve("catch_drain_min_offset")
    p_outlet = std.resolve("drain_outlet_max_spacing")
    crossings: list[float] = []
    for s in structures:
        kind, p = s["kind"], s.get("params") or {}
        where = f"{STRUCTURE_KINDS[kind]['label']} {format_chainage(s['from'])}" + (f" - {format_chainage(s['to'])}" if s["to"] > s["from"] else "")
        t = str(p.get("type") or "")
        if kind in _CROSS_KINDS:
            crossings.append(float(s["from"]))
            span = float(p.get("span") or 0) * max(1, int(p.get("cells") or 1))
            if p_bridge.value is not None and span > p_bridge.value + 1e-9:
                checks.append(Check("culvert_span", "Culvert span", False, round(span, 2), p_bridge.value, "m", p_bridge.source, p_bridge.status, where,
                                    f"total span {span:.2f} m is over {p_bridge.value:g} m: this is a bridge and is designed to the Nepal Bridge Standards", "error"))
            spec = CULVERT_TYPES.get(t)
            if spec and spec.get("max_span") and float(p.get("span") or 0) > float(spec["max_span"]) + 1e-9:
                checks.append(Check("culvert_type", "Culvert type", False, float(p.get("span") or 0), float(spec["max_span"]), "m",
                                    "PLM catalogue: usual span range of this type", "default", where,
                                    f"{spec['label'].lower()} is normally used up to {spec['max_span']:g} m span", "warning"))
        elif kind in _DRAIN_KINDS:
            g = p.get("gradient")
            if g is None and grade_at is not None:
                g = float(abs(grade_at((float(s["from"]) + float(s["to"])) / 2)))
            if g is not None:
                need, why = lining_class(float(g), soil, std)
                have = int((DRAIN_TYPES.get(t) or {}).get("nrs_lining_class", 0))
                ok = have >= need
                checks.append(Check("drain_lining", "Drain lining", ok, float(g), None, "%", "NRS 2070 Table 13-3 type of lining of side drains", "verified", where,
                                    f"{(DRAIN_TYPES.get(t) or {}).get('label', t)}: {why}" + ("" if ok else "; a heavier lining is required"),
                                    "info" if ok else "warning"))
                if p_gmin.value is not None and abs(float(g)) < p_gmin.value - 1e-9 and kind != "subsurface_drain":
                    checks.append(Check("drain_gradient", "Drain gradient", False, round(abs(float(g)), 2), p_gmin.value, "%", p_gmin.source, p_gmin.status, where,
                                        f"gradient {abs(float(g)):.2f} % is flatter than the {p_gmin.value:g} % minimum for drains", "warning"))
            if kind == "catch_drain" and p_offset.value is not None:
                off = float(p.get("offset") or 0)
                checks.append(Check("catch_drain_offset", "Catch drain offset", off >= p_offset.value - 1e-9, off, p_offset.value, "m",
                                    p_offset.source, p_offset.status, where,
                                    f"{off:.1f} m behind the top of the cut ({'ok' if off >= p_offset.value else 'less than'} {p_offset.value:g} m)",
                                    "info" if off >= p_offset.value else "warning"))
        elif kind in _WALL_KINDS:
            spec = WALL_TYPES.get(t)
            h = float(p.get("height") or 0)
            if spec and h > float(spec.get("max_height", 1e9)) + 1e-9:
                checks.append(Check("wall_height", "Wall height", False, round(h, 2), float(spec["max_height"]), "m",
                                    "PLM catalogue: usual height range of this wall type", "default", where,
                                    f"{spec['label'].lower()} is normally used up to {spec['max_height']:g} m; use a heavier type or design it specially", "warning"))
            elif spec and h < float(spec.get("min_height", 0.0)) - 1e-9:
                checks.append(Check("wall_height", "Wall height", True, round(h, 2), float(spec["min_height"]), "m",
                                    "PLM catalogue: usual height range of this wall type", "default", where,
                                    f"{spec['label'].lower()} is usually reserved for walls over {spec['min_height']:g} m; a lighter type may be cheaper", "info"))
    if p_outlet.value is not None and crossings:
        crossings.sort()
        for a, b in zip(crossings, crossings[1:]):
            if b - a > p_outlet.value + 1e-6:
                checks.append(Check("outlet_spacing", "Side-drain outlet spacing", False, round(b - a, 1), p_outlet.value, "m", p_outlet.source, p_outlet.status,
                                    f"CH {format_chainage(a)} - {format_chainage(b)}",
                                    f"{b - a:.0f} m between cross-drainage points, more than the {p_outlet.value:g} m maximum", "warning"))
    return checks
