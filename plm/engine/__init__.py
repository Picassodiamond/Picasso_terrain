"""Pure-Python terrain engine: no web, no database imports.

Modules
-------
points       - PointSet container, de-duplication, feature-line Z interpolation
constraints  - constraint primitives (boundary / hole / breakline), nesting, automatic detection
tin          - constrained Delaunay triangulation, topology, classification, filters, sampling
contour      - contour generation on a TIN, smoothing, labels
io           - importers/exporters (CSV, GeoJSON, DXF)

Default workflow: points -> suggest_constraints -> (user review) -> build_tin -> contour_tin.
"""

from .points import PointSet, DedupeReport, dedupe, fill_zero_z_along_line
from .constraints import Constraint, ConstraintSet, ConstraintIssue, Suggestion, DetectionResult, suggest_constraints
from .tin import (TIN, TinResult, TinIssue, RejectedTriangles, REJECT_REASONS, build_tin, clip_to_boundary,
                  filter_triangles, delaunay_tin, surface_z, TriangleAdapter)
from .contour import ContourLine, ContourLabel, contour_tin, contour_labels, levels_for

__all__ = [
    "PointSet", "DedupeReport", "dedupe", "fill_zero_z_along_line",
    "Constraint", "ConstraintSet", "ConstraintIssue", "Suggestion", "DetectionResult", "suggest_constraints",
    "TIN", "TinResult", "TinIssue", "RejectedTriangles", "REJECT_REASONS", "build_tin", "clip_to_boundary",
    "filter_triangles", "delaunay_tin", "surface_z", "TriangleAdapter",
    "ContourLine", "ContourLabel", "contour_tin", "contour_labels", "levels_for",
]
