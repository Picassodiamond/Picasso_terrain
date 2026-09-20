"""Longitudinal profile and cross-sections of a TIN along a horizontal alignment.

Port of `FrmGenerateProCro.frm` (GenerateLsection / GetCrossXY / MakeXsection /
FindIntermediateProfilePts). Ground points outside the TIN are reported as NaN instead of the
legacy sentinel 0.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from .alignment import HorizontalAlignment, format_chainage
from .tin import TIN


@dataclass
class ProfilePoint:
    chainage: float
    x: float
    y: float
    z: float           # NaN outside the TIN
    source: str        # 'station' | 'edge' | 'curve'
    remark: str = ""


@dataclass
class CrossSection:
    chainage: float
    centre: tuple[float, float]
    direction: float                  # direction of travel (rad)
    left: float
    right: float
    offset: np.ndarray                # negative = left of centre line
    z: np.ndarray                     # NaN outside the TIN
    xy: np.ndarray                    # (k, 2)
    source: list[str] = field(default_factory=list)
    remarks: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return format_chainage(self.chainage)

    @property
    def corners(self) -> tuple[tuple[float, float], tuple[float, float]]:
        return (float(self.xy[0, 0]), float(self.xy[0, 1])), (float(self.xy[-1, 0]), float(self.xy[-1, 1]))


def generate_profile(
    tin: TIN,
    alignment: HorizontalAlignment,
    interval: float = 20.0,
    include_curve_points: bool = True,
    extra_chainages: Iterable[float] = (),
    include_edge_crossings: bool = True,
    curve_max_segment: float = 2.0,
) -> list[ProfilePoint]:
    """Ground profile along the alignment.

    Samples every station (interval + BC/MC/EC + user chainages) and, like the legacy code,
    every TIN edge crossing in between so the profile is exact on the TIN.
    """
    stations = alignment.stations(interval, include_curve_points, extra_chainages)
    dense_xy, dense_ch = alignment.densify(max_segment=curve_max_segment)
    # vertex table = stations + densified curve vertices (for accurate distance->chainage mapping)
    st_xy = np.array([alignment.point_at(c) for c in stations])
    all_ch = np.concatenate([dense_ch, stations])
    all_xy = np.vstack([dense_xy, st_xy])
    src = np.array(["curve"] * len(dense_ch) + ["station"] * len(stations))
    order = np.argsort(all_ch, kind="stable")
    all_ch, all_xy, src = all_ch[order], all_xy[order], src[order]
    keep = np.r_[True, np.diff(all_ch) > 1e-6]
    # when a station coincides with a densified vertex, keep the 'station' label
    for i in np.flatnonzero(~keep):
        if src[i] == "station":
            src[i - 1] = "station"
    all_ch, all_xy, src = all_ch[keep], all_xy[keep], src[keep]

    # distance along the polyline -> chainage mapping
    seg = np.hypot(*np.diff(all_xy, axis=0).T)
    dist = np.concatenate([[0.0], np.cumsum(seg)])
    sample = tin.sample_line(all_xy, include_vertices=True)
    ch = np.interp(sample.distance, dist, all_ch)

    # classify: vertex samples (station / curve) vs edge crossings - vectorised, a 100 km
    # centreline has tens of thousands of samples
    k = np.clip(np.searchsorted(dist, sample.distance), 0, len(dist) - 1)
    k_prev = np.maximum(k - 1, 0)
    k = np.where(np.abs(dist[k_prev] - sample.distance) < np.abs(dist[k] - sample.distance), k_prev, k)
    is_vertex = np.abs(dist[k] - sample.distance) <= 1e-6 * max(1.0, float(dist[-1]))
    kinds = np.where(is_vertex, src[k], "edge")
    keep = is_vertex | include_edge_crossings
    # densified curve vertices stay (labelled 'curve') because they give a smooth profile
    return [ProfilePoint(float(c), float(x), float(y), float(z), str(s))
            for c, (x, y), z, s, m in zip(ch, sample.xy, sample.z, kinds, keep) if m]


def generate_cross_sections(
    tin: TIN,
    alignment: HorizontalAlignment,
    interval: float = 20.0,
    left: float = 15.0,
    right: float = 15.0,
    include_curve_points: bool = True,
    extra_chainages: Iterable[float] = (),
    include_edge_crossings: bool = True,
) -> list[CrossSection]:
    """Cross-sections perpendicular to the alignment at every station.

    Each section samples the TIN from the left corner to the right corner with a vertex at the
    centre line (offset 0) and at every TIN edge crossing (legacy `Find_Int_RL`).
    """
    out: list[CrossSection] = []
    for ch in alignment.stations(interval, include_curve_points, extra_chainages):
        cx, cy, d = alignment.point_and_direction(ch)
        lx, ly = alignment.offset_point(ch, -left)
        rx, ry = alignment.offset_point(ch, right)
        line = np.array([[lx, ly], [cx, cy], [rx, ry]])
        s = tin.sample_line(line, include_vertices=True)
        offset = s.distance - left
        src = []
        for dd in s.distance:
            if abs(dd) < 1e-6:
                src.append("left")
            elif abs(dd - left) < 1e-6:
                src.append("centre")
            elif abs(dd - (left + right)) < 1e-6:
                src.append("right")
            else:
                src.append("edge")
        if not include_edge_crossings:
            m = np.array([k != "edge" for k in src])
            s_d, s_xy, s_z = s.distance[m], s.xy[m], s.z[m]
            offset = s_d - left
            src = [k for k in src if k != "edge"]
        else:
            s_xy, s_z = s.xy, s.z
        out.append(CrossSection(float(ch), (cx, cy), float(d), float(left), float(right),
                                offset, s_z, s_xy, src, [""] * len(src)))
    return out


# ------------------------------------------------------------------------------ legacy CSV
def profile_to_csv(points: Sequence[ProfilePoint], skip_outside: bool = True) -> str:
    """Legacy `Profile.csv`: `Chainage,RL,Remarks`."""
    lines = ["Chainage,RL,Remarks"]
    for p in points:
        if np.isnan(p.z):
            if skip_outside:
                continue
            lines.append(f"{p.chainage:.3f},,{p.remark}")
        else:
            lines.append(f"{p.chainage:.3f},{p.z:.3f},{p.remark}")
    return "\n".join(lines) + "\n"


def cross_sections_to_csv(sections: Sequence[CrossSection], skip_outside: bool = True) -> str:
    """Legacy `Cross.csv`: `Chainage,PD,RL,Remarks`; the chainage is written only on the first
    row of each section, PD is the offset from the centre line (negative left)."""
    lines = ["Chainage,PD,RL,Remarks"]
    for s in sections:
        first = True
        for off, z, rm in zip(s.offset, s.z, s.remarks):
            if np.isnan(z) and skip_outside:
                continue
            ch = f"{s.chainage:.3f}" if first else ""
            zs = "" if np.isnan(z) else f"{z:.3f}"
            lines.append(f"{ch},{off:.2f},{zs},{rm}")
            first = False
    return "\n".join(lines) + "\n"


def profile_summary(points: Sequence[ProfilePoint]) -> dict:
    z = np.array([p.z for p in points])
    inside = ~np.isnan(z)
    return {
        "points": len(points),
        "outside_tin": int((~inside).sum()),
        "z_min": float(np.nanmin(z)) if inside.any() else None,
        "z_max": float(np.nanmax(z)) if inside.any() else None,
        "chainage_start": float(points[0].chainage) if points else None,
        "chainage_end": float(points[-1].chainage) if points else None,
    }
