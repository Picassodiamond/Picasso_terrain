"""Create demo datasets and (optionally) load them into a running PLM server.

    python examples/make_demo.py                 # writes examples/demo/*.csv|geojson
    python examples/make_demo.py --load http://127.0.0.1:8000 [--user admin --password ...]

Two surveys are generated:
  * demo_local   - local grid metres (legacy style), hillside with a stream and a boundary
  * demo_mutm84  - the same terrain placed in Nepal MUTM 84 near Kathmandu so the OSM overlay works
plus a sample alignment file in the legacy *_aln.csv layout.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
OUT = HERE / "demo"


def terrain(u, v):
    """u, v in metres from the survey origin (0..600, 0..400)."""
    return (
        1350.0
        + 0.045 * u
        + 0.03 * v
        + 9.0 * np.exp(-(((u - 220) ** 2 + (v - 160) ** 2) / (2 * 55.0**2)))
        + 5.0 * np.exp(-(((u - 450) ** 2 + (v - 300) ** 2) / (2 * 70.0**2)))
        - 4.0 * np.exp(-(((v - 90 - 0.15 * u) ** 2) / (2 * 18.0**2)))  # valley/stream channel
        + 1.2 * np.sin(u / 40.0) * np.cos(v / 55.0)
    )


def make(origin_x: float, origin_y: float, tag: str, n: int = 2200, seed: int = 11) -> dict:
    rng = np.random.default_rng(seed)
    u = rng.uniform(0, 600, n)
    v = rng.uniform(0, 400, n)
    z = terrain(u, v) + rng.normal(0, 0.04, n)
    codes = rng.choice(["", "", "", "", "GL", "GL", "TREE", "HOUSE", "ROAD", "BL", "TOP"], n)
    x, y = origin_x + u, origin_y + v
    OUT.mkdir(exist_ok=True)
    pts = OUT / f"{tag}_points.csv"
    with pts.open("w", encoding="utf-8") as f:
        f.write("PtNo,Easting,Northing,Elevation,Remark\n")
        for i in range(n):
            f.write(f"{i + 1},{x[i]:.3f},{y[i]:.3f},{z[i]:.3f},{codes[i]}\n")
    # stream (feature line) following the channel, boundary polygon, a void (pond)
    su = np.linspace(10, 590, 40)
    sv = 90 + 0.15 * su + 6 * np.sin(su / 70.0)
    stream = [[float(origin_x + a), float(origin_y + b), float(terrain(a, b) - 0.6)] for a, b in zip(su, sv)]
    ridge_u = np.linspace(120, 330, 12)
    ridge_v = 160 + 0.4 * (ridge_u - 220)
    ridge = [[float(origin_x + a), float(origin_y + b), float(terrain(a, b) + 0.3)] for a, b in zip(ridge_u, ridge_v)]
    boundary = [[origin_x + 15, origin_y + 15], [origin_x + 585, origin_y + 12], [origin_x + 590, origin_y + 385], [origin_x + 300, origin_y + 392], [origin_x + 12, origin_y + 380], [origin_x + 15, origin_y + 15]]
    pond = [[origin_x + 460 + 25 * np.cos(a), origin_y + 120 + 18 * np.sin(a)] for a in np.linspace(0, 2 * np.pi, 20)]
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": stream}, "properties": {"kind": "feature", "layer": "Features", "name": "stream"}},
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": ridge}, "properties": {"kind": "feature", "layer": "Features", "name": "ridge"}},
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [boundary]}, "properties": {"kind": "boundary", "layer": "Boundary"}},
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [pond]}, "properties": {"kind": "void", "layer": "Void", "name": "pond"}},
    ]}
    lines = OUT / f"{tag}_lines.geojson"
    lines.write_text(json.dumps(gj, indent=1), encoding="utf-8")
    # alignment: legacy *_aln.csv layout (count,startCh then N,X,Y,R)
    ips = [(0, 30, 60, 0), (1, 200, 120, 80), (2, 380, 110, 120), (3, 520, 250, 90), (4, 570, 370, 0)]
    aln = OUT / f"{tag}_aln.csv"
    with aln.open("w", encoding="utf-8") as f:
        f.write(f"{len(ips) - 1},0\n")
        for n_, ux, vy, r in ips:
            f.write(f"{n_},{origin_x + ux:.3f},{origin_y + vy:.3f},{r}\n")
    return {"points": pts, "lines": lines, "alignment": aln}


def load(base: str, files: dict, name: str, crs: str, user: str | None, password: str | None) -> None:
    import httpx

    c = httpx.Client(base_url=base, timeout=300)
    if user:
        r = c.post("/api/auth/login", json={"username": user, "password": password or ""})
        if r.status_code == 401 and c.get("/api/auth/status").json()["users"] == 0:
            r = c.post("/api/auth/register", json={"username": user, "password": password or "", "organisation": "demo"})
        r.raise_for_status()
    p = c.post("/api/projects", json={"name": name, "crs": crs, "description": "PLM demo data"}).json()
    pid = p["id"]
    with files["points"].open("rb") as f:
        print(c.post(f"/api/projects/{pid}/import", files={"file": (files["points"].name, f)}).json()["points_added"], "points")
    with files["lines"].open("rb") as f:
        print(c.post(f"/api/projects/{pid}/import", files={"file": (files["lines"].name, f)}).json()["lines_added"], "lines")
    j = c.post(f"/api/projects/{pid}/tin", json={}).json()
    print("tin", j["status"], j.get("result", {}).get("n_triangles"))
    j = c.post(f"/api/projects/{pid}/contours", json={"interval": 1.0, "major_every": 5, "style": {"label_every": 80}}).json()
    print("contours", j["status"], j.get("result", {}).get("n_lines"))
    with files["alignment"].open("rb") as f:
        a = c.post(f"/api/projects/{pid}/alignments/import", files={"file": (files["alignment"].name, f)}, data={"name": "Demo road"}).json()
    print("alignment", a["name"], round(a["length"], 1), "m", "valid" if a["valid"] else a["issues"])
    j = c.post(f"/api/projects/{pid}/sections", json={"alignment_id": a["id"], "interval": 20, "left": 15, "right": 15}).json()
    print("sections", j["status"], j.get("result", {}).get("summary", {}).get("sections"))
    print(f"open {base}/#/p/{pid}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", metavar="URL", help="load into a running server, e.g. http://127.0.0.1:8000")
    ap.add_argument("--user")
    ap.add_argument("--password")
    args = ap.parse_args()
    local = make(1000.0, 5000.0, "demo_local")
    mutm = make(627_000.0, 3_065_000.0, "demo_mutm84", seed=12)  # near Kathmandu in MUTM 84
    print("wrote", *[str(v) for v in local.values()], *[str(v) for v in mutm.values()], sep="\n  ")
    if args.load:
        load(args.load, local, "Demo - local grid", "local", args.user, args.password)
        load(args.load, mutm, "Demo - MUTM 84 (map overlay)", "MUTM84", args.user, args.password)
    return 0


if __name__ == "__main__":
    sys.exit(main())
