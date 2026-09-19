"""Delimited text import/export of survey points.

Legacy rules reproduced as defaults (pt_import.read_Point): by field count
2 -> X Y, 3 -> X Y Z, 4 -> Num X Y Z, 5+ -> Num X Y Z Remark(rest joined).
A header line is detected automatically and used to map columns when present.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Mapping, TextIO


from ..points import PointSet

_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "no", "num", "number", "pt", "ptno", "ptnum", "point", "pointno", "name", "label", "p"),
    "x": ("x", "e", "east", "easting", "lon", "long", "longitude"),
    "y": ("y", "n", "north", "northing", "lat", "latitude"),
    "z": ("z", "elev", "elevation", "rl", "h", "height", "level", "alt", "altitude"),
    "remark": ("remark", "remarks", "rem", "desc", "description", "code", "note", "notes", "feature", "layer"),
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.strip().lower())


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def detect_delimiter(sample: str) -> str | None:
    """Return ',', ';', '\\t' or None (None = split on any whitespace)."""
    lines = [ln for ln in sample.splitlines() if ln.strip()][:20]
    if not lines:
        return ","
    counts = {d: [ln.count(d) for ln in lines] for d in (",", "\t", ";")}
    # 1. a delimiter present on every line with a constant count wins
    for d in (",", "\t", ";"):
        c = counts[d]
        if min(c) >= 1 and max(c) == min(c):
            return d
    # 2. otherwise the delimiter present on most lines (at least half of them)
    best, best_frac = None, 0.0
    for d in (",", "\t", ";"):
        frac = sum(1 for v in counts[d] if v >= 1) / len(lines)
        if frac > best_frac:
            best, best_frac = d, frac
    if best is not None and best_frac >= 0.5:
        return best
    return None


def _split(line: str, delimiter: str | None) -> list[str]:
    if delimiter is None:
        return line.split()
    parts = next(csv.reader([line], delimiter=delimiter))
    return [p.strip() for p in parts]


def _infer_mapping_from_header(header: list[str]) -> dict[str, int] | None:
    norm = [_norm(h) for h in header]
    mapping: dict[str, int] = {}
    for role, aliases in _HEADER_ALIASES.items():
        for i, h in enumerate(norm):
            if h in aliases and i not in mapping.values():
                mapping[role] = i
                break
    if "x" in mapping and "y" in mapping:
        return mapping
    return None


def _mapping_from_count(n: int) -> dict[str, int]:
    if n <= 2:
        return {"x": 0, "y": 1}
    if n == 3:
        return {"x": 0, "y": 1, "z": 2}
    if n == 4:
        return {"id": 0, "x": 1, "y": 2, "z": 3}
    return {"id": 0, "x": 1, "y": 2, "z": 3, "remark": 4}


def read_points_csv(
    source: str | Path | TextIO,
    *,
    delimiter: str | None = "auto",
    mapping: Mapping[str, int] | None = None,
    has_header: bool | None = None,
    skip_bad_rows: bool = True,
) -> PointSet:
    """Read points from CSV/TXT/XYZ text.

    delimiter   'auto' (default), ',', ';', '\\t' or None for whitespace
    mapping     {'id': col, 'x': col, 'y': col, 'z': col, 'remark': col} (0-based); when omitted
                it is inferred from the header or from the field count (legacy rule)
    has_header  None = detect (first row whose x/y fields are not numeric)
    """
    if isinstance(source, (str, Path)) and "\n" not in str(source):
        text = Path(source).read_text(encoding="utf-8-sig", errors="replace")
    elif isinstance(source, str):
        text = source
    else:
        text = source.read()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return PointSet.empty()
    delim = detect_delimiter("\n".join(lines[:50])) if delimiter == "auto" else delimiter

    first = _split(lines[0], delim)
    header: list[str] | None = None
    if has_header is None:
        numeric = [_is_number(v) for v in first]
        if len(lines) > 1:
            numeric2 = [_is_number(v) for v in _split(lines[1], delim)]
            # a field that is numeric in the second row but not in the first -> header
            has_header = sum(numeric) < 2 or any(
                n2 and not n1 for n1, n2 in zip(numeric, numeric2)
            )
        else:
            has_header = sum(numeric) < 2
    if has_header:
        header = first
        lines = lines[1:]
    if not lines:
        return PointSet.empty()

    if mapping is None:
        mapping = (_infer_mapping_from_header(header) if header else None) or _mapping_from_count(
            len(_split(lines[0], delim))
        )
    m = dict(mapping)
    ids, xs, ys, zs, rem, bad = [], [], [], [], [], 0
    for ln in lines:
        f = _split(ln, delim)
        try:
            x = float(f[m["x"]])
            y = float(f[m["y"]])
            z = float(f[m["z"]]) if "z" in m and m["z"] < len(f) and f[m["z"]] != "" else 0.0
        except (ValueError, IndexError):
            bad += 1
            if skip_bad_rows:
                continue
            raise ValueError(f"cannot parse row: {ln!r}")
        ids.append(f[m["id"]] if "id" in m and m["id"] < len(f) else "")
        if "remark" in m and m["remark"] < len(f):
            r_idx = m["remark"]
            used = set(m.values()) - {r_idx}
            # legacy: everything after the remark column joins the remark
            tail = [f[i] for i in range(r_idx, len(f)) if i not in used]
            rem.append(" ".join(t for t in tail if t))
        else:
            rem.append("")
        xs.append(x)
        ys.append(y)
        zs.append(z)
    return PointSet.from_arrays(xs, ys, zs, ids=ids, remarks=rem)


def write_points_csv(
    points: PointSet,
    target: str | Path | TextIO,
    *,
    delimiter: str = ",",
    header: bool = True,
    precision: int = 3,
    fmt: str = "id,x,y,z,remark",
) -> None:
    """Write points; `fmt` chooses/orders columns, e.g. 'x,y,z' for an .xyz file."""
    cols = [c.strip() for c in fmt.split(",")]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    if header:
        w.writerow(cols)
    for i in range(len(points)):
        row = []
        for c in cols:
            if c == "id":
                row.append(points.ids[i] or str(i + 1))
            elif c == "x":
                row.append(f"{points.x[i]:.{precision}f}")
            elif c == "y":
                row.append(f"{points.y[i]:.{precision}f}")
            elif c == "z":
                row.append(f"{points.z[i]:.{precision}f}")
            elif c == "remark":
                row.append(points.remarks[i])
            elif c == "layer":
                row.append(points.layers[i])
        w.writerow(row)
    if isinstance(target, (str, Path)):
        Path(target).write_text(buf.getvalue(), encoding="utf-8")
    else:
        target.write(buf.getvalue())
