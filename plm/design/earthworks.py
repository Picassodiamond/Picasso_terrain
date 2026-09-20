"""Earthwork quantities: end-area and prismoidal volumes, mass haul diagram."""
from __future__ import annotations

from typing import Sequence


def volumes(chainages: Sequence[float], cut_areas: Sequence[float], fill_areas: Sequence[float],
            mid_cut: Sequence[float] | None = None, mid_fill: Sequence[float] | None = None) -> list[dict]:
    """Volume between consecutive stations.

    Average end area V = L (A1 + A2) / 2, or prismoidal V = L (A1 + 4 Am + A2) / 6 when mid-station
    areas are supplied (one per interval)."""
    out = []
    for i in range(len(chainages) - 1):
        L = float(chainages[i + 1] - chainages[i])
        c1, c2, f1, f2 = float(cut_areas[i]), float(cut_areas[i + 1]), float(fill_areas[i]), float(fill_areas[i + 1])
        if mid_cut is not None and mid_fill is not None:
            vc = L * (c1 + 4 * float(mid_cut[i]) + c2) / 6.0
            vf = L * (f1 + 4 * float(mid_fill[i]) + f2) / 6.0
            method = "prismoidal"
        else:
            vc = L * (c1 + c2) / 2.0
            vf = L * (f1 + f2) / 2.0
            method = "end_area"
        out.append({"from": float(chainages[i]), "to": float(chainages[i + 1]), "length": L, "cut": vc, "fill": vf, "method": method})
    return out


def mass_haul(vols: Sequence[dict], cut_factor: float = 1.0, fill_factor: float = 1.0) -> list[dict]:
    """Cumulative (cut x cut_factor - fill x fill_factor) at the end of every interval, starting at zero.

    cut_factor < 1 models shrinkage of excavated material placed as fill; fill_factor > 1 models
    compaction demand. Balance points are where the cumulative curve crosses zero."""
    out = [{"chainage": float(vols[0]["from"]) if vols else 0.0, "cumulative": 0.0, "balance": False}]
    cum = 0.0
    for v in vols:
        prev = cum
        cum += v["cut"] * cut_factor - v["fill"] * fill_factor
        out.append({"chainage": float(v["to"]), "cumulative": cum, "balance": bool(prev * cum < 0 or abs(cum) < 1e-9)})
    return out


def totals(vols: Sequence[dict], mh: Sequence[dict]) -> dict:
    cut = sum(v["cut"] for v in vols)
    fill = sum(v["fill"] for v in vols)
    cum = [m["cumulative"] for m in mh] or [0.0]
    return {"cut": cut, "fill": fill, "net": cum[-1], "max_surplus": max(cum), "max_deficit": min(cum),
            "balance_points": [m["chainage"] for m in mh[1:] if m["balance"]], "length": (vols[-1]["to"] - vols[0]["from"]) if vols else 0.0}
