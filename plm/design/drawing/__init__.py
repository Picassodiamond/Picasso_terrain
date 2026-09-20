"""Drawing sheets and data exports for road designs.

The drawing model is a list of primitives in *sheet millimetres* (origin bottom-left, y up, the
DXF convention) grouped into `Sheet`s. Plan, longitudinal section and cross-section builders fill
sheets from the design inputs; the same sheets are rendered to DXF (AutoCAD, one paper-space
layout per sheet) and to SVG (browser preview), so what the user previews is what AutoCAD opens.

model     primitives (Polyline, Text, Circle, Hatch), Sheet, layer table, paper sizes
frame     sheet template (margins, title block, notes), settings, north arrow, scale bar, tables
plan      plan sheets: contours, centreline, IPs, tangents, chainages, daylight, structures
profile   longitudinal section sheets with data bands
sections  cross-section sheets in a grid with cut / fill hatching and level bands
dxf       sheets -> DXF (mm) and the model-space design DXF in project coordinates (m)
svg       sheet -> SVG string
excel     design data workbook (openpyxl)
"""
