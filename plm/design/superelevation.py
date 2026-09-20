"""Superelevation: rate for a curve and its development (runoff) along the alignment.

Conventions: cross slopes are percentages of rise per metre measured outward from the centreline,
so a normal camber c appears as -c on both sides (the pavement falls away from the crown), and a
fully superelevated section is +e on the outer side and -e on the inner side (one plane falling
towards the inside of the curve). Rotation is about the centreline.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..engine.alignment import HorizontalAlignment


def required_rate(radius: float, speed_kmh: float, side_friction: float) -> float:
    """e (fraction) from e + f = V^2 / (127 R); negative when friction alone suffices."""
    if radius <= 0:
        return 0.0
    return speed_kmh * speed_kmh / (127.0 * radius) - side_friction


def design_rate(radius: float, speed_kmh: float, side_friction: float, e_max_pct: float, camber_pct: float) -> tuple[float, bool]:
    """(e in %, feasible). e is the required rate clamped to [camber, e_max]; feasible is False when
    even e_max cannot supply the demand (radius below the minimum for this speed)."""
    need = required_rate(radius, speed_kmh, side_friction) * 100.0
    feasible = need <= e_max_pct + 1e-9
    return float(min(max(need, camber_pct), e_max_pct)), feasible


@dataclass
class CurveSuper:
    ip_index: int
    radius: float
    e_pct: float
    feasible: bool
    runoff: float           # length over which the section rotates from normal camber to full e
    start: float            # chainage where rotation starts (entry)
    full_from: float        # full superelevation reached
    full_to: float          # full superelevation ends
    end: float              # back to normal camber
    turn: int               # +1 left, -1 right


@dataclass
class SuperelevationProfile:
    camber_pct: float
    curves: list[CurveSuper] = field(default_factory=list)

    def slopes_at(self, chainage: float) -> tuple[float, float]:
        """(left, right) cross slopes in % at a chainage (outward rise per metre)."""
        c = -self.camber_pct
        left, right = c, c
        for cs in self.curves:
            if cs.start <= chainage <= cs.end:
                if chainage < cs.full_from:
                    f = (chainage - cs.start) / max(cs.full_from - cs.start, 1e-9)
                elif chainage <= cs.full_to:
                    f = 1.0
                else:
                    f = (cs.end - chainage) / max(cs.end - cs.full_to, 1e-9)
                f = min(max(f, 0.0), 1.0)
                outer = c + f * (cs.e_pct - c)          # -c -> +e
                inner = c + f * (-cs.e_pct - c)         # -c -> -e
                if cs.turn > 0:  # left turn: inside is left
                    left, right = inner, outer
                else:
                    left, right = outer, inner
                break
        return float(left), float(right)

    def table(self, step: float = 10.0, s0: float = 0.0, s1: float = 0.0) -> list[dict]:
        out = []
        for ch in np.arange(s0, s1 + step / 2, step):
            lft, rgt = self.slopes_at(float(ch))
            out.append({"chainage": float(ch), "left": round(lft, 3), "right": round(rgt, 3)})
        return out


def build_profile(al: HorizontalAlignment, *, speed_kmh: float, side_friction: float, e_max_pct: float, camber_pct: float,
                  relative_gradient_pct: float, rotated_width: float, runoff_on_tangent: float = 2.0 / 3.0) -> SuperelevationProfile:
    """Superelevation along a whole alignment.

    Runoff length: the transition length when the curve has spirals (full e reached at SC), otherwise
    e * w / relative gradient placed `runoff_on_tangent` (default 2/3) before BC and the rest inside
    the curve. Consecutive curves whose developments would overlap are trimmed at the midpoint."""
    prof = SuperelevationProfile(camber_pct)
    for g in al.geometry:
        if g.radius <= 0 or g.total_length <= 0 or not g.valid:
            continue
        e, feasible = design_rate(g.radius, speed_kmh, side_friction, e_max_pct, camber_pct)
        if g.transition > 0:
            runoff = g.transition
            start, full_from = g.bc_chainage, g.sc_chainage if g.sc_chainage is not None else g.bc_chainage + runoff
            full_to, end = (g.cs_chainage if g.cs_chainage is not None else g.ec_chainage - runoff), g.ec_chainage
        else:
            runoff = max((e / 100.0) * rotated_width / max(relative_gradient_pct / 100.0, 1e-6), 1.0)
            start = g.bc_chainage - runoff * runoff_on_tangent
            full_from = start + runoff
            end = g.ec_chainage + runoff * runoff_on_tangent
            full_to = end - runoff
            if full_to < full_from:  # short curve: peak at the middle
                full_from = full_to = g.mc_chainage
        prof.curves.append(CurveSuper(g.index, g.radius, e, feasible, runoff, float(start), float(full_from), float(full_to), float(end),
                                      1 if g.deflection > 0 else -1))
    # trim overlaps between successive developments
    for a, b in zip(prof.curves[:-1], prof.curves[1:]):
        if a.end > b.start:
            mid = (a.full_to + b.full_from) / 2.0
            a.end = b.start = float(min(max(mid, a.full_to), b.full_from))
    return prof
