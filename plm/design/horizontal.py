"""Standards checks and the curve table for a horizontal alignment (NRS 2070 chapter 9 and cl. 19)."""
from __future__ import annotations

import numpy as np

from ..engine.alignment import HorizontalAlignment, format_chainage
from .standards import Check, Param, Standard, compare
from .superelevation import design_rate


def _extra_widening(std: Standard, radius: float, lanes: int) -> float | None:
    if lanes <= 1:
        p = std.resolve("extra_widening_single", radius=radius)
        return p.value
    if lanes == 2:
        return std.resolve("extra_widening_double", radius=radius).value
    per = std.resolve("extra_widening_per_lane_multi", radius=radius).value
    return None if per is None else per * lanes


def setback_distance(radius: float, sight_distance: float, n: float) -> float:
    """Set-back distance from the centre line to sight obstructions inside a curve (NRS 2070 eq. 9-1):
    m = R - (R - n) cos(S / (2 (R - n))), n = distance centre line to centre of the inside lane."""
    rn = max(radius - n, 1e-6)
    return float(radius - rn * np.cos(sight_distance / (2.0 * rn)))


def curve_table(al: HorizontalAlignment, std: Standard | None = None, ctx: dict | None = None) -> list[dict]:
    """One row per IP with the curve quantities engineers expect on a setting-out table, plus the
    NRS extra widening (Table 9-4) and the set-back distance for stopping sight distance (cl. 9.5)."""
    rows = []
    ctx = ctx or {}
    speed = float(ctx.get("design_speed") or 0)
    f = std.resolve("side_friction", design_speed=speed).value if std and speed else None
    e_max = std.resolve("max_superelevation", **ctx).value if std else None
    if std and ctx.get("snow_bound"):
        e_max = std.resolve("max_superelevation_snow_bound").value or e_max
    camber = std.resolve("camber", surface=ctx.get("surface", "bituminous")).value if std else None
    lanes = int(ctx.get("lanes") or (std.resolve("lanes", **ctx).value if std else 2) or 2)
    lane_w = float(ctx.get("lane_width") or (std.resolve("lane_width").value if std else 3.5) or 3.5)
    ssd = std.resolve("stopping_sight_distance", design_speed=speed).value if std and speed else None
    for g in al.geometry:
        e = None
        if g.radius > 0 and speed and f is not None and e_max is not None and camber is not None:
            e = round(design_rate(g.radius, speed, f, e_max, camber)[0], 2)
        widen = _extra_widening(std, g.radius, lanes) if std and g.radius > 0 else None
        setback = round(setback_distance(g.radius, ssd, lane_w / 2.0 if lanes >= 2 else 0.0), 2) if ssd and g.radius > 0 else None
        rows.append({
            "index": g.index, "label": g.label, "x": g.x, "y": g.y, "chainage": g.chainage, "chainage_text": format_chainage(g.chainage),
            "radius": g.radius, "transition": g.transition, "deflection_deg": float(np.degrees(g.deflection)),
            "tangent": g.tangent_length, "arc_length": g.curve_length, "total_length": g.total_length, "external": g.external,
            "spiral_angle_deg": float(np.degrees(g.spiral_angle)), "shift": g.shift,
            "ts": g.bc_chainage if g.total_length else None, "sc": g.sc_chainage, "cs": g.cs_chainage, "st": g.ec_chainage if g.total_length else None,
            "mc": g.mc_chainage if g.total_length else None, "turn": "left" if g.deflection > 0 else ("right" if g.deflection < 0 else ""),
            "superelevation_pct": e, "extra_widening": None if widen is None else round(widen, 2), "setback": setback,
            "hairpin": bool(std and g.radius > 0 and abs(np.degrees(g.deflection)) >= (std.resolve("hairpin_deflection").value or 120)),
            "valid": g.valid, "message": g.message,
        })
    return rows


def check_horizontal(al: HorizontalAlignment, std: Standard, ctx: dict) -> list[Check]:
    """Checks per curve against NRS 2070: absolute and recommended minimum radius (Table 9-1), the
    comfort radius, transition need (cl. 9.2 with the 0.25 m shift rule) and length (Table 9-2),
    clothoid aesthetics (cl. 19 d), hair-pin bends (Table 9-3, cl. 9.3 b), superelevation demand
    (cl. 11.6), plus the geometric issues the alignment itself reports."""
    checks: list[Check] = []
    speed = float(ctx.get("design_speed") or 0)
    super_on = bool(ctx.get("superelevation_enabled", True))
    p_rabs = std.resolve("min_radius_absolute", design_speed=speed)
    p_rrec = std.resolve("min_radius", design_speed=speed)
    p_rcomf = std.resolve("min_radius_comfort", design_speed=speed)
    p_need = std.resolve("transition_required_below_radius")
    p_shift = std.resolve("transition_shift_min")
    p_emax = std.resolve("max_superelevation", **ctx)
    if ctx.get("snow_bound"):
        p_emax = std.resolve("max_superelevation_snow_bound")
    p_f = std.resolve("side_friction", design_speed=speed)
    p_amin, p_amax = std.resolve("transition_a_ratio_min"), std.resolve("transition_a_ratio_max")
    p_frac = std.resolve("transition_min_fraction_of_arc")
    hp_defl = std.resolve("hairpin_deflection").value or 120.0
    p_hp_r, p_hp_ls, p_hp_sp = std.resolve("hairpin_min_radius"), std.resolve("hairpin_min_transition"), std.resolve("hairpin_min_spacing")
    p_hp_v = std.resolve("hairpin_min_speed")
    last_hairpin_end: float | None = None
    for g in al.geometry:
        if g.radius <= 0 or g.total_length <= 0:
            continue
        where = f"IP {g.label or g.index} (CH {format_chainage(g.chainage)})"
        R = g.radius
        # -- radius: absolute (with e_max), recommended (no superelevation), comfort
        checks.append(compare("min_radius_absolute", "Curve radius (absolute minimum)", R, p_rabs, kind="min", where=where, fmt="{:.1f}"))
        if p_rrec.value is not None:
            ok = R >= p_rrec.value - 1e-9
            msg = (f"radius {R:.1f} m at or above {p_rrec.value:.0f} m: normal camber may be kept ({p_rrec.source})" if ok else
                   f"radius {R:.1f} m is below the {p_rrec.value:.0f} m recommended without superelevation: "
                   + ("superelevation must be provided (see the superelevation check)" if super_on else "superelevation is disabled for this design"))
            checks.append(Check("min_radius", "Curve radius (recommended)", ok, R, p_rrec.value, "m", p_rrec.source, p_rrec.status, where, msg,
                                "info" if ok else ("warning" if super_on else "error")))
        if p_rcomf.value is not None:
            ok = R >= p_rcomf.value - 1e-9
            checks.append(Check("comfort_radius", "Curve radius (passenger comfort)", ok, R, p_rcomf.value, "m", p_rcomf.source, p_rcomf.status, where,
                                f"radius {R:.1f} m {'meets' if ok else 'is below'} the comfort radius {p_rcomf.value:.0f} m (lateral force 15 %)", "info"))
        # -- transitions
        p_ls = std.resolve("min_transition_length", radius=R)
        if p_need.value is not None and R < p_need.value:
            if g.transition <= 0:
                ls_min = p_ls.value or 0.0
                shift = ls_min ** 2 / (24.0 * R) if R > 0 else 0.0
                if p_shift.value is not None and shift < p_shift.value:
                    checks.append(Check("transition_required", "Transition curve", True, shift, p_shift.value, "m", p_shift.source, p_shift.status, where,
                                        f"no transition: the shift with the {ls_min:.0f} m table length would be {shift:.2f} m, below {p_shift.value:.2f} m, so it may be omitted", "info"))
                else:
                    checks.append(Check("transition_required", "Transition curve", False, R, p_need.value, "m", p_need.source, p_need.status, where,
                                        f"radius {R:.0f} m is below {p_need.value:.0f} m: a transition curve of at least {ls_min:.0f} m is required ({p_need.source})", "warning"))
            else:
                checks.append(compare("min_transition", "Transition length", g.transition, p_ls, kind="min", where=where, fmt="{:.0f}"))
        if g.transition > 0 and g.curve_length > 0:
            A = float(np.sqrt(R * g.transition))
            if p_amin.value is not None and p_amax.value is not None:
                ok = p_amin.value * R - 1e-9 <= A <= p_amax.value * R + 1e-9
                checks.append(Check("clothoid_parameter", "Clothoid parameter A", ok, round(A, 1), round(p_amin.value * R, 1), "m", p_amin.source, p_amin.status, where,
                                    f"A = sqrt(R L) = {A:.0f} m {'within' if ok else 'outside'} {p_amin.value:g} R - {p_amax.value:g} R ({p_amin.value * R:.0f} - {p_amax.value * R:.0f} m)",
                                    "info" if ok else "warning"))
            if p_frac.value is not None:
                need = p_frac.value * g.curve_length
                ok = g.transition >= need - 1e-9
                checks.append(Check("transition_vs_arc", "Transition vs arc length", ok, g.transition, round(need, 1), "m", p_frac.source, p_frac.status, where,
                                    f"transition {g.transition:.0f} m {'is' if ok else 'is not'} at least 1/4 of the arc ({g.curve_length:.0f} m)", "info" if ok else "warning"))
        # -- hair-pin bends
        if abs(np.degrees(g.deflection)) >= hp_defl:
            hw = f"{where} hair-pin"
            checks.append(compare("hairpin_radius", "Hair-pin radius", R, p_hp_r, kind="min", where=hw, severity="error", fmt="{:.1f}"))
            checks.append(compare("hairpin_transition", "Hair-pin transition", g.transition, p_hp_ls, kind="min", where=hw, severity="warning", fmt="{:.0f}"))
            if p_hp_v.value is not None and speed and speed < p_hp_v.value:
                checks.append(Check("hairpin_speed", "Hair-pin design speed", False, speed, p_hp_v.value, "km/h", p_hp_v.source, p_hp_v.status, hw,
                                    f"design speed {speed:.0f} km/h is below the {p_hp_v.value:.0f} km/h minimum for hair-pin bends", "warning"))
            if last_hairpin_end is not None and p_hp_sp.value is not None:
                gap = g.bc_chainage - last_hairpin_end
                checks.append(Check("hairpin_spacing", "Hair-pin spacing", gap >= p_hp_sp.value - 1e-9, round(gap, 1), p_hp_sp.value, "m", p_hp_sp.source, p_hp_sp.status, hw,
                                    f"{gap:.0f} m from the previous hair-pin bend ({'ok' if gap >= p_hp_sp.value else 'less than'} {p_hp_sp.value:.0f} m)",
                                    "info" if gap >= p_hp_sp.value else "warning"))
            last_hairpin_end = g.ec_chainage
        # -- superelevation demand
        if p_emax.value is not None and p_f.value is not None and speed:
            e, feasible = design_rate(R, speed, p_f.value, p_emax.value, 0.0)
            checks.append(Check("superelevation", "Superelevation demand", feasible, round(e, 2), p_emax.value, "%", p_emax.source, p_emax.status, where,
                                f"needs e = {e:.1f} % {'within' if feasible else 'above'} e_max {p_emax.value:.0f} % at {speed:.0f} km/h (f = {p_f.value:.2f})",
                                "info" if feasible else "error"))
    # -- long straights (aesthetics, cl. 19 g)
    p_straight = std.resolve("max_straight_length")
    if p_straight.value is not None:
        for el in al.elements:
            if el.kind == "tangent" and el.length > p_straight.value:
                checks.append(Check("long_straight", "Straight length", False, round(el.length, 0), p_straight.value, "m", p_straight.source, p_straight.status,
                                    f"CH {format_chainage(el.start_chainage)} - {format_chainage(el.end_chainage)}",
                                    f"straight of {el.length:.0f} m exceeds {p_straight.value:.0f} m", "info"))
    for iss in al.issues:
        sev = "error" if iss.kind in ("overlap", "zero_length") else "warning"
        checks.append(Check(iss.kind, iss.kind.replace("_", " "), False, None, None, "", "geometry", "verified",
                            f"IP {iss.ip_index}", iss.message, sev))
    return checks


def eased_param(p: Param, easing: float, why: str) -> Param:
    """A copy of a limit parameter reduced by `easing` (gradient compensation), keeping its provenance."""
    if p.value is None or easing <= 0:
        return p
    return Param(p.key, round(p.value - easing, 3), p.unit, f"{p.source}; eased by {easing:g} {p.unit} {why}", p.status, p.note)
