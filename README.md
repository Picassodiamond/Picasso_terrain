# Picasso LandMesh (PLM)

Web-based terrain modelling for road and land surveys - 
Users work **in the browser**: import survey points, triangulate, generate styled
contours, view them in 3D or over OpenStreetMap, draw alignments with chainage, generate profiles
and cross-sections in a split screen, comment and take turns with team-mates, and export DXF for
AutoCAD.

## Frequent commands

```powershell
.venv\Scripts\python -m plm.serve                                        # API + built front end at http://127.0.0.1:8000
.venv\Scripts\python examples\make_demo.py --load http://127.0.0.1:8000  # load demo projects
.venv\Scripts\python examples\make_realworld.py --load http://127.0.0.1:8000  # real-world terrain sample (SRTM, UTM 45N)
cd web ; npm run dev                                                     # Vite dev server on :5173, proxies /api to :8000
.venv\Scripts\python -m pytest -q                                       # engine + API tests
```

## Layout

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
  examples/      demo data generators; examples/realworld/ real SRTM terrain sample (Nagarkot, UTM 45N)
  plm/api/modules.py  module registry (terrain core + design modules)
  plm/design/    road design engine: standards, horizontal checks, vertical, superelevation, templates, corridor, earthworks, structures
  web/src/modules/    design workspaces (road: plan / profile / section shell)
  plm/api/catalogue.py, archive.py   asset library, archive bundles, backups
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
| Constraints | boundaries, holes (voids), islands and breaklines are *constraint segments* of the triangulation - no triangle ever crosses one; rings nest to any depth (island inside a hole); **automatic** detection of the survey limit and unsurveyed gaps from the points, **semi-automatic** review (accept / reject suggestions), or fully **manual** drawing; edit, re-kind or delete every constraint line |
| TIN | constrained Delaunay (Shewchuk Triangle, the legacy DLL algorithm) on points + constraints; exact duplicate removal (k-d tree), collinear / degenerate input reported clearly; long-edge and min-angle filters peel bad edge triangles (never across a constraint); rejected triangles kept for display with their reason; structural validation (manifold, orientation, constraint edges present); legacy crossing rule still available; elevation-coloured / shaded / wireframe display, spot heights, quick profile |
| Contours | generated from the validated TIN only; one consistent crossing rule so contours pass cleanly through vertices, along flat triangles and around holes with no gaps or duplicate segments; exact chaining by edge identity (fast on large meshes); interval, index contours, datum, optional clip polygon, smoothing, vertex thinning; colours (index/minor or elevation ramp), widths, opacity; label text format/prefix/suffix and spacing; multiple sets side by side |
| View | CesiumJS 3D / 2D / 2.5D, OpenStreetMap / OpenTopoMap / Carto / Esri base maps for georeferenced projects, local-grid mode for legacy metre coordinates; **Height** placement: rest the lowest point on the base map (default, display only) or show true elevation above the ellipsoid |
| Alignment | draw IPs on the map, drag them, set curve radii with live geometry (T, L, BC/MC/EC), validity checks, chainage labels, import legacy `*_aln.csv` / GeoJSON / DXF, export CSV; per-alignment **editing turn** (lock), version history with restore |
| Sections | profile + cross-sections at an interval and left/right width, exact TIN edge crossings, split-screen D3 charts with vertical exaggeration, station browsing synchronised with the map, `Profile.csv` / `Cross.csv` in the legacy layout |
| Export | DXF R2010 with legacy layer names (points blocks, 3DFACE TIN, contours at elevation, labels, H_ALIGN with arcs, chainage ticks, section lines), GeoPackage (QGIS), GeoJSON, CSV |
| Team | simple login (first user = admin), shared projects (org-wide or private), members, comments with map pins and replies, resolve/reopen, activity feed |
| Coordinates | local grid (legacy default), Nepal MUTM 81/84/87, UTM 44N/45N, WGS84, custom EPSG/PROJ, Helmert fit from control points |

## Constraint workflow

```
XYZ points -> automatic analysis -> suggested constraints -> user review / edit -> constrained TIN -> validated triangles -> contours
```

* **Automatic** (default): when no boundary has been drawn, imported or accepted, `Build TIN` detects the
  survey limit (long Delaunay triangles peeled from the convex hull) and unsurveyed gaps (groups of long
  interior triangles), stores them as constraint lines with source `auto` (dashed on the map) and uses them.
* **Semi-automatic**: `Detect constraints` previews the suggestions with a reason and confidence; untick
  what you do not want and `Accept`. Accepted lines are ordinary constraints you can edit or delete.
* **Manual**: only drawn / imported lines are used. Draw breaklines, boundaries and holes on the map; change a
  line's kind or delete it under *Data > Constraint lines*.

Engine entry points: `plm.engine.suggest_constraints(xyz)` -> suggestions, `build_tin(points, constraints=[...],
max_edge_length=..., min_angle_deg=...)` -> `TinResult` (TIN, rejected triangles, validation, stats),
`contour_tin(tin, interval, clip=...)`. API: `POST /constraints/detect`, `POST /constraints/accept`,
`GET /constraints`, `DELETE /constraints/auto`, `POST /tin` with `constraint_mode`, `GET /tin/{run}/rejected.geojson`.

## Accounts, sharing, guests, archive, load

* **Invite-only accounts.** With `PLM_AUTH_ENABLED=1` the first registration creates the administrator; after that
  self-registration is closed and the admin creates every account (username, password, role, organisation, name,
  email) on the **Admin** page or with `python -m plm.admin create-user`. Passwords are reset and accounts disabled there too.
* **Project roles.** owner (one per project, `owner_id`), editor, viewer. Only the owner (or an admin) adds or removes
  members, changes roles, changes visibility (private / organisation / public), transfers ownership, archives or
  deletes. Editors change data and designs; viewers read and comment. Every project route checks the role.
* **Guest sandbox** (`PLM_GUEST_ENABLED`, default on): visitors without an account get a private sandbox within quotas
  (`PLM_GUEST_MAX_POINTS` 5000, `PLM_GUEST_MAX_PROJECTS` 2, `PLM_GUEST_MAX_TIN_RUNS` 3, no exports, deleted after
  `PLM_GUEST_TTL_DAYS` 7). Signing in claims the sandbox projects for the account. Guest jobs queue behind accounts.
* **Queue and load.** Jobs carry `queue_position`, `queue_length` and `eta_seconds`; the UI shows "waiting in queue: 2 of 3".
  `PLM_JOB_MODE=worker` makes the web process only enqueue and `python -m plm.worker --loop 2` execute (one running job per
  user, guests last). Heavy synchronous calls (detection, profiles, tiles) share `PLM_MAX_HEAVY` slots and answer
  503 + Retry-After when full (the client retries); a memory guard refuses builds that would not fit (507).
  `/api/health` reports queue depth and load and a `busy` flag that the workspace turns into a banner.
* **Archive.** Owners archive a project into a portable zip (GeoPackage + meta.json) under `<data>/archive`; the project
  becomes read-only (423 on writes) until restored. `python -m plm.admin backup [--out DIR]` writes a consistent copy of
  the app database and every project file.
* **Library** (`#/library`): every TIN run and design is catalogued with a WGS84 footprint, size, CRS, tags and lineage,
  searchable by text, tag and bounding box, and can be cloned into a new project. Visibility follows the project.

## Modules and design workspaces

The terrain workspace is the core. Design work happens in separate **design workspaces** (modules)
that are pinned to one immutable TIN run - the terrain snapshot - and switch with the module tabs in
the top bar (`#/p/{project}` terrain, `#/p/{project}/road/{design}` design). Modules are registered in
`plm/api/modules.py` (backend: availability, requirements, stages) and `web/src/modules/registry.ts`
(front end: lazy-loaded workspace chunk).

| Module | Status | What it is |
|---|---|---|
| Terrain | core | survey points, constraints, TIN, contours, alignments, sections |
| Road design | engine + workspace | horizontal alignment with circular curves and clothoid transitions (IPs dragged on the plan, curve table, superelevation development), vertical alignment with parabolic curves (fit to ground, PVIs dragged in the profile, grades and K values), typical-section templates by chainage (lanes, shoulders, verges, kerbs; cut slope with benches and side ditch, fill slope), corridor sweep with daylighting, cut / fill areas, end-area or prismoidal volumes and mass haul, retaining / breast / toe / catch walls and culverts and drains chosen from a type catalogue with quantities; Output stage with drawing sheets on a standard template (plan, longitudinal section, cross-sections) previewed in the browser and exported to AutoCAD DXF, a model-space design DXF, an Excel workbook and CSV tables; every check quotes the standard's value and clause (`plm/design/standards/*.json`, Nepal Road Standard 2070 transcribed with clause references from `Resource/Nepal Road Standard (NRS) 2070.pdf`, status *verified*; application defaults are marked *default*) and designs may record deviations |
| Canal design | planned | |
| Building site | planned | |

API: `GET /api/modules`, `GET/POST /projects/{id}/designs`, `GET/PATCH/DELETE /projects/{id}/designs/{did}`
(PATCH `tin_run_id` = rebase onto a newer terrain). "Open in Road design" on an alignment creates a design
seeded with it.

## Road design engine (`plm/design`)

```
horizontal (IPs, R, Ls) -> checks -> vertical (PVIs, L) -> templates + superelevation -> corridor -> areas / volumes / mass haul -> structures
```

* `standards.py` - parameter tables as data with source clause and status (verified / default / placeholder / deviation); numeric keys
  are matched floor / ceil or interpolated per table. `nrs-2070.json` holds the Nepal Road Standard 2070 (classes I-IV, terrain by cross
  slope, Tables 7-1 to 13-3 and the cl. 19 aesthetics rules) transcribed from the PDF in `Resource/`; legacy class and material names
  are mapped through the file's `meta.legacy_*` tables.
* `plm.engine.alignment` now supports symmetric clothoid transitions (`IP.transition`); `*_aln.csv` accepts a fifth column
  (Road layout). `horizontal.py` checks radius, transition need / length and superelevation demand.
* `vertical.py` - PVIs with symmetric parabolic curves, K values, high / low points, gradient and K checks, fit-to-ground.
* `superelevation.py` - e from e + f = V^2 / 127 R, runoff over the spiral or from the relative gradient, rotation about the centreline.
* `template.py` / `corridor.py` - components outward from the centreline to the hinge, then cut (ditch, slope, benches) or fill daylighting
  against the TIN ground line; exact piecewise-linear cut and fill areas; `earthworks.py` volumes and mass haul; `structures.py` wall and
  culvert suggestions (fill height, no-catch, stream crossings, sag points). Walls (retaining, breast, toe, catch) come in eight types
  from dry stone to RCC counterfort and reinforced soil, drains in ten types from earthen trapezoid to stepped cascade and perforated
  pipe, culverts in six; each type carries its section geometry (drawn on the cross-sections), the height or span it suits and the
  materials it consumes, so `bill_of_quantities()` gives masonry, gabion, concrete, steel, excavation and lining per structure.
  Drain linings follow NRS 2070 Table 13-3 from the grade at that chainage; `check_structures()` reports span, lining, gradient,
  offset, outlet spacing and wall height against the standard.
* API under `/projects/{id}/designs/{did}/road`: `horizontal` (GET/PUT, `preview`), `ground`, `vertical` (GET/PUT, `auto`), `templates`,
  `corridor` (POST build, GET latest, `section`, `volumes.csv`), `structures` (GET/PUT, `suggest`), `standards`.
* `drawing/` - drawing sheets and data exports. One primitive model in sheet millimetres (`model.py`) is rendered to DXF
  (`dxf.py`: sheets side by side in model space plus one paper-space layout per sheet at 1:1, so AutoCAD plots them directly)
  and to SVG (`svg.py`, the browser preview), so the preview is exactly what AutoCAD opens. `frame.py` holds the sheet template
  (`plm/design/sheets/default.json`: paper, margins, notes, data table and title-block rows bound to fields) and the per-design
  settings; `plan.py` splits the alignment into sheets that fit the paper at the plan scale (rotated along the road or north up)
  with contours, IPs and tangents, chainage ticks, curve key points, corridor daylight lines, structures, match lines and the curve
  table; `profile.py` draws the longitudinal section with data bands (grade, design and ground levels, cut / fill, chainage,
  horizontal alignment diagram, superelevation) and splits sheets by the level range; `sections.py` packs the cross-sections in a
  grid with cut / fill hatching and offset / level bands; `excel.py` writes the workbook (horizontal, superelevation, vertical,
  levels, sections, volumes with live cut / fill factor formulas, mass haul, section points, structures, checks, standard).
  A model-space DXF in project coordinates (centreline with arcs as bulges, 3-D design lines, sections, structures) is separate.
* Output routes: `sheets` (GET index + settings, PUT `sheets/settings`), `sheets/{plan|profile|sections}/{n}.svg` (preview, free for
  guests), `export/sheets.dxf?kinds=`, `export/model.dxf`, `export/design.xlsx` (signed-in users).

## User help pages

End-user documentation lives in `web/public/help/` (static HTML, no build step) and is served with the app at
`/help/index.html`; the **Help** buttons in the projects page, the terrain workspace, the road workspace and the
Output stage open the matching page. Pages: getting started, terrain module, road design, standards and checks,
accounts and sharing, troubleshooting. Screenshots are generated by `node e2e\help_shots.mjs` against a running
server (a georeferenced terrain project with contours, alignment and sections, and a project with a built road
corridor) into `web/public/help/img/`; re-run it after UI changes so the pictures match the software.

## Large datasets

Measured on an 8 GB laptop with random 5 m survey points:

| Points | Triangles | TIN build | Peak RAM (build) | Held after build | mesh to browser |
|---|---|---|---|---|---|
| 250k | 0.5 M | 6 s | 335 MB | 130 MB | 9 MB |
| 1 M | 2.0 M | 28 s | 1.2 GB | 350 MB | 36 MB (as 25 tiles) |

* Builds above `PLM_SYNC_POINT_LIMIT` (250k points) run as background jobs; memory is released when the job ends.
* The last `PLM_TIN_CACHE` (default 2) loaded TINs are kept per process so spot heights, profiles and
  tiles do not reload the mesh on every request; runs are evicted when deleted.
* Point location and line sampling use a numpy grid over the triangles (about 10 bytes per triangle)
  instead of one geometry per triangle; the TIN outline is built from boundary edges only.
* The browser receives points as `points.bin` (16 bytes per point). Up to 150k points are individual
  pickable primitives; above that a single GPU point cloud is drawn and a click asks `/points/nearest`.
* Meshes above 300k triangles are served as tiles (`/tin/{run}/tiles`, `PLM_TILE_TRIANGLES` per tile)
  and drawn progressively, nearest the centre first; Cesium culls tiles outside the view.

## Development

```powershell
.venv\Scripts\python -m pytest -q          # engine + API tests
.venv\Scripts\python -m ruff check plm tests examples
cd web ; npm run dev                       # Vite dev server on :5173 proxying /api to :8000
node e2e\smoke.mjs http://127.0.0.1:8000 <projectId>   # browser smoke test (Playwright)
node e2e\basemap_check.mjs http://127.0.0.1:8000 <projectId>   # base map tiles load + height placement (Playwright)
node e2e\constraints_check.mjs http://127.0.0.1:8000 <projectId>   # detect -> accept -> build -> rejected layer (Playwright)
node e2e\bigdata_check.mjs http://127.0.0.1:8000 <projectId>   # tiled mesh + point cloud + nearest-point click (Playwright)
node e2e\modules_check.mjs http://127.0.0.1:8000 <projectId>   # terrain -> Open in Road design -> plan / profile / section -> back (Playwright)
node e2e\road_check.mjs http://127.0.0.1:8000 <projectId>   # road design: IPs -> fit profile -> templates -> corridor -> structures (Playwright)
node e2e\svg_shot.mjs <folder> [zoom] [x y w h]   # render exported sheet SVGs to PNG (visual check of drawings)
node e2e\help_shots.mjs http://127.0.0.1:8000 <terrainProjectId> <roadProjectId>   # refresh the screenshots of the user help pages
node e2e\help_check.mjs http://127.0.0.1:8000   # help pages: served, images load, anchors exist (Playwright)
node e2e\auth_check.mjs http://127.0.0.1:8011   # accounts: guest sandbox -> sign in claims it -> library -> admin (server with PLM_AUTH_ENABLED=1)
```

Engine API:

```python
from plm.engine import build_tin, contour_tin, contour_labels, suggest_constraints, Constraint
from plm.engine.alignment import HorizontalAlignment, IP
from plm.engine.sections import generate_profile, generate_cross_sections
from plm.engine.io import read_points_csv, write_dxf

pts = read_points_csv("survey.csv")
suggested = suggest_constraints(pts.xyz)                       # boundary + gaps, with reasons
constraints = [s.to_constraint() for s in suggested.suggestions] + [Constraint("breakline", ridge_xyz)]
res = build_tin(pts, constraints=constraints, max_edge_factor=3.0, min_angle_deg=5.0)
res.rejected.counts(), res.validation                           # what was removed and why; [] when sound
lines = contour_tin(res.tin, interval=1.0, major_every=5)
al = HorizontalAlignment([IP(0, 0), IP(200, 50, radius=80), IP(400, 60)], start_chainage=0)
profile = generate_profile(res.tin, al, interval=20)
write_dxf("out.dxf", points=pts, tin=res.tin, contours=lines, contour_labels=contour_labels(lines, 100))
```

See `../docs/LEGACY_ANALYSIS.md` (what the VB6 program did), `../docs/WEB_PORT_PLAN.md` (plan)
and `../docs/DEPLOYMENT.md` (cPanel / Passenger deployment, configuration, backups).
