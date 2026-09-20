"""Create a real-world terrain sample (real coordinates, real SRTM elevations).

    python examples/make_realworld.py                 # writes examples/realworld/nagarkot_points.csv
    python examples/make_realworld.py --load http://127.0.0.1:8000 [--user admin --password ...]

Site: a hillside on the Kathmandu valley rim below Nagarkot, Nepal (about 27.715 N, 85.520 E),
roughly 600 m x 400 m. Elevations come from the SRTM 30 m dataset via the public OpenTopoData
API (https://www.opentopodata.org/, 100 locations per request, 1 request/s) and are sampled with
cubic interpolation (the public server rounds to whole metres). Coordinates are written in UTM zone 45N (WGS84, EPSG:32645) so the OSM base
map lines up without a datum shift. Terrain points only - no feature lines or alignments.

The generated CSV is committed, so `--load` works offline; pass --fetch to re-download.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
OUT = HERE / "realworld"
POINTS = OUT / "nagarkot_points.csv"

# South-west corner of the site (lat, lon) and its extent in metres.
LAT0, LON0 = 27.7130, 85.5170
WIDTH, HEIGHT = 600.0, 400.0
N_POINTS = 1200
CRS = "UTM45N"
API = "https://api.opentopodata.org/v1/srtm30m"


def site_lonlat(n: int, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """Random survey positions plus a sparse regular grid, in lon/lat."""
    rng = np.random.default_rng(seed)
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * np.cos(np.radians(LAT0))
    u = rng.uniform(0, WIDTH, n)
    v = rng.uniform(0, HEIGHT, n)
    gu, gv = np.meshgrid(np.linspace(0, WIDTH, 13), np.linspace(0, HEIGHT, 9))
    u = np.concatenate([u, gu.ravel()])
    v = np.concatenate([v, gv.ravel()])
    return LON0 + u / m_per_deg_lon, LAT0 + v / m_per_deg_lat


def fetch_elevations(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    import httpx

    z = np.empty(len(lon))
    c = httpx.Client(timeout=60)
    for i in range(0, len(lon), 100):
        locs = "|".join(f"{a:.6f},{b:.6f}" for a, b in zip(lat[i : i + 100], lon[i : i + 100]))
        for attempt in range(5):
            r = c.get(API, params={"locations": locs, "interpolation": "cubic"})
            if r.status_code == 429:
                time.sleep(2.0 * (attempt + 1))
                continue
            r.raise_for_status()
            break
        res = r.json()["results"]
        z[i : i + 100] = [p["elevation"] for p in res]
        print(f"  fetched {min(i + 100, len(lon))}/{len(lon)}", file=sys.stderr)
        time.sleep(1.05)  # public API rate limit
    return z


def write_points(lon: np.ndarray, lat: np.ndarray, z: np.ndarray) -> Path:
    from plm.engine.crs import transform_xy

    x, y = transform_xy("EPSG:4326", CRS, lon, lat)
    rng = np.random.default_rng(3)
    codes = rng.choice(["", "", "", "", "GL", "GL", "TREE", "TERRACE", "TRACK", "TOP"], len(x))
    OUT.mkdir(exist_ok=True)
    with POINTS.open("w", encoding="utf-8") as f:
        f.write("PtNo,Easting,Northing,Elevation,Remark\n")
        for i in range(len(x)):
            f.write(f"{i + 1},{x[i]:.3f},{y[i]:.3f},{z[i]:.2f},{codes[i]}\n")
    return POINTS


def load(base: str, user: str | None, password: str | None) -> None:
    import httpx

    c = httpx.Client(base_url=base, timeout=300)
    if user:
        r = c.post("/api/auth/login", json={"username": user, "password": password or ""})
        if r.status_code == 401 and c.get("/api/auth/status").json()["users"] == 0:
            r = c.post("/api/auth/register", json={"username": user, "password": password or "", "organisation": "demo"})
        r.raise_for_status()
    p = c.post("/api/projects", json={"name": "Real world - Nagarkot hillside (SRTM, UTM 45N)", "crs": CRS,
                                      "description": "SRTM 30 m terrain below Nagarkot, Nepal. Real coordinates, terrain points only."}).json()
    pid = p["id"]
    with POINTS.open("rb") as f:
        print(c.post(f"/api/projects/{pid}/import", files={"file": (POINTS.name, f)}).json()["points_added"], "points")
    j = c.post(f"/api/projects/{pid}/tin", json={}).json()
    print("tin", j["status"], j.get("result", {}).get("n_triangles"))
    j = c.post(f"/api/projects/{pid}/contours", json={"interval": 2.0, "major_every": 5, "style": {"label_every": 100}}).json()
    print("contours", j["status"], j.get("result", {}).get("n_lines"))
    print(f"open {base}/#/p/{pid}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", metavar="URL", help="load into a running server, e.g. http://127.0.0.1:8000")
    ap.add_argument("--fetch", action="store_true", help="re-download elevations even if the CSV exists")
    ap.add_argument("--user")
    ap.add_argument("--password")
    args = ap.parse_args()
    if args.fetch or not POINTS.exists():
        lon, lat = site_lonlat(N_POINTS)
        print(f"fetching {len(lon)} SRTM elevations from {API} ...", file=sys.stderr)
        z = fetch_elevations(lon, lat)
        write_points(lon, lat, z)
        print("wrote", POINTS, f"({len(lon)} points, z {z.min():.0f}..{z.max():.0f} m)")
    else:
        print("using existing", POINTS)
    if args.load:
        load(args.load, args.user, args.password)
    return 0


if __name__ == "__main__":
    sys.exit(main())
