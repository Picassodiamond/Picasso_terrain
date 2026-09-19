"""Horizontal road alignment: intersection points (IPs) with circular curves.

Unifies the three legacy copies of the IP geometry (`ACAD_FinalAlignment.cls`,
`FrmGenerateProCro.frm`, `ProCroToDtm.frm`): whole-circle bearing, deflection, tangent length
T = R tan(D/2), curve length L = R D, BC/EC chainages, curve centre and the stationing function
`point_at(chainage)`.

Conventions: coordinates are (easting, northing); directions are mathematical angles in radians
(counter-clockwise from +X); `deflection` is signed, positive = left turn (counter-clockwise).
Offsets are positive to the RIGHT of the direction of travel (legacy `Rdist`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np


def format_chainage(ch: float, decimals: int = 2) -> str:
    """Legacy `0+000.00` chainage format (km + metres)."""
    sign = "-" if ch < 0 else ""
    ch = abs(ch)
    km = int(ch // 1000)
    m = ch - km * 1000
    if round(m, decimals) >= 1000:
        km += 1
        m = 0.0
    width = 3 + (decimals + 1 if decimals > 0 else 0)
    return f"{sign}{km}+{m:0{width}.{decimals}f}"


def parse_chainage(text: str) -> float:
    """Accept '1+234.56', '1234.56' or '1234'."""
    t = str(text).strip().replace(" ", "")
    if "+" in t:
        km, m = t.split("+", 1)
        return float(km or 0) * 1000.0 + float(m or 0)
    return float(t)


@dataclass
class IP:
    x: float
    y: float
    radius: float = 0.0
    label: str = ""


@dataclass
class IPGeometry:
    """Derived quantities for one IP (all lengths in metres, angles in radians)."""

    index: int
    label: str
    x: float
    y: float
    radius: float
    chainage: float          # chainage of the IP measured along the tangents (legacy IP.Ch)
    direction_in: float      # direction of the incoming tangent
    direction_out: float     # direction of the outgoing tangent
    deflection: float        # signed, + left
    tangent_length: float    # T
    curve_length: float      # L
    external: float          # E
    bc_chainage: float
    mc_chainage: float
    ec_chainage: float
    bc: tuple[float, float]
    ec: tuple[float, float]
    centre: tuple[float, float] | None
    valid: bool = True
    message: str = ""


@dataclass
class Element:
    kind: str                 # 'tangent' | 'curve'
    start_chainage: float
    end_chainage: float
    start: tuple[float, float]
    end: tuple[float, float]
    radius: float = 0.0       # curves only
    centre: tuple[float, float] | None = None
    deflection: float = 0.0   # signed
    ip_index: int | None = None

    @property
    def length(self) -> float:
        return self.end_chainage - self.start_chainage


@dataclass
class AlignmentIssue:
    ip_index: int
    kind: str      # 'overlap' | 'min_radius' | 'zero_length'
    message: str


class HorizontalAlignment:
    """IP-based horizontal alignment with simple circular curves (no transitions)."""

    def __init__(self, ips: Sequence[IP], start_chainage: float = 0.0, min_radius: float = 4.0):
        if len(ips) < 2:
            raise ValueError("an alignment needs at least two IPs")
        self.ips: list[IP] = [IP(float(p.x), float(p.y), float(p.radius or 0.0), str(p.label or "")) for p in ips]
        self.start_chainage = float(start_chainage)
        self.min_radius = float(min_radius)
        self.issues: list[AlignmentIssue] = []
        self.geometry: list[IPGeometry] = []
        self.elements: list[Element] = []
        self._compute()

    # ------------------------------------------------------------------ construction
    @classmethod
    def from_rows(cls, rows: Iterable[Sequence], start_chainage: float = 0.0) -> "HorizontalAlignment":
        """rows of (label, x, y, r) - legacy `*_aln.csv` body."""
        ips = [IP(float(r[1]), float(r[2]), float(r[3]) if len(r) > 3 and r[3] not in ("", None) else 0.0, str(r[0])) for r in rows]
        return cls(ips, start_chainage)

    @classmethod
    def from_polyline(cls, coords: np.ndarray, bulges: Sequence[float] | None = None,
                      start_chainage: float = 0.0) -> "HorizontalAlignment":
        """Build IPs from a polyline; arc segments (bulges) become an IP with radius at the
        intersection of the arc tangents (AutoCAD `H_ALIGN` LWPOLYLINE import)."""
        c = np.asarray(coords, dtype=float)[:, :2]
        n = len(c)
        b = list(bulges) if bulges is not None else [0.0] * (n - 1)
        ips: list[IP] = []
        i = 0
        while i < n:
            if i < n - 1 and b[i] != 0.0:
                pa, pb = c[i], c[i + 1]
                chord = pb - pa
                L = float(np.hypot(*chord))
                delta = 4.0 * np.arctan(b[i])            # signed included angle (+ CCW)
                r = L / (2.0 * np.sin(abs(delta) / 2.0))
                ang = np.arctan2(chord[1], chord[0]) - delta / 2.0   # tangent direction at pa
                t = r * np.tan(abs(delta) / 2.0)
                ip = pa + t * np.array([np.cos(ang), np.sin(ang)])
                if ips and np.allclose((ips[-1].x, ips[-1].y), pa):
                    ips.pop()  # pa is the BC, not an IP
                ips.append(IP(float(ip[0]), float(ip[1]), float(r)))
                i += 1
                # pb is the EC: skip it unless it is the last vertex or the next segment starts a new arc
                if i == n - 1 or (i < n - 1 and b[i] != 0.0):
                    if i == n - 1:
                        ips.append(IP(float(pb[0]), float(pb[1]), 0.0))
                    continue
                i += 1
                continue
            ips.append(IP(float(c[i, 0]), float(c[i, 1]), 0.0))
            i += 1
        for k, p in enumerate(ips):
            p.label = p.label or str(k)
        return cls(ips, start_chainage)

    # ------------------------------------------------------------------ core maths
    def _compute(self) -> None:
        ips = self.ips
        n = len(ips)
        xy = np.array([[p.x, p.y] for p in ips])
        seg = np.diff(xy, axis=0)
        seg_len = np.hypot(seg[:, 0], seg[:, 1])
        for i, L in enumerate(seg_len):
            if L == 0:
                self.issues.append(AlignmentIssue(i, "zero_length", f"IP {ips[i].label} and IP {ips[i+1].label} coincide"))
        dirs = np.arctan2(seg[:, 1], seg[:, 0])  # direction of tangent i (IP i -> IP i+1)

        # per-IP curve quantities
        T = np.zeros(n)
        Lc = np.zeros(n)
        E = np.zeros(n)
        defl = np.zeros(n)
        bc = xy.copy()
        ec = xy.copy()
        centre: list[tuple[float, float] | None] = [None] * n
        for i in range(1, n - 1):
            r = ips[i].radius
            if r <= 0:
                continue
            u1 = seg[i - 1] / (seg_len[i - 1] or 1.0)
            u2 = seg[i] / (seg_len[i] or 1.0)
            cross = u1[0] * u2[1] - u1[1] * u2[0]
            dot = float(np.clip(np.dot(u1, u2), -1.0, 1.0))
            d = float(np.arctan2(abs(cross), dot))
            if d < 1e-12:
                continue  # collinear: no curve
            sgn = 1.0 if cross > 0 else -1.0
            defl[i] = sgn * d
            T[i] = r * np.tan(d / 2.0)
            Lc[i] = r * d
            E[i] = r / np.cos(d / 2.0) - r
            bc[i] = xy[i] - u1 * T[i]
            ec[i] = xy[i] + u2 * T[i]
            nrm = np.array([-u1[1], u1[0]]) * sgn  # towards the centre
            c = bc[i] + nrm * r
            centre[i] = (float(c[0]), float(c[1]))
            if r < self.min_radius:
                self.issues.append(AlignmentIssue(i, "min_radius", f"IP {ips[i].label}: radius {r:g} < minimum {self.min_radius:g}"))
        # validity: tangents must fit between IPs
        valid = np.ones(n, dtype=bool)
        for i in range(n - 1):
            if T[i] + T[i + 1] > seg_len[i] + 1e-9:
                for k in (i, i + 1):
                    if T[k] > 0:
                        valid[k] = False
                self.issues.append(AlignmentIssue(i + 1, "overlap",
                    f"curves at IP {ips[i].label} and IP {ips[i+1].label} overlap (T1+T2={T[i]+T[i+1]:.2f} > {seg_len[i]:.2f})"))

        # chainages along the alignment
        ip_ch = np.zeros(n)
        ip_ch[0] = self.start_chainage
        for i in range(1, n):
            ip_ch[i] = ip_ch[i - 1] + seg_len[i - 1]
        bc_ch = np.zeros(n)
        ec_ch = np.zeros(n)
        bc_ch[0] = ec_ch[0] = self.start_chainage
        for i in range(1, n):
            tangent = seg_len[i - 1] - T[i - 1] - T[i]
            bc_ch[i] = ec_ch[i - 1] + max(tangent, 0.0)
            ec_ch[i] = bc_ch[i] + Lc[i]
        self._bc_ch, self._ec_ch, self._T, self._L, self._defl = bc_ch, ec_ch, T, Lc, defl
        self._bc, self._ec, self._centre = bc, ec, centre
        self._dirs = dirs

        self.geometry = []
        for i in range(n):
            din = float(dirs[i - 1]) if i > 0 else float(dirs[0])
            dout = float(dirs[i]) if i < n - 1 else float(dirs[-1])
            self.geometry.append(IPGeometry(
                index=i, label=ips[i].label, x=ips[i].x, y=ips[i].y, radius=ips[i].radius,
                chainage=float(ip_ch[i]), direction_in=din, direction_out=dout,
                deflection=float(defl[i]), tangent_length=float(T[i]), curve_length=float(Lc[i]),
                external=float(E[i]), bc_chainage=float(bc_ch[i]), mc_chainage=float(bc_ch[i] + Lc[i] / 2),
                ec_chainage=float(ec_ch[i]), bc=(float(bc[i, 0]), float(bc[i, 1])),
                ec=(float(ec[i, 0]), float(ec[i, 1])), centre=centre[i], valid=bool(valid[i]),
                message="" if valid[i] else "curve does not fit between adjacent IPs",
            ))

        # elements
        els: list[Element] = []
        for i in range(n - 1):
            s = ec[i]
            e = bc[i + 1]
            s_ch, e_ch = ec_ch[i], bc_ch[i + 1]
            if e_ch - s_ch > 1e-9:
                els.append(Element("tangent", float(s_ch), float(e_ch), (float(s[0]), float(s[1])), (float(e[0]), float(e[1]))))
            if i + 1 < n - 1 and Lc[i + 1] > 0:
                k = i + 1
                els.append(Element("curve", float(bc_ch[k]), float(ec_ch[k]),
                                   (float(bc[k, 0]), float(bc[k, 1])), (float(ec[k, 0]), float(ec[k, 1])),
                                   radius=ips[k].radius, centre=centre[k], deflection=float(defl[k]), ip_index=k))
        self.elements = els

    # ------------------------------------------------------------------ properties
    @property
    def end_chainage(self) -> float:
        return float(self._ec_ch[-1]) if len(self.ips) else self.start_chainage

    @property
    def length(self) -> float:
        return self.end_chainage - self.start_chainage

    @property
    def is_valid(self) -> bool:
        return all(g.valid for g in self.geometry) and not any(i.kind == "zero_length" for i in self.issues)

    # ------------------------------------------------------------------ stationing
    def _element_at(self, ch: float) -> Element:
        els = self.elements
        if not els:
            raise ValueError("alignment has no elements")
        if ch <= els[0].start_chainage:
            return els[0]
        for el in els:
            if el.start_chainage - 1e-9 <= ch <= el.end_chainage + 1e-9:
                return el
        return els[-1]

    def point_and_direction(self, ch: float) -> tuple[float, float, float]:
        """(x, y, direction) at a chainage; extrapolates along the end tangents."""
        el = self._element_at(ch)
        if el.kind == "tangent":
            dx, dy = el.end[0] - el.start[0], el.end[1] - el.start[1]
            L = el.length
            d = np.arctan2(dy, dx) if L > 0 else self._dirs[0]
            t = (ch - el.start_chainage) / L if L > 0 else 0.0
            return el.start[0] + t * dx, el.start[1] + t * dy, float(d)
        cx, cy = el.centre  # type: ignore[misc]
        r = el.radius
        a0 = np.arctan2(el.start[1] - cy, el.start[0] - cx)
        s = np.clip(ch - el.start_chainage, 0.0, el.length)
        sgn = 1.0 if el.deflection > 0 else -1.0
        a = a0 + sgn * s / r
        x = cx + r * np.cos(a)
        y = cy + r * np.sin(a)
        d = a + sgn * np.pi / 2.0
        return float(x), float(y), float(np.arctan2(np.sin(d), np.cos(d)))

    def point_at(self, ch: float) -> tuple[float, float]:
        x, y, _ = self.point_and_direction(ch)
        return x, y

    def offset_point(self, ch: float, offset: float) -> tuple[float, float]:
        """Point at `offset` metres from the centre line (positive = right of travel)."""
        x, y, d = self.point_and_direction(ch)
        return x + offset * np.sin(d), y - offset * np.cos(d)

    def stations(self, interval: float, include_curve_points: bool = True,
                 extra: Iterable[float] = (), tol: float = 1e-3) -> np.ndarray:
        """Legacy chainage list: start, multiples of `interval`, BC/MC/EC, user chainages, end."""
        s0, s1 = self.start_chainage, self.end_chainage
        ch = [s0, s1]
        if interval > 0:
            first = np.ceil(s0 / interval) * interval
            if first - s0 < tol:
                first += interval
            ch.extend(np.arange(first, s1 - tol, interval).tolist())
        if include_curve_points:
            for g in self.geometry:
                if g.curve_length > 0 and g.valid:
                    ch.extend([g.bc_chainage, g.mc_chainage, g.ec_chainage])
        ch.extend(float(c) for c in extra if s0 - tol <= float(c) <= s1 + tol)
        ch = np.array(sorted(ch))
        keep = np.r_[True, np.diff(ch) > tol]
        return ch[keep]

    def densify(self, max_segment: float = 1.0, max_angle_deg: float = 2.0) -> tuple[np.ndarray, np.ndarray]:
        """Polyline approximation: returns (coords (k,2), chainages (k,))."""
        pts: list[tuple[float, float]] = []
        chs: list[float] = []
        for el in self.elements:
            if el.kind == "tangent":
                n = max(1, int(np.ceil(el.length / max_segment)))
            else:
                step_len = max_segment
                step_ang = np.radians(max_angle_deg) * el.radius
                n = max(2, int(np.ceil(el.length / min(step_len, step_ang))))
            for k in range(n):
                c = el.start_chainage + el.length * k / n
                x, y, _ = self.point_and_direction(c)
                if chs and abs(c - chs[-1]) < 1e-9:
                    continue
                pts.append((x, y))
                chs.append(c)
        x, y, _ = self.point_and_direction(self.end_chainage)
        pts.append((x, y))
        chs.append(self.end_chainage)
        return np.array(pts), np.array(chs)

    def polyline_with_bulges(self) -> list[tuple[float, float, float]]:
        """Vertices (x, y, bulge) for a DXF LWPOLYLINE: tangents as straight segments, curves as
        arcs with bulge = tan(deflection/4) (legacy `CalBlg`)."""
        out: list[tuple[float, float, float]] = []
        for el in self.elements:
            if el.kind == "tangent":
                out.append((el.start[0], el.start[1], 0.0))
            else:
                out.append((el.start[0], el.start[1], float(np.tan(el.deflection / 4.0))))
        if self.elements:
            e = self.elements[-1].end
            out.append((e[0], e[1], 0.0))
        return out

    def chainage_marks(self, interval: float, tick_length: float = 5.0, decimals: int = 2) -> list[dict]:
        """Chainage ticks and labels (legacy `writechain`): tick from the centre line to the right."""
        marks = []
        for ch in self.stations(interval, include_curve_points=False):
            x, y, d = self.point_and_direction(ch)
            x2, y2 = self.offset_point(ch, tick_length)
            marks.append({
                "chainage": float(ch), "label": format_chainage(ch, decimals),
                "x": x, "y": y, "x2": x2, "y2": y2,
                "angle_deg": float(np.degrees(np.arctan2(y2 - y, x2 - x))),
            })
        return marks

    def key_points(self) -> list[dict]:
        out = []
        for g in self.geometry:
            out.append({"kind": "IP", "index": g.index, "label": g.label, "x": g.x, "y": g.y,
                        "chainage": g.chainage, "radius": g.radius, "valid": g.valid})
            if g.curve_length > 0:
                mx, my = self.point_at(g.mc_chainage) if g.valid else (g.x, g.y)
                out.append({"kind": "BC", "index": g.index, "x": g.bc[0], "y": g.bc[1], "chainage": g.bc_chainage})
                out.append({"kind": "MC", "index": g.index, "x": mx, "y": my, "chainage": g.mc_chainage})
                out.append({"kind": "EC", "index": g.index, "x": g.ec[0], "y": g.ec[1], "chainage": g.ec_chainage})
        return out

    def to_rows(self) -> list[tuple[str, float, float, float]]:
        return [(p.label, p.x, p.y, p.radius) for p in self.ips]

    def to_aln_csv(self) -> str:
        """Legacy `Alignment.swr` / `*_aln.csv`: `count,startCh` then `N,X,Y,R` rows."""
        lines = [f"{len(self.ips) - 1},{self.start_chainage:g}"]
        lines += [f"{p.label},{p.x:.3f},{p.y:.3f},{p.radius:g}" for p in self.ips]
        return "\n".join(lines) + "\n"

    @classmethod
    def from_aln_csv(cls, text: str) -> "HorizontalAlignment":
        rows = [ln.strip() for ln in text.splitlines() if ln.strip()]
        head = rows[0].split(",")
        start = float(head[1]) if len(head) > 1 else 0.0
        body = [r.split(",") for r in rows[1:]]
        return cls.from_rows(body, start)
