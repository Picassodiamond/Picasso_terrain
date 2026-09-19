"""Pure-Python terrain engine: no web, no database imports.

Modules
-------
points   - PointSet container, de-duplication, feature-line Z interpolation
tin      - constrained Delaunay triangulation, topology, boundary clipping, sampling
contour  - contour generation on a TIN, smoothing, labels
io       - importers/exporters (CSV, GeoJSON, DXF)
"""

from .points import PointSet, DedupeReport, dedupe, fill_zero_z_along_line
from .tin import TIN, TinResult, TinIssue, build_tin, clip_to_boundary, TriangleAdapter
from .contour import ContourLine, ContourLabel, contour_tin, contour_labels, levels_for

__all__ = [
    "PointSet", "DedupeReport", "dedupe", "fill_zero_z_along_line",
    "TIN", "TinResult", "TinIssue", "build_tin", "clip_to_boundary", "TriangleAdapter",
    "ContourLine", "ContourLabel", "contour_tin", "contour_labels", "levels_for",
]
