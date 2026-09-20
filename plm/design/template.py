"""Typical cross-sections (templates) and their assignment along the alignment.

A template is data, so new component kinds (kerbs, walls, drains, medians) can be added without
touching the corridor code: every component has a width and a cross slope; components flagged
`superelevate` follow the superelevation profile, the rest keep their own slope. Side treatment
(cut slope with benches and a ditch, fill slope with optional benches) hangs off the outermost
component, the *hinge*.

Cross slopes are in % of rise per metre outward from the centreline (negative = falls outward).
Side slopes are h:v ratios (horizontal metres per metre of height).
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .standards import Standard

COMPONENT_KINDS = ("lane", "shoulder", "verge", "median", "kerb", "footpath", "custom")

DEFAULT_CUT = {"slope": 1.0, "bench_height": 6.0, "bench_width": 1.5, "ditch": {"foreslope": 1.0, "depth": 0.5, "bottom": 0.5}}
DEFAULT_FILL = {"slope": 1.5, "bench_height": 0.0, "bench_width": 0.0}


def normalise_component(c: dict) -> dict:
    kind = str(c.get("kind", "custom"))
    if kind not in COMPONENT_KINDS:
        kind = "custom"
    return {"kind": kind, "name": str(c.get("name") or kind), "width": max(float(c.get("width", 0.0) or 0.0), 0.0),
            "slope": float(c.get("slope", -2.5) if c.get("slope") is not None else -2.5),
            "superelevate": bool(c.get("superelevate", kind in ("lane", "shoulder")))}


def normalise_template(t: dict) -> dict:
    """Fill in defaults so the corridor never has to guess; unknown keys are kept for future modules."""
    out = deepcopy(t)
    out["id"] = str(out.get("id") or "default")
    out["name"] = str(out.get("name") or out["id"])
    out["surface"] = str(out.get("surface") or "bituminous")
    out["left"] = [normalise_component(c) for c in out.get("left") or []]
    out["right"] = [normalise_component(c) for c in out.get("right") or []]
    cut = {**DEFAULT_CUT, **(out.get("cut") or {})}
    cut["ditch"] = {**DEFAULT_CUT["ditch"], **((out.get("cut") or {}).get("ditch") or {})} if (out.get("cut") or {}).get("ditch", True) not in (False, None) else None
    fill = {**DEFAULT_FILL, **(out.get("fill") or {})}
    out["cut"], out["fill"] = cut, fill
    out["max_offset"] = float(out.get("max_offset") or 60.0)  # search width for daylight
    return out


DEFAULT_FILL_HEIGHT = 6.0   # the fill slope of the default template is the standard's value for this height (checked per section later)


def default_template(std: Standard | None, ctx: dict) -> dict:
    """The typical section for the design's class / terrain from the standard: carriageway (lanes x lane
    width), shoulders with the extra crossfall, cut slope for the material, fill slope for a typical
    height (NRS 2070 Tables 11-1 to 11-5; berm dimensions are application defaults)."""
    cw = sh = camber = extra = None
    if std is not None:
        cw = std.resolve("carriageway_width", **ctx).value
        sh = std.resolve("shoulder_width", **ctx).value
        camber = std.resolve("camber", surface=ctx.get("surface", "bituminous")).value
        extra = std.resolve("shoulder_crossfall_extra").value
        cut_slope = std.resolve("cut_slope", material=ctx.get("material", "soil")).value
        fill_slope = std.resolve("fill_slope", fill_height=DEFAULT_FILL_HEIGHT).value
        if fill_slope is None:
            fill_slope = std.resolve("fill_slope").value
        bh = std.resolve("bench_height").value
        bw = std.resolve("bench_width").value
        label = std.label("class", std.canonical("classes", str(ctx.get("road_class", "road"))))
        name = f"Class {std.canonical('classes', str(ctx.get('road_class', '')))} typical section" if label else "typical section"
    else:
        cut_slope = fill_slope = bh = bw = None
        name = f"{ctx.get('road_class', 'road')} typical section"
    cw = cw or 5.5
    sh = sh or 1.0
    camber = camber or 2.5
    extra = 0.5 if extra is None else extra
    half = [{"kind": "lane", "name": "lane", "width": cw / 2.0, "slope": -camber, "superelevate": True},
            {"kind": "shoulder", "name": "shoulder", "width": sh, "slope": -(camber + extra), "superelevate": True}]
    return normalise_template({
        "id": "default", "name": name, "surface": ctx.get("surface", "bituminous"),
        "left": deepcopy(half), "right": deepcopy(half),
        "cut": {"slope": cut_slope or 1.0, "bench_height": bh or 6.0, "bench_width": bw or 1.5, "ditch": {"foreslope": 1.0, "depth": 0.5, "bottom": 0.5}},
        "fill": {"slope": fill_slope or 2.0, "bench_height": 0.0, "bench_width": 0.0},
    })


def hinge_polyline(template: dict, side: str, centre_z: float, super_slope: float | None) -> list[tuple[float, float]]:
    """Points (offset >= 0 outward, z) from the centreline to the hinge for one side.
    `super_slope` (in %) replaces the slope of components flagged superelevate when given."""
    pts = [(0.0, float(centre_z))]
    o, z = 0.0, float(centre_z)
    for c in template[side]:
        w = c["width"]
        if w <= 0:
            continue
        s = super_slope if (super_slope is not None and c["superelevate"]) else c["slope"]
        o += w
        z += w * s / 100.0
        pts.append((o, z))
    return pts


def template_at(templates: list[dict], assignments: list[dict], chainage: float) -> dict:
    """The template assigned to a chainage; falls back to the first template."""
    by_id = {t["id"]: t for t in templates}
    for a in assignments:
        if float(a.get("from", -1e18)) <= chainage <= float(a.get("to", 1e18)) and a.get("template_id") in by_id:
            return by_id[a["template_id"]]
    return templates[0]


def validate_templates(templates: list[dict], assignments: list[dict]) -> list[str]:
    problems = []
    ids = [t.get("id") for t in templates]
    if not templates:
        problems.append("no template defined")
    if len(set(ids)) != len(ids):
        problems.append("template ids are not unique")
    for a in assignments:
        if a.get("template_id") not in ids:
            problems.append(f"assignment {a.get('from')}-{a.get('to')} refers to unknown template {a.get('template_id')!r}")
        if float(a.get("from", 0)) > float(a.get("to", 0)):
            problems.append(f"assignment {a.get('from')}-{a.get('to')} has from > to")
    for t in templates:
        if not t.get("left") and not t.get("right"):
            problems.append(f"template {t.get('id')} has no components")
        for side in ("left", "right"):
            for c in t.get(side) or []:
                if c.get("width", 0) < 0:
                    problems.append(f"template {t.get('id')}: negative width")
        if float(t.get("cut", {}).get("slope", 1)) <= 0 or float(t.get("fill", {}).get("slope", 1)) <= 0:
            problems.append(f"template {t.get('id')}: side slopes must be positive h:v ratios")
    return problems


def component_catalogue() -> list[dict[str, Any]]:
    """What the UI can offer; future modules extend this list (walls, drains, kerbs with heights)."""
    return [
        {"kind": "lane", "label": "Lane", "defaults": {"width": 3.5, "slope": -2.5, "superelevate": True}},
        {"kind": "shoulder", "label": "Shoulder", "defaults": {"width": 1.0, "slope": -3.5, "superelevate": True}},
        {"kind": "verge", "label": "Verge", "defaults": {"width": 0.5, "slope": -4.0, "superelevate": False}},
        {"kind": "median", "label": "Median", "defaults": {"width": 1.0, "slope": 0.0, "superelevate": False}},
        {"kind": "kerb", "label": "Kerb", "defaults": {"width": 0.15, "slope": 0.0, "superelevate": False}},
        {"kind": "footpath", "label": "Footpath", "defaults": {"width": 1.5, "slope": -2.0, "superelevate": False}},
        {"kind": "custom", "label": "Custom strip", "defaults": {"width": 1.0, "slope": -2.0, "superelevate": False}},
    ]
