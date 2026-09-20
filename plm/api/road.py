"""Road design service: joins the design record, the terrain snapshot and `plm.design`.

Documents per design (design_data): horizontal, vertical, templates, structures. Results (design_results):
corridor runs. Every payload returned to the UI carries the resolved standards checks with sources.
"""
from __future__ import annotations

import re
from typing import Any

import numpy as np

from ..design import earthworks  # noqa: F401  (re-exported for callers that want the helpers)
from ..design.corridor import build_corridor
from ..design.horizontal import check_horizontal, curve_table
from ..design.standards import Standard, list_standards, load_standard
from ..design.structures import (STRUCTURE_KINDS, bill_of_quantities, catalogue as structure_catalogue, check_structures,
                                 normalise_structure, suggest_culverts, suggest_drains, suggest_walls)
from ..design.superelevation import SuperelevationProfile, build_profile
from ..design.template import component_catalogue, default_template, normalise_template, validate_templates
from ..design.vertical import VerticalAlignment, stretch_pvis, trim_pvis
from ..engine.alignment import IP, HorizontalAlignment, format_chainage
from ..engine.sections import generate_profile
from ..engine.tin import TIN
from . import services
from .gpkg import ProjectStore
from .services import ServiceError

DEFAULT_STANDARD = "nrs-2070"
# parameters shown in the Standards panel and the Excel "Standard" sheet (resolvable from the design context alone)
STANDARD_KEYS = ["design_speed", "stopping_sight_distance", "overtaking_distance", "min_radius", "min_radius_absolute", "min_radius_comfort", "side_friction",
                 "max_superelevation", "superelevation_relative_gradient", "transition_required_below_radius", "transition_shift_min",
                 "hairpin_min_radius", "hairpin_min_transition", "hairpin_max_gradient", "hairpin_min_spacing",
                 "max_gradient", "gradient_altitude_easing", "min_gradient_drainage", "min_k_crest", "min_k_sag", "min_vertical_curve_length",
                 "lanes", "lane_width", "carriageway_width", "shoulder_width", "shoulder_crossfall_extra", "camber", "right_of_way", "vertical_clearance",
                 "cut_slope", "cut_slope_flat", "max_fill_height", "bench_height", "bench_width", "max_fill_height_without_wall", "max_cut_depth_without_wall",
                 "drain_min_gradient", "drain_outlet_max_spacing", "catch_drain_min_offset", "toe_drain_max_fill", "culvert_max_length",
                 "drain_hard_lining_min_gradient", "drain_stepped_min_gradient", "design_return_period"]
DEFAULT_ROAD_TYPE = {"I": "national_highway", "II": "national_highway", "III": "feeder_road", "IV": "district_road"}


# ---------------------------------------------------------------------- context
def design_context(design: dict) -> tuple[Standard, dict]:
    """Design settings -> (standard, context). Legacy class / material names (national, feeder, colluvium ...)
    are mapped to the standard's own keys (NRS 2070 classes I-IV, Table 11-5 materials)."""
    st = design.get("settings") or {}
    std = load_standard(str(st.get("standard") or DEFAULT_STANDARD), st.get("deviations") or {})
    road_class = std.canonical("classes", str(st.get("road_class") or "III"))
    ctx = {"road_class": road_class, "terrain": str(st.get("terrain") or "rolling"),
           "surface": str(st.get("surface") or "bituminous"), "material": std.canonical("materials", str(st.get("material") or "soil")),
           "road_type": str(st.get("road_type") or DEFAULT_ROAD_TYPE.get(road_class, "feeder_road")),
           "snow_bound": bool(st.get("snow_bound", False)), "altitude_compensation": bool(st.get("altitude_compensation", True))}
    speed = st.get("design_speed")
    if not speed:
        speed = std.resolve("design_speed", **ctx).value or 40.0
    ctx["design_speed"] = float(speed)
    ctx["lanes"] = int(std.resolve("lanes", **ctx).value or 2)
    ctx["lane_width"] = float(std.resolve("lane_width").value or 3.5)
    return std, ctx


def gradient_easing(std: Standard, ctx: dict, mean_level: float | None) -> float:
    """NRS 2070 cl. 10.1.2 a: the maximum gradient is eased by 0.5 % for each 500 m of altitude above
    mean sea level (levels are taken as metres above MSL)."""
    if not ctx.get("altitude_compensation", True) or mean_level is None or not np.isfinite(mean_level):
        return 0.0
    per = std.resolve("gradient_altitude_easing").value
    if not per or mean_level <= 500:
        return 0.0
    return round(per * int(mean_level // 500), 3)


def _tin(store: ProjectStore, design: dict) -> TIN:
    if design.get("tin_run_id") is None:
        raise ServiceError("the design has no terrain snapshot (TIN run)", 400)
    _, tin = services.load_tin(store, int(design["tin_run_id"]))
    return tin


def _checks(checks) -> list[dict]:
    return [c.as_dict() for c in checks]


def _round(v: Any, nd: int = 3):
    if isinstance(v, float):
        return None if np.isnan(v) else round(v, nd)
    return v


# ---------------------------------------------------------------------- horizontal
def horizontal_doc(store: ProjectStore, design: dict) -> dict | None:
    doc = store.design_get(design["id"], "horizontal")
    if doc is None and design.get("alignment_id"):
        row = store.get_alignment(int(design["alignment_id"]))
        if row:
            al = services.alignment_from_dict(row)
            doc = {"ips": [{"x": p.x, "y": p.y, "radius": p.radius, "label": p.label, "transition": p.transition} for p in al.ips],
                   "start_chainage": al.start_chainage, "seeded_from": {"alignment_id": row["id"], "name": row.get("name")}}
            store.design_set(design["id"], "horizontal", doc)
    return doc


def horizontal_from_doc(doc: dict) -> HorizontalAlignment:
    ips = [IP(float(p["x"]), float(p["y"]), float(p.get("radius") or 0), str(p.get("label") or ""), float(p.get("transition") or 0)) for p in doc.get("ips") or []]
    if len(ips) < 2:
        raise ServiceError("the horizontal alignment needs at least two IPs", 400)
    return HorizontalAlignment(ips, float(doc.get("start_chainage") or 0.0))


def supere_settings(std: Standard, ctx: dict, tdoc: dict | None) -> dict:
    s = dict((tdoc or {}).get("superelevation") or {})
    out = {
        "enabled": bool(s.get("enabled", True)),
        "e_max": float(s.get("e_max") or (std.resolve("max_superelevation_snow_bound").value if ctx.get("snow_bound") else None)
                       or std.resolve("max_superelevation", **ctx).value or 7.0),
        "side_friction": float(s.get("side_friction") or std.resolve("side_friction", design_speed=ctx["design_speed"]).value or 0.15),
        "camber": float(s.get("camber") or std.resolve("camber", surface=ctx["surface"]).value or 2.5),
        "relative_gradient": float(s.get("relative_gradient") or std.resolve("superelevation_relative_gradient", **ctx).value or 0.667),
        "rotated_width": float(s.get("rotated_width") or (std.resolve("carriageway_width", **ctx).value or 5.5) / 2.0),
        "runoff_on_tangent": float(s.get("runoff_on_tangent", 2.0 / 3.0)),
    }
    return out


def supere_profile(al: HorizontalAlignment, std: Standard, ctx: dict, tdoc: dict | None) -> SuperelevationProfile | None:
    s = supere_settings(std, ctx, tdoc)
    if not s["enabled"]:
        return None
    return build_profile(al, speed_kmh=ctx["design_speed"], side_friction=s["side_friction"], e_max_pct=s["e_max"], camber_pct=s["camber"],
                         relative_gradient_pct=s["relative_gradient"], rotated_width=s["rotated_width"], runoff_on_tangent=s["runoff_on_tangent"])


def horizontal_payload(store: ProjectStore, design: dict, doc: dict) -> dict:
    std, ctx = design_context(design)
    al = horizontal_from_doc(doc)
    xy, ch = al.densify(2.0)
    tdoc = store.design_get(design["id"], "templates")
    sp = supere_profile(al, std, ctx, tdoc)
    ctx = {**ctx, "superelevation_enabled": sp is not None}
    return {
        "ips": doc["ips"], "start_chainage": al.start_chainage, "end_chainage": al.end_chainage, "length": al.length, "is_valid": al.is_valid,
        "seeded_from": doc.get("seeded_from"),
        "geometry": {"centreline": np.round(xy, 3).tolist(), "chainages": np.round(ch, 3).tolist(), "key_points": al.key_points(),
                     "elements": [{"kind": e.kind, "start_chainage": e.start_chainage, "end_chainage": e.end_chainage, "radius": e.radius, "ip_index": e.ip_index}
                                  for e in al.elements]},
        "table": curve_table(al, std, ctx), "checks": _checks(check_horizontal(al, std, ctx)),
        "superelevation": {"settings": supere_settings(std, ctx, tdoc),
                           "curves": [c.__dict__ for c in sp.curves] if sp else [],
                           "table": sp.table(max(al.length / 200.0, 5.0), al.start_chainage, al.end_chainage) if sp else []},
        "context": ctx, "standard": std.summary()["id"],
    }


FOLLOW_MODES = ("keep", "stretch", "refit", "trim")
FOLLOW_MESSAGES = {"stretch": "grade line stretched to the new alignment, cut / fill depths kept",
                   "refit": "grade line re-fitted to the ground under the new alignment",
                   "trim": "grade line trimmed / extended to the new alignment"}


def put_horizontal(store: ProjectStore, design: dict, body: dict) -> dict:
    follow = str(body.get("follow") or "keep")
    if follow not in FOLLOW_MODES:
        raise ServiceError(f"unknown follow mode {follow!r}; use one of {', '.join(FOLLOW_MODES)}", 400)
    doc = {"ips": [{"x": float(p["x"]), "y": float(p["y"]), "radius": float(p.get("radius") or 0), "label": str(p.get("label") or str(i)),
                    "transition": float(p.get("transition") or 0)} for i, p in enumerate(body.get("ips") or [])],
           "start_chainage": float(body.get("start_chainage") or 0.0), "seeded_from": (store.design_get(design["id"], "horizontal") or {}).get("seeded_from")}
    horizontal_from_doc(doc)  # validates
    store.design_set(design["id"], "horizontal", doc)
    payload = horizontal_payload(store, design, doc)
    info: dict[str, Any] = {"mode": follow, "applied": False, "message": ""}
    if follow != "keep":
        try:
            v = follow_vertical(store, design, follow)
        except ServiceError as e:
            v = None
            info["message"] = f"grade line not updated: {e}"
        if v is not None:
            info.update({"applied": True, "message": FOLLOW_MESSAGES[follow], "stale": v["stale"], "pvis": len(v["pvis"])})
    payload["vertical_follow"] = info
    return payload


def preview_ground(store: ProjectStore, design: dict, doc: dict, interval: float = 10.0) -> list[dict]:
    """Ground profile under a *preview* alignment (nothing stored), for the live profile while dragging IPs."""
    try:
        return ground_profile(store, design, horizontal_from_doc(doc), interval=interval)
    except ServiceError:
        return []


# ---------------------------------------------------------------------- ground and vertical
def ground_profile(store: ProjectStore, design: dict, al: HorizontalAlignment, interval: float = 10.0) -> list[dict]:
    tin = _tin(store, design)
    pts = generate_profile(tin, al, interval, include_curve_points=True, include_edge_crossings=False)
    return [{"chainage": round(p.chainage, 3), "z": round(p.z, 3), "x": round(p.x, 3), "y": round(p.y, 3)} for p in pts if np.isfinite(p.z)]


def _ground_fn(store: ProjectStore, design: dict, al: HorizontalAlignment):
    """Interpolating ground level along the alignment, or None when there is no terrain under it."""
    try:
        g = ground_profile(store, design, al, interval=5.0)
    except ServiceError:
        return None
    if len(g) < 2:
        return None
    ch = np.array([q["chainage"] for q in g], float)
    z = np.array([q["z"] for q in g], float)
    return lambda c: float(np.interp(c, ch, z, left=np.nan, right=np.nan))


def _stamp_vertical(store: ProjectStore, design: dict, doc: dict, al: HorizontalAlignment | None) -> dict:
    """Record what the grade line was made for: the alignment range and the ground level under each PVI,
    so that a later alignment change can move the PVIs while keeping their cut / fill depths."""
    if al is None:
        return doc
    doc["for_range"] = [al.start_chainage, al.end_chainage]
    gf = _ground_fn(store, design, al)
    for p in doc["pvis"]:
        gz = gf(float(p["chainage"])) if gf else float("nan")
        p["ground_z"] = None if gz is None or not np.isfinite(gz) else round(gz, 3)
    return doc


def _curve_checks(va: VerticalAlignment, al: HorizontalAlignment, std: Standard, ctx: dict, gmax: float | None) -> list[dict]:
    """Checks that need both alignments: grade compensation on curves (cl. 10.1.2 b, c) and the
    gradient limit inside hair-pin bends (Table 9-3)."""
    from ..design.standards import Check

    out: list[Check] = []
    thr = std.resolve("grade_compensation_threshold").value or 4.0
    p_hp_g = std.resolve("hairpin_max_gradient")
    hp_defl = std.resolve("hairpin_deflection").value or 120.0
    for g in al.geometry:
        if g.radius <= 0 or g.total_length <= 0:
            continue
        a, b = max(g.bc_chainage, va.start_chainage), min(g.ec_chainage, va.end_chainage)
        if b <= a:
            continue
        chs = np.linspace(a, b, max(int((b - a) / 5.0), 2) + 1)
        gmax_curve = float(np.max(np.abs(va.grade_at(chs))))
        where = f"IP {g.label or g.index} curve (CH {format_chainage(g.bc_chainage)} - {format_chainage(g.ec_chainage)})"
        if gmax is not None and gmax_curve > thr + 1e-9:
            comp = min((30.0 + g.radius) / g.radius, 75.0 / g.radius)
            limit = gmax - comp
            ok = gmax_curve <= limit + 1e-9
            out.append(Check("grade_compensation", "Grade on curve", ok, round(gmax_curve, 2), round(limit, 2), "%",
                             "NRS 2070 cl. 10.1.2 b: gradient eased on curves by (30 + R) / R %, at most 75 / R %", "verified", where,
                             f"gradient {gmax_curve:.2f} % on R = {g.radius:.0f} m {'is within' if ok else 'exceeds'} {limit:.2f} % ({gmax:g} % less {comp:.2f} % compensation)",
                             "info" if ok else "warning"))
        if abs(np.degrees(g.deflection)) >= hp_defl and p_hp_g.value is not None:
            ok = gmax_curve <= p_hp_g.value + 1e-9
            out.append(Check("hairpin_gradient", "Hair-pin gradient", ok, round(gmax_curve, 2), p_hp_g.value, "%", p_hp_g.source, p_hp_g.status, where,
                             f"gradient {gmax_curve:.2f} % through the hair-pin bend {'ok' if ok else 'exceeds'} ({p_hp_g.value:g} % maximum)", "info" if ok else "error"))
    return [c.as_dict() for c in out]


def vertical_payload(store: ProjectStore, design: dict, doc: dict, al: HorizontalAlignment | None) -> dict:
    std, ctx = design_context(design)
    va = VerticalAlignment.make(doc["pvis"])
    mean_level = float(np.mean([p.elevation for p in va.pvis])) if va.pvis else None
    easing = gradient_easing(std, ctx, mean_level)
    ctx = {**ctx, "gradient_easing": easing}
    checks = _checks(va.check(std, ctx))
    if al is not None:
        gmax = std.resolve("max_gradient", **ctx).value
        checks += _curve_checks(va, al, std, ctx, None if gmax is None else gmax - easing)
    out = {"pvis": va.to_dicts(), "table": va.table(), "line": va.densify(2.0), "checks": checks, "is_valid": va.is_valid,
           "gradient_easing": easing, "mean_level": None if mean_level is None else round(mean_level, 2),
           "start_chainage": va.start_chainage, "end_chainage": va.end_chainage, "source": doc.get("source", "manual"), "followed": doc.get("followed"),
           "for_range": doc.get("for_range"), "stale": False, "stale_reasons": [], "gaps": [], "outside_pvis": 0}
    if al is not None:
        out["alignment_range"] = [al.start_chainage, al.end_chainage]
        reasons: list[str] = []
        fr = doc.get("for_range")
        if fr and (abs(fr[0] - al.start_chainage) > 0.01 or abs(fr[1] - al.end_chainage) > 0.01):
            reasons.append(f"the grade line was made for CH {format_chainage(fr[0])} - {format_chainage(fr[1])}, the alignment now runs "
                           f"CH {format_chainage(al.start_chainage)} - {format_chainage(al.end_chainage)}")
        upd = store.design_updated(design["id"])
        if upd.get("horizontal") and upd.get("vertical") and upd["horizontal"] > upd["vertical"]:
            reasons.append("the alignment was changed after the grade line was saved")
        gaps = []
        if va.start_chainage > al.start_chainage + 0.01:
            gaps.append([al.start_chainage, min(va.start_chainage, al.end_chainage)])
        if va.end_chainage < al.end_chainage - 0.01:
            gaps.append([max(va.end_chainage, al.start_chainage), al.end_chainage])
        outside = sum(1 for p in va.pvis if p.chainage < al.start_chainage - 0.01 or p.chainage > al.end_chainage + 0.01)
        if gaps:
            reasons.append("the grade line does not cover " + ", ".join(f"CH {format_chainage(a)} - {format_chainage(b)}" for a, b in gaps))
        if outside:
            reasons.append(f"{outside} PVI(s) lie beyond the alignment")
        out.update({"stale": bool(reasons), "stale_reasons": reasons, "gaps": gaps, "outside_pvis": outside, "updated": upd})
    return out


def put_vertical(store: ProjectStore, design: dict, body: dict) -> dict:
    pvis = [{"chainage": float(p["chainage"]), "elevation": float(p["elevation"]), "length": float(p.get("length") or 0), "label": str(p.get("label") or "")}
            for p in body.get("pvis") or []]
    if len(pvis) < 2:
        raise ServiceError("a vertical alignment needs at least two PVIs", 400)
    doc = {"pvis": pvis, "source": body.get("source", "manual")}
    VerticalAlignment.make(pvis)  # validates
    hdoc = horizontal_doc(store, design)
    al = horizontal_from_doc(hdoc) if hdoc else None
    _stamp_vertical(store, design, doc, al)
    store.design_set(design["id"], "vertical", doc)
    return vertical_payload(store, design, doc, al)


def auto_vertical(store: ProjectStore, design: dict, spacing: float = 200.0, curve_length: float | None = None) -> dict:
    hdoc = horizontal_doc(store, design)
    if not hdoc:
        raise ServiceError("define the horizontal alignment first", 400)
    al = horizontal_from_doc(hdoc)
    ground = ground_profile(store, design, al, interval=max(spacing / 10.0, 5.0))
    if len(ground) < 2:
        raise ServiceError("the alignment has no ground under it (outside the TIN)", 400)
    std, ctx = design_context(design)
    L = curve_length if curve_length is not None else (std.resolve("min_vertical_curve_length", design_speed=ctx["design_speed"]).value or 40.0)
    va = VerticalAlignment.from_ground([(g["chainage"], g["z"]) for g in ground], spacing=spacing, curve_length=max(L, 0.0))
    doc = {"pvis": va.to_dicts(), "source": "auto", "spacing": float(spacing)}
    _stamp_vertical(store, design, doc, al)
    store.design_set(design["id"], "vertical", doc)
    return vertical_payload(store, design, doc, al)


def follow_vertical(store: ProjectStore, design: dict, mode: str, spacing: float | None = None) -> dict | None:
    """Bring the grade line up to date with the current alignment: `stretch` keeps relative positions and
    cut / fill depths, `refit` fits the ground again, `trim` clips / extends to the new range. Returns the
    vertical payload, or None when there is nothing to do (mode keep, or no grade line yet)."""
    if mode not in FOLLOW_MODES:
        raise ServiceError(f"unknown follow mode {mode!r}; use one of {', '.join(FOLLOW_MODES)}", 400)
    vdoc = store.design_get(design["id"], "vertical")
    if mode == "keep" or not vdoc or len(vdoc.get("pvis") or []) < 2:
        return None
    hdoc = horizontal_doc(store, design)
    if not hdoc:
        raise ServiceError("define the horizontal alignment first", 400)
    al = horizontal_from_doc(hdoc)
    if mode == "refit":
        sp = spacing or vdoc.get("spacing")
        if not sp:
            fr = vdoc.get("for_range") or [vdoc["pvis"][0]["chainage"], vdoc["pvis"][-1]["chainage"]]
            sp = max(round((float(fr[1]) - float(fr[0])) / max(len(vdoc["pvis"]) - 1, 1) / 10.0) * 10.0, 20.0)
        out = auto_vertical(store, design, spacing=float(sp))
        doc = store.design_get(design["id"], "vertical")
        doc["followed"] = "refit"
        store.design_set(design["id"], "vertical", doc)
        out["followed"] = "refit"
        return out
    new_range = (al.start_chainage, al.end_chainage)
    if mode == "stretch":
        old = vdoc.get("for_range") or [vdoc["pvis"][0]["chainage"], vdoc["pvis"][-1]["chainage"]]
        pvis = stretch_pvis(vdoc["pvis"], (float(old[0]), float(old[1])), new_range, _ground_fn(store, design, al))
    else:
        pvis = trim_pvis(vdoc["pvis"], new_range)
    VerticalAlignment.make(pvis)  # validates
    doc = {"pvis": pvis, "source": vdoc.get("source", "manual"), "spacing": vdoc.get("spacing"), "followed": mode}
    _stamp_vertical(store, design, doc, al)
    store.design_set(design["id"], "vertical", doc)
    return vertical_payload(store, design, doc, al)


# ---------------------------------------------------------------------- templates
def templates_doc(store: ProjectStore, design: dict) -> dict:
    std, ctx = design_context(design)
    doc = store.design_get(design["id"], "templates")
    if doc is None:
        doc = {"templates": [default_template(std, ctx)], "assignments": [], "superelevation": {}}
        store.design_set(design["id"], "templates", doc)
    return doc


def templates_payload(store: ProjectStore, design: dict, doc: dict) -> dict:
    std, ctx = design_context(design)
    templates = [normalise_template(t) for t in doc.get("templates") or []]
    return {"templates": templates, "assignments": doc.get("assignments") or [], "superelevation": supere_settings(std, ctx, doc),
            "problems": validate_templates(templates, doc.get("assignments") or []), "catalogue": component_catalogue(),
            "defaults": default_template(std, ctx)}


def put_templates(store: ProjectStore, design: dict, body: dict) -> dict:
    templates = [normalise_template(t) for t in body.get("templates") or []]
    assignments = [{"from": float(a.get("from", 0)), "to": float(a.get("to", 0)), "template_id": str(a.get("template_id"))} for a in body.get("assignments") or []]
    problems = validate_templates(templates, assignments)
    if problems:
        raise ServiceError("; ".join(problems), 400)
    doc = {"templates": templates, "assignments": assignments, "superelevation": dict(body.get("superelevation") or {})}
    store.design_set(design["id"], "templates", doc)
    return templates_payload(store, design, doc)


# ---------------------------------------------------------------------- corridor
def _compact_section(s: dict) -> dict:
    return {k: v for k, v in s.items() if k not in ("design", "ground")} | {"left": {k: v for k, v in s["left"].items() if k != "points"},
                                                                          "right": {k: v for k, v in s["right"].items() if k != "points"}}


def build_corridor_run(store: ProjectStore, design: dict, params: dict, progress=None) -> dict:
    hdoc = horizontal_doc(store, design)
    vdoc = store.design_get(design["id"], "vertical")
    if not hdoc:
        raise ServiceError("define the horizontal alignment first", 400)
    if not vdoc:
        raise ServiceError("define the vertical alignment first (Profile stage, or 'Fit to ground')", 400)
    std, ctx = design_context(design)
    al = horizontal_from_doc(hdoc)
    va = VerticalAlignment.make(vdoc["pvis"])
    tdoc = templates_doc(store, design)
    sp = supere_profile(al, std, ctx, tdoc)
    tin = _tin(store, design)
    try:
        res = build_corridor(tin, al, va, tdoc["templates"], tdoc.get("assignments") or [], sp,
                             interval=float(params.get("interval") or 20.0), prismoidal=bool(params.get("prismoidal", False)),
                             cut_factor=float(params.get("cut_factor") or 1.0), fill_factor=float(params.get("fill_factor") or 1.0),
                             swath=params.get("swath"), extra_chainages=params.get("extra_chainages") or (), progress=progress)
    except ValueError as e:
        raise ServiceError(str(e), 400)
    sections = [s.as_dict(full=True) for s in res.sections]
    nrs_flags = _fill_slope_flags(sections, tdoc, std)
    summary = res.summary()
    summary["flags"] = sorted({f for s in sections for f in s["flags"]})
    summary["standard_flags"] = nrs_flags
    rid = store.design_result_save(design["id"], "corridor", res.params, summary, {"sections": sections, "volumes": res.volumes, "mass_haul": res.mass_haul})
    return {"result_id": rid, "summary": summary, "volumes": res.volumes, "mass_haul": res.mass_haul, "sections": [_compact_section(s) for s in sections]}


def _fill_slope_flags(sections: list[dict], tdoc: dict, std: Standard) -> dict:
    """NRS 2070 Table 11-4: the embankment slope depends on the fill height. Sections whose template
    fill slope is steeper than the table value for their fill height are flagged, as are fills above
    the height that must be designed specially (12 m)."""
    slopes = {t["id"]: float((t.get("fill") or {}).get("slope") or 0) for t in tdoc.get("templates") or []}
    p_max = std.resolve("max_fill_height")
    counts = {"fill_slope_too_steep": 0, "fill_over_max_height": 0}
    for s in sections:
        used = slopes.get(s.get("template_id"), 0.0)
        for side in ("left", "right"):
            sd = s.get(side) or {}
            if sd.get("kind") != "fill" or not sd.get("height"):
                continue
            h = abs(float(sd["height"]))
            need = std.resolve("fill_slope", fill_height=h).value
            if need is not None and used and used < need - 1e-6:
                s["flags"].append(f"{side}_fill_slope_1:{used:g}_steeper_than_NRS_1:{need:g}_for_{h:.1f}m")
                counts["fill_slope_too_steep"] += 1
            if p_max.value is not None and h > p_max.value:
                s["flags"].append(f"{side}_fill_{h:.1f}m_over_{p_max.value:g}m_design_specially")
                counts["fill_over_max_height"] += 1
    return counts


def corridor_latest(store: ProjectStore, design: dict, full: bool = False) -> dict | None:
    rows = store.design_results(design["id"], "corridor")
    if not rows:
        return None
    r = rows[0]
    data = store.design_result_data(r["id"]) or {}
    secs = data.get("sections") or []
    return {"result_id": r["id"], "created": r["created"], "summary": r["summary"], "volumes": data.get("volumes") or [], "mass_haul": data.get("mass_haul") or [],
            "sections": secs if full else [_compact_section(s) for s in secs]}


def corridor_section(store: ProjectStore, design: dict, chainage: float) -> dict | None:
    rows = store.design_results(design["id"], "corridor")
    if not rows:
        return None
    data = store.design_result_data(rows[0]["id"]) or {}
    secs = data.get("sections") or []
    if not secs:
        return None
    k = int(np.argmin([abs(s["chainage"] - chainage) for s in secs]))
    return secs[k]


# ---------------------------------------------------------------------- structures
def structures_doc(store: ProjectStore, design: dict) -> list[dict]:
    return store.design_get(design["id"], "structures", []) or []


def _grade_fn(store: ProjectStore, design: dict):
    """Grade (%) of the design line at a chainage, or None when there is no vertical alignment."""
    vdoc = store.design_get(design["id"], "vertical")
    if not vdoc or len(vdoc.get("pvis") or []) < 2:
        return None
    va = VerticalAlignment.make(vdoc["pvis"])
    return lambda ch: float(np.asarray(va.grade_at(np.clip(ch, va.start_chainage, va.end_chainage)), float).ravel()[0])


def structures_payload(store: ProjectStore, design: dict, items: list[dict] | None = None) -> dict:
    """Stored structures with the catalogue the UI offers, the bill of quantities and the checks."""
    std, ctx = design_context(design)
    structures = structures_doc(store, design) if items is None else items
    soil = str((design.get("settings") or {}).get("drain_soil") or "clayey")
    checks = check_structures(structures, std, ctx, _grade_fn(store, design), soil)
    return {"structures": structures, "kinds": STRUCTURE_KINDS, "catalogue": structure_catalogue(),
            "quantities": bill_of_quantities(structures), "checks": _checks(checks), "soil": soil}


def put_structures(store: ProjectStore, design: dict, items: list[dict]) -> list[dict]:
    out = []
    next_id = 1
    for d in items:
        try:
            s = normalise_structure(d, next_id)
        except ValueError as e:
            raise ServiceError(str(e), 400)
        next_id = max(next_id, s["id"]) + 1
        out.append(s)
    store.design_set(design["id"], "structures", out)
    return out


_STREAM_RE = re.compile(r"stream|khola|river|nala|nadi|drain|culvert|creek", re.I)


def suggest_structures(store: ProjectStore, design: dict, params: dict) -> list[dict]:
    std, ctx = design_context(design)
    out: list[dict] = []
    latest = corridor_latest(store, design, full=False)
    soil = str(params.get("soil") or (design.get("settings") or {}).get("drain_soil") or "clayey")
    if latest and params.get("walls", True):
        mf = float(params.get("max_fill_height") or std.resolve("max_fill_height_without_wall").value or 6.0)
        mc = float(params.get("max_cut_depth") or std.resolve("max_cut_depth_without_wall").value or 8.0)
        out += suggest_walls(latest["sections"], mf, mc)
    if latest and params.get("drains", False):
        out += suggest_drains(latest["sections"], _grade_fn(store, design), soil, std,
                              catch_drain_offset=float(std.resolve("catch_drain_min_offset").value or 5.0),
                              toe_drain_max_fill=float(std.resolve("toe_drain_max_fill").value or 0.8))
    hdoc = horizontal_doc(store, design)
    if hdoc and params.get("culverts", True):
        al = horizontal_from_doc(hdoc)
        streams = [ln["coords"] for ln in store.lines(kind="feature") if _STREAM_RE.search(f"{ln.get('name', '')} {ln.get('layer', '')}")]
        sags: list[float] = []
        vdoc = store.design_get(design["id"], "vertical")
        if vdoc:
            va = VerticalAlignment.make(vdoc["pvis"])
            sags = [c.turning_chainage for c in va.curves if c.kind == "sag" and c.turning_chainage is not None]
        out += suggest_culverts(al, streams, sags, outlet_spacing=std.resolve("drain_outlet_max_spacing").value if params.get("outlets", True) else None)
    return out


# ---------------------------------------------------------------------- standards and overview
def standards_payload(design: dict) -> dict:
    std, ctx = design_context(design)
    params = [std.resolve(k, **ctx).as_dict() for k in STANDARD_KEYS]
    meta = std.meta
    options = {"classes": [{"value": c, "label": meta.get("class_labels", {}).get(c, c)} for c in meta.get("classes", [])],
               "terrains": [{"value": t, "label": meta.get("terrain_labels", {}).get(t, t)} for t in meta.get("terrains", [])],
               "road_types": [{"value": t, "label": meta.get("road_type_labels", {}).get(t, t)} for t in meta.get("road_types", [])],
               "surfaces": [{"value": t, "label": t} for t in meta.get("surfaces", ["bituminous", "gravel", "earthen", "concrete"])],
               "materials": [{"value": m, "label": meta.get("material_labels", {}).get(m, m)} for m in meta.get("materials", [])],
               "speeds": sorted({int(v) for row in (std.table("design_speed") or {}).get("values", {}).values() for v in row.values()}, reverse=True)}
    return {"standard": std.summary(), "available": list_standards(), "context": ctx, "parameters": params, "options": options,
            "deviations": (design.get("settings") or {}).get("deviations") or {}, "structure_kinds": STRUCTURE_KINDS}


def overview(store: ProjectStore, design: dict) -> dict:
    std, ctx = design_context(design)
    hdoc = horizontal_doc(store, design)
    vdoc = store.design_get(design["id"], "vertical")
    tdoc = store.design_get(design["id"], "templates")
    latest = store.design_results(design["id"], "corridor")
    structures = structures_doc(store, design)
    vertical_stale, stale_reasons = False, []
    if vdoc and hdoc and len(vdoc.get("pvis") or []) >= 2:
        vp = vertical_payload(store, design, vdoc, horizontal_from_doc(hdoc))
        vertical_stale, stale_reasons = vp["stale"], vp["stale_reasons"]
    upd = store.design_updated(design["id"])
    corridor_stale = bool(latest) and any(upd.get(k, "") > (latest[0].get("created") or "") for k in ("horizontal", "vertical", "templates"))
    stages = {
        "alignment": {"status": "done" if hdoc else "todo", "detail": f"{len(hdoc['ips'])} IPs" if hdoc else "no alignment"},
        "profile": {"status": ("stale" if vertical_stale else "done") if vdoc else "todo",
                    "detail": (f"{len(vdoc['pvis'])} PVIs ({vdoc.get('source', 'manual')})" + ("; out of date: " + "; ".join(stale_reasons) if vertical_stale else "")) if vdoc else "no vertical alignment"},
        "templates": {"status": "done" if tdoc else "default", "detail": f"{len(tdoc['templates'])} template(s), {len(tdoc.get('assignments') or [])} assignment(s)" if tdoc else "default template"},
        "earthworks": {"status": ("stale" if corridor_stale else "done") if latest else "todo",
                       "detail": (f"{latest[0]['summary'].get('sections')} sections, cut {latest[0]['summary']['totals']['cut']:.0f} m3, fill {latest[0]['summary']['totals']['fill']:.0f} m3"
                                  + ("; built before the latest alignment / profile / template change - rebuild" if corridor_stale else "") if latest else "no corridor yet")},
        "structures": {"status": "done" if any(STRUCTURE_KINDS[s["kind"]]["group"] == "wall" for s in structures) else "todo",
                       "detail": f"{sum(1 for s in structures if STRUCTURE_KINDS[s['kind']]['group'] == 'wall')} wall(s) of {len(structures)} structure(s)" if structures else "none"},
        "drainage": {"status": "done" if any(STRUCTURE_KINDS[s["kind"]]["group"] in ("cross", "drain") for s in structures) else "todo",
                     "detail": f"{sum(1 for s in structures if STRUCTURE_KINDS[s['kind']]['group'] in ('cross', 'drain'))} drainage structure(s)"},
        "output": {"status": "todo", "detail": "volumes CSV and setting-out tables available from the Earthworks stage"},
    }
    return {"context": ctx, "standard": std.summary()["id"], "standard_status": std.meta.get("status", "placeholder"), "stages": stages,
            "has_horizontal": bool(hdoc), "has_vertical": bool(vdoc), "has_corridor": bool(latest),
            "vertical_stale": vertical_stale, "corridor_stale": corridor_stale, "updated": upd, "corridor_created": latest[0].get("created") if latest else None}
