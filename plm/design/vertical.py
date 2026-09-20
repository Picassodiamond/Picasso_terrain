"""Vertical alignment: PVIs joined by grades with symmetric parabolic vertical curves.

Grade g in %, curve length L, algebraic difference A = g2 - g1 (%), K = L / |A| (metres per % of
grade change). On a curve starting at BVC: z(x) = z_BVC + g1 x / 100 + (g2 - g1) x^2 / (200 L).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from ..engine.alignment import format_chainage
from .standards import Check, Param, Standard, compare


@dataclass
class PVI:
    chainage: float
    elevation: float
    length: float = 0.0     # vertical curve length (0 = grade break)
    label: str = ""


@dataclass
class VerticalCurve:
    index: int              # PVI index
    chainage: float
    elevation: float
    length: float
    g_in: float             # %
    g_out: float            # %
    A: float                # g_out - g_in
    K: float | None
    kind: str               # crest | sag | none
    bvc: float
    evc: float
    z_bvc: float
    z_evc: float
    turning_chainage: float | None = None
    turning_elevation: float | None = None
    valid: bool = True
    message: str = ""


@dataclass
class VerticalIssue:
    index: int
    kind: str
    message: str


class VerticalAlignment:
    def __init__(self, pvis: Sequence[PVI]):
        pv = sorted((PVI(float(p.chainage), float(p.elevation), float(p.length or 0.0), str(p.label or "")) for p in pvis), key=lambda p: p.chainage)
        if len(pv) < 2:
            raise ValueError("a vertical alignment needs at least two PVIs")
        self.pvis: list[PVI] = pv
        self.issues: list[VerticalIssue] = []
        self._compute()

    # ------------------------------------------------------------------ core
    def _compute(self) -> None:
        pv = self.pvis
        ch = np.array([p.chainage for p in pv])
        z = np.array([p.elevation for p in pv])
        d = np.diff(ch)
        for i, dd in enumerate(d):
            if dd <= 1e-9:
                self.issues.append(VerticalIssue(i + 1, "duplicate_chainage", f"PVI {i} and PVI {i + 1} are at the same chainage"))
        d[d <= 1e-9] = 1e-9
        self.grades = np.diff(z) / d * 100.0   # % per tangent
        pv[0].length = 0.0
        pv[-1].length = 0.0
        self.curves: list[VerticalCurve] = []
        for i in range(1, len(pv) - 1):
            g1, g2 = float(self.grades[i - 1]), float(self.grades[i])
            L = max(float(pv[i].length), 0.0)
            A = g2 - g1
            kind = "crest" if A < -1e-9 else ("sag" if A > 1e-9 else "none")
            K = (L / abs(A)) if L > 0 and abs(A) > 1e-9 else None
            bvc, evc = pv[i].chainage - L / 2, pv[i].chainage + L / 2
            z_bvc = pv[i].elevation - g1 * (L / 2) / 100.0
            z_evc = pv[i].elevation + g2 * (L / 2) / 100.0
            tc = tz = None
            if L > 0 and abs(A) > 1e-9:
                x = -g1 * L / A  # dz/dx = g1 + A x / L = 0
                if 0 < x < L:
                    tc = bvc + x
                    tz = z_bvc + g1 * x / 100.0 + A * x * x / (200.0 * L)
            self.curves.append(VerticalCurve(i, pv[i].chainage, pv[i].elevation, L, g1, g2, A, K, kind, bvc, evc, z_bvc, z_evc, tc, tz))
        # overlaps: half lengths must fit on each tangent
        for a, b in zip(self.curves[:-1], self.curves[1:]):
            if a.evc > b.bvc + 1e-9:
                a.valid = b.valid = False
                msg = f"vertical curves at PVI {a.index} and PVI {b.index} overlap ({a.evc - b.bvc:.1f} m)"
                a.message = b.message = msg
                self.issues.append(VerticalIssue(b.index, "overlap", msg))
        if self.curves:
            first, last = self.curves[0], self.curves[-1]
            if first.bvc < pv[0].chainage - 1e-9:
                first.valid = False
                first.message = "curve starts before the first PVI"
                self.issues.append(VerticalIssue(first.index, "overlap", first.message))
            if last.evc > pv[-1].chainage + 1e-9:
                last.valid = False
                last.message = "curve ends after the last PVI"
                self.issues.append(VerticalIssue(last.index, "overlap", last.message))

    # ------------------------------------------------------------------ queries
    @property
    def start_chainage(self) -> float:
        return self.pvis[0].chainage

    @property
    def end_chainage(self) -> float:
        return self.pvis[-1].chainage

    @property
    def is_valid(self) -> bool:
        return not self.issues

    def elevation_at(self, chainage) -> np.ndarray:
        """Design level at chainage(s); extrapolates the end grades."""
        ch = np.atleast_1d(np.asarray(chainage, dtype=float))
        pv_ch = np.array([p.chainage for p in self.pvis])
        pv_z = np.array([p.elevation for p in self.pvis])
        seg = np.clip(np.searchsorted(pv_ch, ch, side="right") - 1, 0, len(pv_ch) - 2)
        z = pv_z[seg] + self.grades[seg] * (ch - pv_ch[seg]) / 100.0
        for c in self.curves:
            if c.length <= 0:
                continue
            m = (ch >= c.bvc) & (ch <= c.evc)
            if m.any():
                x = ch[m] - c.bvc
                z[m] = c.z_bvc + c.g_in * x / 100.0 + c.A * x * x / (200.0 * c.length)
        return z

    def grade_at(self, chainage) -> np.ndarray:
        ch = np.atleast_1d(np.asarray(chainage, dtype=float))
        pv_ch = np.array([p.chainage for p in self.pvis])
        seg = np.clip(np.searchsorted(pv_ch, ch, side="right") - 1, 0, len(pv_ch) - 2)
        g = self.grades[seg].copy()
        for c in self.curves:
            if c.length <= 0:
                continue
            m = (ch >= c.bvc) & (ch <= c.evc)
            if m.any():
                g[m] = c.g_in + c.A * (ch[m] - c.bvc) / c.length
        return g

    def densify(self, step: float = 5.0) -> list[dict]:
        chs = list(np.arange(self.start_chainage, self.end_chainage, step)) + [self.end_chainage]
        for c in self.curves:
            chs += [c.bvc, c.evc]
            if c.turning_chainage is not None:
                chs.append(c.turning_chainage)
        chs = np.unique(np.round(np.clip(np.array(chs), self.start_chainage, self.end_chainage), 6))
        z = self.elevation_at(chs)
        return [{"chainage": float(c), "z": float(v)} for c, v in zip(chs, z)]

    def table(self) -> list[dict]:
        rows = []
        for i, p in enumerate(self.pvis):
            row = {"index": i, "chainage": p.chainage, "chainage_text": format_chainage(p.chainage), "elevation": p.elevation, "length": p.length, "label": p.label,
                   "grade_in": float(self.grades[i - 1]) if i > 0 else None, "grade_out": float(self.grades[i]) if i < len(self.pvis) - 1 else None}
            c = next((c for c in self.curves if c.index == i), None)
            if c:
                row.update({"A": c.A, "K": c.K, "kind": c.kind, "bvc": c.bvc, "evc": c.evc, "z_bvc": c.z_bvc, "z_evc": c.z_evc,
                            "turning_chainage": c.turning_chainage, "turning_elevation": c.turning_elevation, "valid": c.valid, "message": c.message})
            rows.append(row)
        return rows

    # ------------------------------------------------------------------ checks
    def check(self, std: Standard, ctx: dict) -> list[Check]:
        """NRS 2070 chapter 10: maximum gradient by design speed (Table 10-1) eased for altitude
        (cl. 10.1.2 a, `ctx['gradient_easing']` in %), critical length of grade (Table 10-2), minimum
        drainage gradient (cl. 10.1.1 e), K values of summit and valley curves (Tables 10-3, 10-4)."""
        checks: list[Check] = []
        speed = float(ctx.get("design_speed") or 0)
        p_gmax = std.resolve("max_gradient", **ctx)
        easing = float(ctx.get("gradient_easing") or 0.0)
        if easing > 0 and p_gmax.value is not None:
            p_gmax = Param(p_gmax.key, round(p_gmax.value - easing, 3), p_gmax.unit, f"{p_gmax.source}; eased by {easing:g} % for altitude (cl. 10.1.2 a)", p_gmax.status, p_gmax.note)
        p_thr = std.resolve("grade_compensation_threshold")
        p_gmin = std.resolve("min_gradient_drainage")
        p_kc = std.resolve("min_k_crest", design_speed=speed)
        p_ks = std.resolve("min_k_sag", design_speed=speed)
        p_lmin = std.resolve("min_vertical_curve_length", design_speed=speed)
        thr = p_thr.value if p_thr.value is not None else 4.0
        for i, g in enumerate(self.grades):
            a, b = self.pvis[i], self.pvis[i + 1]
            where = f"{format_chainage(a.chainage)} - {format_chainage(b.chainage)}"
            ag = abs(float(g))
            checks.append(compare("gradient", "Gradient", ag, p_gmax, kind="max", where=where))
            if ag > thr + 1e-9:
                p_crit = std.resolve("gradient_critical_length", gradient=ag)
                if p_crit.value is not None:
                    length = b.chainage - a.chainage
                    ok = length <= p_crit.value + 1e-9
                    checks.append(Check("critical_length", "Length of grade", ok, round(length, 1), p_crit.value, "m", p_crit.source, p_crit.status, where,
                                        f"{ag:.2f} % over {length:.0f} m {'is within' if ok else 'exceeds'} the critical length {p_crit.value:.0f} m"
                                        + ("" if ok else "; consider a climbing lane (cl. 10.2) or a flatter grade"), "info" if ok else "warning"))
            if p_gmin.value is not None and ag < p_gmin.value - 1e-9:
                checks.append(Check("min_gradient", "Drainage gradient", False, ag, p_gmin.value, "%", p_gmin.source, p_gmin.status, where,
                                    f"gradient {ag:.2f} % is flatter than {p_gmin.value:.1f} %: check side-drain flow", "warning"))
        for c in self.curves:
            where = f"PVI {c.index} (CH {format_chainage(c.chainage)})"
            if c.kind == "none":
                continue
            if c.length <= 0:
                checks.append(Check("vertical_curve", "Vertical curve", False, 0.0, p_lmin.value, "m", p_lmin.source, p_lmin.status, where,
                                    f"grade change of {abs(c.A):.2f} % without a vertical curve", "warning"))
                continue
            if p_lmin.value is not None:
                checks.append(compare("min_curve_length", "Vertical curve length", c.length, p_lmin, kind="min", where=where, severity="warning", fmt="{:.0f}"))
            p_k = p_kc if c.kind == "crest" else p_ks
            checks.append(compare(f"k_{c.kind}", f"K value ({c.kind})", c.K, p_k, kind="min", where=where, fmt="{:.1f}"))
        for iss in self.issues:
            checks.append(Check(iss.kind, iss.kind.replace("_", " "), False, None, None, "", "geometry", "verified", f"PVI {iss.index}", iss.message, "error"))
        return checks

    # ------------------------------------------------------------------ construction helpers
    @classmethod
    def from_ground(cls, ground: Sequence[tuple[float, float]], spacing: float = 200.0, curve_length: float | None = None,
                    smooth: int = 2) -> "VerticalAlignment":
        """Starting grade line that follows the ground: PVIs at `spacing`, elevations from a lightly
        smoothed ground line, symmetric curves of `curve_length` (default 40 % of the spacing)."""
        pts = np.array([(c, z) for c, z in ground if z is not None and np.isfinite(z)], dtype=float)
        if len(pts) < 2:
            raise ValueError("ground profile has fewer than two points inside the TIN")
        c0, c1 = pts[0, 0], pts[-1, 0]
        n = max(2, int(round((c1 - c0) / max(spacing, 1.0))) + 1)
        chs = np.linspace(c0, c1, n)
        z = np.interp(chs, pts[:, 0], pts[:, 1])
        for _ in range(max(0, smooth)):
            if len(z) > 2:
                z[1:-1] = 0.25 * z[:-2] + 0.5 * z[1:-1] + 0.25 * z[2:]
        L = curve_length if curve_length is not None else 0.4 * (chs[1] - chs[0])
        pvis = [PVI(float(c), float(v), float(L) if 0 < i < len(chs) - 1 else 0.0, str(i)) for i, (c, v) in enumerate(zip(chs, z))]
        return cls(pvis)

    @staticmethod
    def make(items: Sequence[dict]) -> "VerticalAlignment":
        return VerticalAlignment([PVI(float(d["chainage"]), float(d["elevation"]), float(d.get("length") or 0.0), str(d.get("label") or "")) for d in items])

    def to_dicts(self) -> list[dict]:
        return [{"chainage": p.chainage, "elevation": p.elevation, "length": p.length, "label": p.label} for p in self.pvis]


# ---------------------------------------------------------------------- following a changed horizontal alignment
def stretch_pvis(pvis: Sequence[dict], old_range: tuple[float, float], new_range: tuple[float, float], ground_new=None) -> list[dict]:
    """Move a grade line from `old_range` to `new_range`: every PVI keeps its relative position along the
    road and, when the ground level it was designed against is stored (`ground_z`) and `ground_new(ch)`
    is given, its cut / fill depth. Curve lengths shrink with a shorter road and are kept otherwise."""
    s0, s1 = float(old_range[0]), float(old_range[1])
    t0, t1 = float(new_range[0]), float(new_range[1])
    L0, L1 = max(s1 - s0, 1e-9), max(t1 - t0, 1e-9)
    k = L1 / L0
    out = []
    for p in pvis:
        f = (float(p["chainage"]) - s0) / L0
        ch = t0 + f * L1
        z = float(p["elevation"])
        gz_old = p.get("ground_z")
        if ground_new is not None and gz_old is not None and np.isfinite(float(gz_old)):
            gz_new = ground_new(ch)
            if gz_new is not None and np.isfinite(float(gz_new)):
                z = float(gz_new) + (z - float(gz_old))
        length = float(p.get("length") or 0.0) * (k if k < 1.0 else 1.0)
        out.append({"chainage": round(ch, 3), "elevation": round(z, 3), "length": round(length, 3), "label": str(p.get("label") or "")})
    return out


def trim_pvis(pvis: Sequence[dict], new_range: tuple[float, float], min_gap: float = 1.0) -> list[dict]:
    """Clip or extend a grade line to `new_range`: PVIs outside are dropped and the two ends are placed
    on the range limits at the level of the grade line extended along its first / last tangent."""
    if len(pvis) < 2:
        return [dict(p) for p in pvis]
    va = VerticalAlignment.make(pvis)
    t0, t1 = float(new_range[0]), float(new_range[1])

    def z_at(ch: float) -> float:
        if ch < va.start_chainage:
            return va.pvis[0].elevation + float(va.grades[0]) / 100.0 * (ch - va.start_chainage)
        if ch > va.end_chainage:
            return va.pvis[-1].elevation + float(va.grades[-1]) / 100.0 * (ch - va.end_chainage)
        return float(np.asarray(va.elevation_at(ch), float).ravel()[0])

    inner = [dict(p) for p in pvis if t0 + min_gap < float(p["chainage"]) < t1 - min_gap]
    out = [{"chainage": round(t0, 3), "elevation": round(z_at(t0), 3), "length": 0.0, "label": "0"}] + inner + \
          [{"chainage": round(t1, 3), "elevation": round(z_at(t1), 3), "length": 0.0, "label": ""}]
    for i, p in enumerate(out):
        p["label"] = str(i)
        p.pop("ground_z", None)
    return out
