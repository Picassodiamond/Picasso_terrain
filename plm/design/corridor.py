"""Corridor: sweep the assigned templates along the alignment over the terrain.

For every station: ground line across the road (from the TIN), design centreline level (vertical
alignment), cross slopes (superelevation or normal camber), the template's hinge polylines, then
daylight on each side - cut with ditch and benches, or fill with optional benches - and the cut and
fill areas between the design and the ground. Everything is piecewise linear, so intersections and
areas are exact for the sampled ground line.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

from ..engine.alignment import HorizontalAlignment
from ..engine.tin import TIN
from . import earthworks
from .superelevation import SuperelevationProfile
from .template import hinge_polyline, normalise_template, template_at
from .vertical import VerticalAlignment


@dataclass
class SideResult:
    kind: str                       # cut | fill | level | no_ground | no_catch_cut | no_catch_fill
    hinge_offset: float             # signed (negative = left)
    hinge_z: float
    ground_z_at_hinge: float | None
    height: float                   # design - ground at the hinge (+ fill, - cut), 0 when unknown
    catch_offset: float | None      # signed daylight offset
    points: list[tuple[float, float]] = field(default_factory=list)  # signed (offset, z) from hinge outward
    benches: int = 0

    def as_dict(self) -> dict:
        return {"kind": self.kind, "hinge_offset": self.hinge_offset, "hinge_z": self.hinge_z, "ground_z_at_hinge": self.ground_z_at_hinge,
                "height": self.height, "catch_offset": self.catch_offset, "benches": self.benches,
                "points": [[round(o, 3), round(z, 3)] for o, z in self.points]}


@dataclass
class SectionResult:
    chainage: float
    template_id: str
    x: float
    y: float
    direction: float
    design_z: float
    ground_z: float | None
    slopes: tuple[float, float]
    design: list[tuple[float, float]]       # signed offset, z from left daylight to right daylight
    ground: list[tuple[float, float]]       # signed offset, z (finite samples only)
    left: SideResult
    right: SideResult
    cut_area: float
    fill_area: float
    flags: list[str] = field(default_factory=list)

    def as_dict(self, full: bool = True) -> dict:
        d = {"chainage": self.chainage, "template_id": self.template_id, "x": self.x, "y": self.y, "direction": self.direction,
             "design_z": self.design_z, "ground_z": self.ground_z, "slopes": list(self.slopes), "cut_area": round(self.cut_area, 4),
             "fill_area": round(self.fill_area, 4), "flags": self.flags,
             "left": {k: v for k, v in self.left.as_dict().items() if full or k != "points"},
             "right": {k: v for k, v in self.right.as_dict().items() if full or k != "points"}}
        if full:
            d["design"] = [[round(o, 3), round(z, 3)] for o, z in self.design]
            d["ground"] = [[round(o, 3), round(z, 3)] for o, z in self.ground]
        return d


@dataclass
class CorridorResult:
    sections: list[SectionResult]
    volumes: list[dict]
    mass_haul: list[dict]
    totals: dict
    params: dict

    def summary(self) -> dict:
        n = len(self.sections)
        return {"sections": n, "start": self.sections[0].chainage if n else None, "end": self.sections[-1].chainage if n else None,
                "totals": self.totals, "flags": sorted({f for s in self.sections for f in s.flags}), "params": self.params}


# ---------------------------------------------------------------------- piecewise-linear helpers
def _interp(o: np.ndarray, z: np.ndarray, q: float) -> float:
    if len(o) == 0 or q < o[0] - 1e-9 or q > o[-1] + 1e-9:
        return float("nan")
    return float(np.interp(q, o, z))


def _first_hit(o0: float, z0: float, m: float, go: np.ndarray, gz: np.ndarray, o_max: float) -> tuple[float, float] | None:
    """First intersection, outward from o0, of the design line z = z0 + m (o - o0) with the ground."""
    for k in range(len(go) - 1):
        ob = float(go[k + 1])
        if ob <= o0 + 1e-9:
            continue
        oa = max(float(go[k]), o0)
        if oa > o_max + 1e-9:
            break
        za = float(np.interp(oa, go[k:k + 2], gz[k:k + 2]))
        ob_c = min(ob, o_max)
        zb_c = float(np.interp(ob_c, go[k:k + 2], gz[k:k + 2]))
        fa = z0 + m * (oa - o0) - za
        fb = z0 + m * (ob_c - o0) - zb_c
        if abs(fa) < 1e-9 and oa > o0 + 1e-9:
            return oa, za
        if fa * fb < 0:
            t = fa / (fa - fb)
            oh = oa + t * (ob_c - oa)
            return oh, z0 + m * (oh - o0)
        if ob >= o_max:
            break
    return None


def daylight_side(hinge_o: float, hinge_z: float, go: np.ndarray, gz: np.ndarray, cut_cfg: dict, fill_cfg: dict, max_offset: float,
                  tol: float = 0.01) -> tuple[str, list[tuple[float, float]], float | None, float, int, float | None]:
    """Daylight one side in outward coordinates (offset >= 0 increasing away from the centreline).

    Returns (kind, points from the hinge outward, catch offset, height, benches, ground z at hinge)."""
    gz_h = _interp(go, gz, hinge_o)
    if not np.isfinite(gz_h):
        return "no_ground", [(hinge_o, hinge_z)], None, 0.0, 0, None
    height = hinge_z - gz_h
    if abs(height) <= tol:
        return "level", [(hinge_o, hinge_z)], hinge_o, height, 0, gz_h
    pts = [(hinge_o, hinge_z)]
    o, z = hinge_o, hinge_z
    benches = 0
    if height < 0:  # cut: the ground is above the road
        ditch = cut_cfg.get("ditch")
        if ditch:
            d, fs, b = float(ditch.get("depth", 0.5)), float(ditch.get("foreslope", 1.0)), float(ditch.get("bottom", 0.5))
            # a ditch only makes sense if it stays below the ground
            hit = _first_hit(o, z, -1.0 / max(fs, 1e-6), go, gz, min(o + d * fs, max_offset))
            if hit:
                pts.append(hit)
                return "cut", pts, hit[0], height, 0, gz_h
            o, z = o + d * fs, z - d
            pts.append((o, z))
            hit = _first_hit(o, z, 0.0, go, gz, min(o + b, max_offset))
            if hit:
                pts.append(hit)
                return "cut", pts, hit[0], height, 0, gz_h
            o += b
            pts.append((o, z))
        s = max(float(cut_cfg.get("slope", 1.0)), 1e-6)
        bh, bw = float(cut_cfg.get("bench_height", 0.0) or 0.0), float(cut_cfg.get("bench_width", 0.0) or 0.0)
        m = 1.0 / s
        for _ in range(64):
            o_limit = o + (bh * s if bh > 0 else max_offset)
            hit = _first_hit(o, z, m, go, gz, min(o_limit, max_offset))
            if hit:
                pts.append(hit)
                return "cut", pts, hit[0], height, benches, gz_h
            if o_limit >= max_offset or bh <= 0:
                pts.append((max_offset, z + m * (max_offset - o)))
                return "no_catch_cut", pts, None, height, benches, gz_h
            o, z = o_limit, z + bh
            pts.append((o, z))
            benches += 1
            hit = _first_hit(o, z, 0.0, go, gz, min(o + bw, max_offset))
            if hit:
                pts.append(hit)
                return "cut", pts, hit[0], height, benches, gz_h
            o += bw
            pts.append((o, z))
        return "no_catch_cut", pts, None, height, benches, gz_h
    # fill: the ground is below the road
    s = max(float(fill_cfg.get("slope", 1.5)), 1e-6)
    bh, bw = float(fill_cfg.get("bench_height", 0.0) or 0.0), float(fill_cfg.get("bench_width", 0.0) or 0.0)
    m = -1.0 / s
    for _ in range(64):
        o_limit = o + (bh * s if bh > 0 else max_offset)
        hit = _first_hit(o, z, m, go, gz, min(o_limit, max_offset))
        if hit:
            pts.append(hit)
            return "fill", pts, hit[0], height, benches, gz_h
        if o_limit >= max_offset or bh <= 0:
            pts.append((max_offset, z + m * (max_offset - o)))
            return "no_catch_fill", pts, None, height, benches, gz_h
        o, z = o_limit, z - bh
        pts.append((o, z))
        benches += 1
        hit = _first_hit(o, z, 0.0, go, gz, min(o + bw, max_offset))
        if hit:
            pts.append(hit)
            return "fill", pts, hit[0], height, benches, gz_h
        o += bw
        pts.append((o, z))
    return "no_catch_fill", pts, None, height, benches, gz_h


def areas(design: list[tuple[float, float]], go: np.ndarray, gz: np.ndarray) -> tuple[float, float]:
    """(cut, fill) areas between a design polyline (signed offsets, increasing) and the ground line,
    over the design extent where ground exists. Exact for piecewise-linear lines."""
    if len(design) < 2 or len(go) < 2:
        return 0.0, 0.0
    do = np.array([p[0] for p in design])
    dz = np.array([p[1] for p in design])
    order = np.argsort(do, kind="stable")
    do, dz = do[order], dz[order]
    lo, hi = max(do[0], go[0]), min(do[-1], go[-1])
    if hi <= lo:
        return 0.0, 0.0
    xs = np.unique(np.concatenate([[lo, hi], do[(do > lo) & (do < hi)], go[(go > lo) & (go < hi)]]))
    d = np.interp(xs, do, dz) - np.interp(xs, go, gz)
    cut = fill = 0.0
    for i in range(len(xs) - 1):
        w = xs[i + 1] - xs[i]
        a, b = d[i], d[i + 1]
        if a >= 0 and b >= 0:
            fill += (a + b) / 2 * w
        elif a <= 0 and b <= 0:
            cut += -(a + b) / 2 * w
        else:
            t = a / (a - b)
            wa, wb = w * t, w * (1 - t)
            if a > 0:
                fill += a * wa / 2
                cut += -b * wb / 2
            else:
                cut += -a * wa / 2
                fill += b * wb / 2
    return float(cut), float(fill)


# ---------------------------------------------------------------------- sections
def ground_line(tin: TIN, al: HorizontalAlignment, chainage: float, swath: float) -> tuple[np.ndarray, np.ndarray, float | None]:
    """Signed offsets and elevations of the ground across the alignment at a chainage (finite samples)."""
    x, y, d = al.point_and_direction(chainage)
    lx, ly = al.offset_point(chainage, -swath)
    rx, ry = al.offset_point(chainage, swath)
    s = tin.sample_line(np.array([[lx, ly], [x, y], [rx, ry]]), include_vertices=True)
    off = s.distance - swath
    ok = np.isfinite(s.z)
    zc = tin.elevation_at([x], [y])[0]
    return off[ok], s.z[ok], (None if np.isnan(zc) else float(zc))


def build_section(tin: TIN, al: HorizontalAlignment, vertical: VerticalAlignment, templates: list[dict], assignments: list[dict],
                  supere: SuperelevationProfile | None, chainage: float, swath: float | None = None) -> SectionResult:
    tmpl = template_at(templates, assignments, chainage)
    max_off = float(tmpl.get("max_offset", 60.0))
    sw = float(swath or max_off)
    go, gz, zc_ground = ground_line(tin, al, chainage, sw)
    x, y, d = al.point_and_direction(chainage)
    z_c = float(vertical.elevation_at([chainage])[0])
    sl_l, sl_r = supere.slopes_at(chainage) if supere else (None, None)
    flags: list[str] = []
    sides: dict[str, SideResult] = {}
    for side, sgn, slope in (("left", -1.0, sl_l), ("right", 1.0, sl_r)):
        hinge = hinge_polyline(tmpl, side, z_c, slope)
        ho, hz = hinge[-1]
        if sgn < 0:
            m = go <= 1e-9
            g_o, g_z = -go[m][::-1], gz[m][::-1]
        else:
            m = go >= -1e-9
            g_o, g_z = go[m], gz[m]
        kind, pts, catch, height, benches, gzh = daylight_side(ho, hz, g_o, g_z, tmpl["cut"], tmpl["fill"], max_off)
        if kind.startswith("no_"):
            flags.append(f"{kind}_{side}")
        sides[side] = SideResult(kind, sgn * ho, hz, gzh, height, sgn * catch if catch is not None else None,
                                 [(sgn * o, z) for o, z in hinge[1:]] + [(sgn * o, z) for o, z in pts[1:]], benches)
    # assemble the full design polyline left -> right
    design: list[tuple[float, float]] = list(reversed(sides["left"].points)) + [(0.0, z_c)] + sides["right"].points
    cut_a, fill_a = areas(design, go, gz)
    if zc_ground is None:
        flags.append("no_ground_at_centreline")
    return SectionResult(float(chainage), tmpl["id"], float(x), float(y), float(d), z_c, zc_ground, (sl_l if sl_l is not None else 0.0, sl_r if sl_r is not None else 0.0),
                         design, [(float(o), float(z)) for o, z in zip(go, gz)], sides["left"], sides["right"], cut_a, fill_a, flags)


def build_corridor(tin: TIN, al: HorizontalAlignment, vertical: VerticalAlignment, templates: list[dict], assignments: list[dict],
                   supere: SuperelevationProfile | None = None, *, interval: float = 20.0, extra_chainages: Iterable[float] = (),
                   include_curve_points: bool = True, prismoidal: bool = False, cut_factor: float = 1.0, fill_factor: float = 1.0,
                   swath: float | None = None, progress=None) -> CorridorResult:
    templates = [normalise_template(t) for t in templates] or []
    if not templates:
        raise ValueError("at least one template is required")
    s0 = max(al.start_chainage, vertical.start_chainage)
    s1 = min(al.end_chainage, vertical.end_chainage)
    if s1 <= s0:
        raise ValueError("the vertical alignment does not overlap the horizontal alignment")
    st = al.stations(interval, include_curve_points, extra_chainages)
    st = st[(st >= s0 - 1e-6) & (st <= s1 + 1e-6)]
    if len(st) < 2 or st[0] > s0 + 1e-6:
        st = np.unique(np.concatenate([[s0], st]))
    if st[-1] < s1 - 1e-6:
        st = np.unique(np.concatenate([st, [s1]]))
    sections: list[SectionResult] = []
    for i, ch in enumerate(st):
        sections.append(build_section(tin, al, vertical, templates, assignments, supere, float(ch), swath))
        if progress and i % 25 == 0:
            progress(0.1 + 0.8 * i / max(len(st), 1), f"section {i + 1}/{len(st)}")
    mids = None
    if prismoidal:
        mids = [build_section(tin, al, vertical, templates, assignments, supere, float((a + b) / 2), swath) for a, b in zip(st[:-1], st[1:])]
    vols = earthworks.volumes([s.chainage for s in sections], [s.cut_area for s in sections], [s.fill_area for s in sections],
                              [m.cut_area for m in mids] if mids else None, [m.fill_area for m in mids] if mids else None)
    mh = earthworks.mass_haul(vols, cut_factor=cut_factor, fill_factor=fill_factor)
    totals = earthworks.totals(vols, mh)
    params = {"interval": interval, "prismoidal": prismoidal, "cut_factor": cut_factor, "fill_factor": fill_factor, "swath": swath,
              "templates": [t["id"] for t in templates], "stations": len(sections)}
    return CorridorResult(sections, vols, mh, totals, params)
