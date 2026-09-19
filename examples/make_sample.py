"""Generate a synthetic hillside survey for trying the CLI (no legacy sample data exists).

    python examples/make_sample.py            -> examples/sample_points.csv, sample_lines.geojson
    plm tin examples/sample_points.csv examples/sample_lines.geojson --interval 1 --major 5 \
        --label-every 60 --dxf examples/sample.dxf --geojson examples/sample_contours.geojson
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent


def surface(x, y):
    # gentle hillside with a ridge and a small knoll (local grid metres, MUTM-like magnitudes)
    return (
        1400.0
        + 0.06 * (x - 500_000)
        + 0.02 * (y - 3_050_000)
        + 6.0 * np.exp(-(((x - 500_180) ** 2 + (y - 3_050_120) ** 2) / (2 * 40.0**2)))
        + 3.0 * np.sin((x - 500_000) / 35.0)
    )


def main() -> None:
    rng = np.random.default_rng(42)
    n = 1500
    x = rng.uniform(500_000, 500_400, n)
    y = rng.uniform(3_050_000, 3_050_250, n)
    z = surface(x, y) + rng.normal(0, 0.05, n)
    remarks = rng.choice(["", "", "", "GL", "TREE", "HOUSE", "ROAD", "RIVER"], n)
    with (HERE / "sample_points.csv").open("w", encoding="utf-8") as f:
        f.write("PtNo,Easting,Northing,Elevation,Remark\n")
        for i in range(n):
            f.write(f"{i + 1},{x[i]:.3f},{y[i]:.3f},{z[i]:.3f},{remarks[i]}\n")

    # a stream (feature line) cutting a channel, and a survey boundary
    sx = np.linspace(500_020, 500_380, 25)
    sy = 3_050_060 + 30 * np.sin((sx - 500_000) / 60.0) + 0.25 * (sx - 500_000)
    stream = [[float(a), float(b), float(surface(a, b) - 1.5)] for a, b in zip(sx, sy)]
    boundary = [
        [500_010, 3_050_010], [500_390, 3_050_010], [500_390, 3_050_240],
        [500_200, 3_050_235], [500_010, 3_050_240], [500_010, 3_050_010],
    ]
    gj = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": stream},
             "properties": {"kind": "feature", "layer": "Features", "name": "stream"}},
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [boundary]},
             "properties": {"kind": "boundary", "layer": "Boundary"}},
        ],
    }
    (HERE / "sample_lines.geojson").write_text(json.dumps(gj, indent=1), encoding="utf-8")
    print("wrote sample_points.csv and sample_lines.geojson")


if __name__ == "__main__":
    main()
