"""Command line front-end for the engine.

Examples
--------
    plm tin points.csv --interval 1 --major 5 --dxf out.dxf --geojson contours.geojson
    plm tin survey.dxf --point-layers Points-Blk --feature-layers Features --boundary-layers Boundary --dxf out.dxf
    plm info points.csv
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from .engine import build_tin, contour_labels, contour_tin
from .engine.io import (
    contours_to_geojson,
    features_to_geojson,
    points_to_geojson,
    read_dxf,
    read_geojson,
    read_points_csv,
    write_dxf,
)
from .engine.io.geojson_io import write_geojson
from .engine.points import PointSet


def _load_inputs(args) -> tuple[PointSet, list[np.ndarray], list, list]:
    points = PointSet.empty()
    features: list[np.ndarray] = []
    boundary: list = []
    voids: list = []
    for src in args.inputs:
        p = Path(src)
        ext = p.suffix.lower()
        if ext == ".dxf":
            d = read_dxf(p, point_layers=args.point_layers, line_layers=None)
            points = PointSet.concat([points, d.points])
            for ln in d.lines:
                lay = ln["layer"].upper()
                if args.boundary_layers and lay in {s.upper() for s in args.boundary_layers}:
                    boundary.append(ln["coords"])
                elif args.void_layers and lay in {s.upper() for s in args.void_layers}:
                    voids.append(ln["coords"])
                elif args.feature_layers and lay in {s.upper() for s in args.feature_layers}:
                    features.append(ln["coords"])
                elif not args.feature_layers and ln["kind"] == "feature" and lay == "FEATURES":
                    features.append(ln["coords"])
                elif not args.boundary_layers and ln["kind"] == "boundary":
                    boundary.append(ln["coords"])
                elif not args.void_layers and ln["kind"] == "void":
                    voids.append(ln["coords"])
        elif ext in (".geojson", ".json"):
            g = read_geojson(p)
            points = PointSet.concat([points, g.points])
            features.extend(g.lines_of_kind("feature"))
            boundary.extend(g.polygons_of_kind("boundary"))
            boundary.extend(g.lines_of_kind("boundary"))
            voids.extend(g.polygons_of_kind("void"))
            voids.extend(g.lines_of_kind("void"))
        else:
            points = PointSet.concat([points, read_points_csv(p, delimiter=args.delimiter)])
    return points, features, boundary, voids


def cmd_info(args) -> int:
    points, features, boundary, voids = _load_inputs(args)
    print(f"points: {len(points)}  features: {len(features)}  boundary: {len(boundary)}  voids: {len(voids)}")
    if len(points):
        print(f"x: {points.x.min():.3f} .. {points.x.max():.3f}")
        print(f"y: {points.y.min():.3f} .. {points.y.max():.3f}")
        print(f"z: {points.z.min():.3f} .. {points.z.max():.3f}")
        print("first rows:")
        for i in range(min(5, len(points))):
            print(f"  {points.ids[i]!r:>8} {points.x[i]:.3f} {points.y[i]:.3f} {points.z[i]:.3f} {points.remarks[i]!r}")
    return 0


def cmd_tin(args) -> int:
    t0 = time.perf_counter()
    points, features, boundary, voids = _load_inputs(args)
    if args.no_features:
        features = []
    bnd = boundary[0] if len(boundary) == 1 else (boundary if boundary else None)
    res = build_tin(
        points,
        features,
        boundary=bnd,
        voids=voids,
        dedupe_tol=args.tol,
        drop_zero_z=args.drop_zero_z,
        boundary_mode=args.boundary_mode,
    )
    t1 = time.perf_counter()
    print(json.dumps(res.stats, indent=2))
    kinds: dict[str, int] = {}
    for it in res.issues:
        kinds[it.kind] = kinds.get(it.kind, 0) + 1
    if kinds:
        print("issues:", kinds)
        for it in [i for i in res.issues if i.kind == "crossing_features"][:10]:
            print(f"  crossing at {it.x:.3f}, {it.y:.3f}")
    contours = []
    labels = []
    if args.interval > 0:
        contours = contour_tin(
            res.tin, args.interval, args.major, base=args.base,
            min_spacing=args.min_spacing, smoothing=args.smoothing,
        )
        labels = contour_labels(contours, every_m=args.label_every, fmt=args.label_format) if args.label_every > 0 else []
        n_major = sum(1 for c in contours if c.is_major)
        print(f"contours: {len(contours)} pieces ({n_major} major), levels {len({c.level for c in contours})}")
    t2 = time.perf_counter()
    if args.dxf:
        from .engine.io.dxf_export import DxfStyle

        write_dxf(
            args.dxf,
            points=points if args.export_points else None,
            tin=res.tin if args.export_tin else None,
            contours=contours,
            contour_labels=labels,
            feature_lines=features,
            boundary_lines=[np.asarray(getattr(b, "exterior", b).coords if hasattr(b, "exterior") else b) for b in boundary],
            style=DxfStyle(arc_smoothing=args.smoothing == "arc", text_height=args.text_height),
        )
        print(f"wrote {args.dxf}")
    if args.geojson:
        write_geojson(contours_to_geojson(contours), args.geojson)
        print(f"wrote {args.geojson}")
    if args.points_geojson:
        write_geojson(points_to_geojson(points), args.points_geojson)
    if args.features_geojson and features:
        write_geojson(features_to_geojson(features), args.features_geojson)
    print(f"timing: tin {t1 - t0:.2f}s  contours {t2 - t1:.2f}s  total {time.perf_counter() - t0:.2f}s")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="plm", description="terrain engine CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_inputs(p):
        p.add_argument("inputs", nargs="+", help="CSV/TXT/XYZ, GeoJSON or DXF files")
        p.add_argument("--delimiter", default="auto", help="CSV delimiter (auto, ',', ';', tab, space)")
        p.add_argument("--point-layers", nargs="*", default=None)
        p.add_argument("--feature-layers", nargs="*", default=None)
        p.add_argument("--boundary-layers", nargs="*", default=None)
        p.add_argument("--void-layers", nargs="*", default=None)

    pi = sub.add_parser("info", help="inspect inputs")
    add_inputs(pi)
    pi.set_defaults(func=cmd_info)

    pt = sub.add_parser("tin", help="triangulate, contour and export")
    add_inputs(pt)
    pt.add_argument("--tol", type=float, default=0.001, help="duplicate tolerance in metres")
    pt.add_argument("--drop-zero-z", action="store_true", help="legacy: discard points with Z = 0")
    pt.add_argument("--no-features", action="store_true", help="legacy 'Process Without Feature'")
    pt.add_argument("--boundary-mode", choices=["inside", "legacy_cross"], default="inside")
    pt.add_argument("--interval", type=float, default=1.0, help="contour interval (0 = no contours)")
    pt.add_argument("--major", type=int, default=5, help="index contour every N intervals")
    pt.add_argument("--base", type=float, default=0.0)
    pt.add_argument("--min-spacing", type=float, default=0.0)
    pt.add_argument("--smoothing", choices=["none", "chaikin", "arc"], default="none")
    pt.add_argument("--label-every", type=float, default=0.0, help="label spacing in metres (0 = none)")
    pt.add_argument("--label-format", default="{z:.2f}")
    pt.add_argument("--text-height", type=float, default=1.0)
    pt.add_argument("--dxf", help="output DXF path")
    pt.add_argument("--geojson", help="output contours GeoJSON path")
    pt.add_argument("--points-geojson")
    pt.add_argument("--features-geojson")
    pt.add_argument("--export-points", action="store_true", help="include points (POINTS blocks) in DXF")
    pt.add_argument("--export-tin", action="store_true", help="include 3DFACE triangles in DXF")
    pt.set_defaults(func=cmd_tin)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.delimiter == "tab":
        args.delimiter = "\t"
    elif args.delimiter == "space":
        args.delimiter = None
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
