"""Design standards as data.

A standard is a JSON document under `plm/design/standards/` with tables keyed by road class, terrain,
design speed and so on. Every value carries a source (clause) and a status: `verified` when it has
been checked against the printed document, `placeholder` when it still needs that check. Checks never
embed numbers; they call `Standard.resolve()` and report the value with its source, so a reviewer can
verify every parameter, and a design may record *deviations* (value + justification) that override a
parameter for that design only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STANDARDS_DIR = Path(__file__).parent / "standards"


@dataclass
class Param:
    key: str
    value: float | None
    unit: str = ""
    source: str = ""
    status: str = "placeholder"   # verified | placeholder | deviation | missing
    note: str = ""

    def as_dict(self) -> dict:
        return {"key": self.key, "value": self.value, "unit": self.unit, "source": self.source, "status": self.status, "note": self.note}


@dataclass
class Standard:
    id: str
    name: str
    version: str
    tables: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)
    deviations: dict[str, dict] = field(default_factory=dict)

    # ------------------------------------------------------------------ lookup
    def _lookup(self, table: dict, keys: list[str], lookup: str = "floor", interpolate: bool = False) -> Any:
        """Walk the nested value table. Numeric keys (design speed, radius, gradient, fill height)
        are matched by `lookup`: 'floor' = nearest not-larger key (else the smallest), 'ceil' =
        nearest not-smaller key (else the largest); with `interpolate` the value is interpolated
        linearly between the two neighbouring keys and clamped at the ends."""
        node: Any = table
        for k in keys:
            if not isinstance(node, dict):
                return None
            if k in node:
                node = node[k]
            elif "*" in node:
                node = node["*"]
            else:
                try:
                    q = float(k)
                except ValueError:
                    return None
                nums = sorted((float(x), x) for x in node.keys() if x not in ("unit", "source", "status", "note") and _isnum(x))
                if not nums:
                    return None
                if interpolate and all(isinstance(node[n], (int, float)) for _, n in nums):
                    xs = [v for v, _ in nums]
                    ys = [float(node[n]) for _, n in nums]
                    if q <= xs[0]:
                        return ys[0]
                    if q >= xs[-1]:
                        return ys[-1]
                    for i in range(1, len(xs)):
                        if q <= xs[i]:
                            f = (q - xs[i - 1]) / (xs[i] - xs[i - 1])
                            return ys[i - 1] + f * (ys[i] - ys[i - 1])
                if lookup == "ceil":
                    pick = nums[-1][1]
                    for v, name in reversed(nums):
                        if v >= q:
                            pick = name
                else:
                    pick = nums[0][1]
                    for v, name in nums:
                        if v <= q:
                            pick = name
                node = node[pick]
        return node

    def resolve(self, key: str, **ctx: Any) -> Param:
        """Resolve a parameter for a context such as road_class, terrain, design_speed, material.

        Deviations recorded on the design win over the table value."""
        if key in self.deviations:
            d = self.deviations[key]
            return Param(key, d.get("value"), d.get("unit", ""), f"deviation: {d.get('justification', '')}", "deviation")
        t = self.tables.get(key)
        if t is None:
            return Param(key, None, status="missing", note=f"{self.id} has no table {key!r}")
        by = [b for b in t.get("by", []) if b != "*"]  # "*" = one value for every context
        legacy_kind = {"road_class": "classes", "material": "materials"}
        keys = [self.canonical(legacy_kind[k], str(ctx[k])) if k in legacy_kind else str(ctx[k]) for k in by if k in ctx]
        if len(keys) < len(by):
            missing = [k for k in by if k not in ctx]
            return Param(key, None, t.get("unit", ""), t.get("source", ""), "missing", f"needs {', '.join(missing)}")
        v = self._lookup(t.get("values", {}), keys, str(t.get("lookup", "floor")), bool(t.get("interpolate", False)))
        if isinstance(v, dict) and "*" in v:
            v = v["*"]
        if isinstance(v, dict):
            v = v.get("value")
        if v is None:
            return Param(key, None, t.get("unit", ""), t.get("source", ""), "missing", f"no value for {dict(zip(t.get('by', []), keys))}")
        return Param(key, round(float(v), 6), t.get("unit", ""), t.get("source", ""), t.get("status", self.meta.get("status", "placeholder")), t.get("note", ""))

    def label(self, kind: str, value: str) -> str:
        """Human label for a class / terrain / material / road type value, from the standard's meta."""
        labels = self.meta.get(f"{kind}_labels") or {}
        return str(labels.get(value, value))

    def canonical(self, kind: str, value: str) -> str:
        """Map legacy names (national, feeder, colluvium ...) to the standard's own keys."""
        legacy = self.meta.get(f"legacy_{kind}") or {}
        return str(legacy.get(value, value))

    def table(self, key: str) -> dict | None:
        return self.tables.get(key)

    def summary(self) -> dict:
        return {"id": self.id, "name": self.name, "version": self.version, "status": self.meta.get("status", "placeholder"),
                "tables": sorted(self.tables.keys()), "meta": self.meta}


def _isnum(x: str) -> bool:
    try:
        float(x)
        return True
    except ValueError:
        return False


def list_standards() -> list[dict]:
    out = []
    for f in sorted(STANDARDS_DIR.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        out.append({"id": d["id"], "name": d["name"], "version": d.get("version", ""), "status": d.get("meta", {}).get("status", "placeholder")})
    return out


def load_standard(std_id: str, deviations: dict | None = None) -> Standard:
    f = STANDARDS_DIR / f"{std_id}.json"
    if not f.exists():
        raise FileNotFoundError(f"unknown standard {std_id!r}; available: {[s['id'] for s in list_standards()]}")
    d = json.loads(f.read_text(encoding="utf-8"))
    return Standard(d["id"], d["name"], d.get("version", ""), d["tables"], d.get("meta", {}), dict(deviations or {}))


@dataclass
class Check:
    """One design check result, always with the governing parameter and its source."""
    id: str
    label: str
    ok: bool | None            # None = could not be evaluated
    actual: float | None
    limit: float | None
    unit: str = ""
    source: str = ""
    status: str = ""           # parameter status (verified / placeholder / deviation / missing)
    where: str = ""            # IP 3, PVI 2, CH 1+240 ...
    message: str = ""
    severity: str = "error"    # error | warning | info

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def compare(check_id: str, label: str, actual: float | None, p: Param, *, kind: str, where: str = "", severity: str = "error",
            fmt: str = "{:.2f}") -> Check:
    """kind: 'min' -> actual must be >= limit; 'max' -> actual must be <= limit."""
    if p.value is None or actual is None:
        return Check(check_id, label, None, actual, p.value, p.unit, p.source, p.status, where,
                     f"not evaluated: {p.note or 'no value'}", "info")
    ok = actual >= p.value - 1e-9 if kind == "min" else actual <= p.value + 1e-9
    rel = ">=" if kind == "min" else "<="
    msg = f"{label} {fmt.format(actual)} {p.unit} {'ok' if ok else 'fails'} ({rel} {fmt.format(p.value)} {p.unit}, {p.source or p.status})"
    return Check(check_id, label, ok, actual, p.value, p.unit, p.source, p.status, where, msg, severity if not ok else "info")
