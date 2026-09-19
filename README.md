# Picasso LandMesh (PLM)

Web-based terrain modelling for road and land surveys - 
Users work **in the browser**: import survey points, triangulate, generate styled
contours, view them in 3D or over OpenStreetMap, draw alignments with chainage, generate profiles
and cross-sections in a split screen, comment and take turns with team-mates, and export DXF for
AutoCAD.

```
plm/
  plm/engine/    pure geometry engine (points, TIN, contours, alignment, sections, CRS, io)
  plm/api/       FastAPI backend: projects, import, jobs, GeoPackage store, auth, collaboration
  plm/cli.py     `plm info|tin` command line
  plm/serve.py   run API + built front end locally
  plm/worker.py  background job worker (cron)
  plm/admin.py   user administration
  web/           Vite + TypeScript + CesiumJS + D3 front end
  tests/         pytest (engine + API)   ·   web/e2e/smoke.mjs  Playwright browser smoke test
  examples/      demo data generators
  passenger_wsgi.py   cPanel / Passenger entry point
```

## Quick start

```powershell
cd plm
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]" fastapi "uvicorn[standard]" python-multipart a2wsgi openpyxl
cd web ; npm install ; npm run build ; cd ..
.venv\Scripts\python -m plm.serve                                   # http://127.0.0.1:8000
.venv\Scripts\python examples\make_demo.py --load http://127.0.0.1:8000   # demo projects
```

Open http://127.0.0.1:8000, pick a demo project: **Data** → **TIN** → **Contours** →
**Alignment** (draw IPs on the map, set radii) → **Sections** (profile & cross-section charts) →
**Export** (DXF / GeoPackage / GeoJSON / CSV). API docs: http://127.0.0.1:8000/api/docs.

## Features

| Area | What you can do |
|---|---|
| Import | CSV/TXT/XYZ (`x y z`, `id x y z remark`, header auto-detected), GeoJSON (points, feature lines, boundary/void polygons), DXF (legacy POINTS blocks, POINT, polylines by layer), XLSX |
| TIN | constrained Delaunay honouring feature lines (same Triangle algorithm as the legacy DLL), boundary clip (inside rule or legacy crossing rule), voids, duplicate removal, crossing-feature detection, elevation-coloured / shaded / wireframe display, spot heights, quick profile |
| Contours | interval, index contours, datum, smoothing, vertex thinning; colours (index/minor or elevation ramp), widths, opacity; label text format/prefix/suffix and spacing; multiple sets side by side |
| View | CesiumJS 3D / 2D / 2.5D, OpenStreetMap / OpenTopoMap / Carto / Esri base maps for georeferenced projects, local-grid mode for legacy metre coordinates |
| Alignment | draw IPs on the map, drag them, set curve radii with live geometry (T, L, BC/MC/EC), validity checks, chainage labels, import legacy `*_aln.csv` / GeoJSON / DXF, export CSV; per-alignment **editing turn** (lock), version history with restore |
| Sections | profile + cross-sections at an interval and left/right width, exact TIN edge crossings, split-screen D3 charts with vertical exaggeration, station browsing synchronised with the map, `Profile.csv` / `Cross.csv` in the legacy layout |
| Export | DXF R2010 with legacy layer names (points blocks, 3DFACE TIN, contours at elevation, labels, H_ALIGN with arcs, chainage ticks, section lines), GeoPackage (QGIS), GeoJSON, CSV |
| Team | simple login (first user = admin), shared projects (org-wide or private), members, comments with map pins and replies, resolve/reopen, activity feed |
| Coordinates | local grid (legacy default), Nepal MUTM 81/84/87, UTM 44N/45N, WGS84, custom EPSG/PROJ, Helmert fit from control points |

## Development

```powershell
.venv\Scripts\python -m pytest -q          # engine + API tests
.venv\Scripts\python -m ruff check plm tests examples
cd web ; npm run dev                       # Vite dev server on :5173 proxying /api to :8000
node e2e\smoke.mjs http://127.0.0.1:8000 <projectId>   # browser smoke test (Playwright)
```

Engine API:

```python
from plm.engine import build_tin, contour_tin, contour_labels
from plm.engine.alignment import HorizontalAlignment, IP
from plm.engine.sections import generate_profile, generate_cross_sections
from plm.engine.io import read_points_csv, write_dxf

pts = read_points_csv("survey.csv")
res = build_tin(pts, feature_lines=[...], boundary=polygon)
lines = contour_tin(res.tin, interval=1.0, major_every=5)
al = HorizontalAlignment([IP(0, 0), IP(200, 50, radius=80), IP(400, 60)], start_chainage=0)
profile = generate_profile(res.tin, al, interval=20)
write_dxf("out.dxf", points=pts, tin=res.tin, contours=lines, contour_labels=contour_labels(lines, 100))
```

See `../docs/LEGACY_ANALYSIS.md` (what the VB6 program did), `../docs/WEB_PORT_PLAN.md` (plan)
and `../docs/DEPLOYMENT.md` (cPanel / Passenger deployment, configuration, backups).
