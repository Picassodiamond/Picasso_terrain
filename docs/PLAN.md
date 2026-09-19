# Picasso LandMesh (PLM) – Implementation Plan 



## 1. Product scope (v1)

1. **Points → Contours**: import survey points (+ optional feature lines and boundary), triangulate,
   generate contours at an interval with index contours; set contour **label text/naming**, **colours**
   (major/minor or elevation ramp), line weights; view **in 3D** on the TIN and **overlaid on an open
   base map** (OpenStreetMap, OpenTopoMap).
2. **Alignment → Profile & Cross‑sections**: draw an alignment (IPs + radii or free polyline) on the
   map, write **chainage** labels on it, generate the **vertical profile** and **cross‑sections** at an
   interval and half‑width from the TIN, and view them in a **split screen** (map left, profile /
   section charts right) with synchronised hover.
3. **Export** contours, TIN, points and alignment (with chainage text) as **DXF** that AutoCAD opens
   directly, plus GeoJSON / GeoPackage / CSV / LandXML.

Out of scope for v1: Plan Data Entry grid (`.ddf`), detail line‑work from remark codes, custom
block insertion, ProCro→DTM, Excel live link, the legacy profile *sheet* drawing (bands, datum
boxes) – v2 candidate as a DXF/PDF sheet export.

---

## 2. Tech stack (final)

### Backend
| Concern | Choice | Notes |
|---|---|---|
| API | **FastAPI** + Pydantic v2 | served under Passenger via `a2wsgi.ASGIMiddleware` (`passenger_wsgi.py`); local dev with uvicorn |
| Geometry engine | **NumPy, SciPy, Shapely 2.x** | vectorised TIN topology, spatial index (`shapely.STRtree`), interpolation |
| Constrained Delaunay | **`triangle`** (Python bindings to Shewchuk's Triangle – the same algorithm as the legacy DLL) behind an `Triangulator` interface | flag `pq0` not used; use `'p'` (+`'c'` to keep hull) so behaviour matches legacy. Fallback adapter: **PythonCDT** (MPL‑2.0) if the Triangle licence is a problem for a hosted commercial service |
| Contouring | own NumPy edge‑marching on the TIN (port of legacy §4) + `shapely.ops.linemerge`; cross‑check with `matplotlib.tri.TriContourGenerator` in tests | deterministic, no raster step, preserves feature lines |
| CRS | **PyProj** | project CRS presets (§3), transform to EPSG:4326 for Cesium |
| Storage | **SQLite** (app db: users, projects, jobs) + **GeoPackage** per project (points, lines, TIN, contours, alignments) | GeoPackage written with std‑lib `sqlite3` + Shapely WKB (no GDAL needed) |
| DXF | **ezdxf** | pure Python; writes LWPOLYLINE with elevation/bulges, TEXT, INSERT blocks, 3DFACE |
| XLSX | **openpyxl** | import/export |
| LandXML / KML / GPX | `lxml` (or std‑lib `xml.etree`) | small hand‑written readers/writers |
| GDAL | **optional** only (extra formats, rasters); not on the critical path because cPanel shared hosting rarely has it |
| Jobs | job table in SQLite + cron‑driven worker (`python -m plm.worker`) for triangulations > N points; small jobs run inline | Passenger may kill long requests |
| Auth | FastAPI session cookie / JWT, users table, roles viewer/editor/admin | replaces dongle |

### Frontend
| Concern | Choice |
|---|---|
| Build | **Vite + TypeScript** (vanilla TS modules; add Lit or Preact only if UI grows) |
| Map/3D | **CesiumJS** (Apache‑2.0; no Ion token needed when using OSM/OpenTopoMap imagery and our own TIN) |
| Base maps | OpenStreetMap, OpenTopoMap, Esri World Imagery (optional, check ToS) via `UrlTemplateImageryProvider` |
| 2D charts (profile / cross‑section) | **D3** (SVG) – axes, datum, grid, hover sync; export SVG/PNG |
| Layout | CSS grid split view with draggable divider; panels: layers, contour settings, alignment IP table |
| Data transfer | GeoJSON for vectors; binary Float32/Uint32 arrays (or glTF) for TIN meshes |

### Hosting (cPanel + Apache + Passenger)
* cPanel "Setup Python App" → Python 3.11/3.12, `passenger_wsgi.py` exposing `application = ASGIMiddleware(app)`.
* Vite build output in `public_html/` served by Apache; API under `/api` (Passenger app root).
* Confirm wheel availability on the host's Python: numpy, scipy, shapely, pyproj, triangle, ezdxf, openpyxl. All ship manylinux wheels.
* Cron entry every minute for the job worker; SQLite in the app directory (WAL mode).
* File uploads to a per‑project folder outside `public_html`.

---

## 3. Coordinate systems

Legacy data are **plain grid metres with no CRS**. Default therefore stays "**Local grid (no
georeference)**": everything works (TIN, contours, profiles, DXF) but the base‑map overlay is
disabled. The user can assign a CRS at any time (project setting), which enables the map overlay.

Presets (verify parameters with the Survey Department of Nepal before release):

| Preset | Definition |
|---|---|
| Local grid (default, legacy) | none |
| MUTM 81 / 84 / 87 (Nepal Modified UTM, Everest 1830 1937 adj.) | `+proj=tmerc +lat_0=0 +lon_0={81|84|87} +k=0.9999 +x_0=500000 +y_0=0 +a=6377276.345 +rf=300.8017 +towgs84=<datum shift, to be confirmed> +units=m` |
| UTM 44N / 45N (WGS84) | EPSG:32644 / EPSG:32645 |
| WGS84 lat/long | EPSG:4326 (for GeoJSON/KML/GPX imports) |
| Custom | any EPSG code or PROJ string |
| Georeference by control points | 2+ points with local XY and lat/long → Helmert (similarity) transform, for surveys done on an arbitrary local grid |

---

## 4. Input & output formats

### Import
| Format | Content | Rules |
|---|---|---|
| CSV / TXT / XYZ | `x,y,z[,remark]` or `id,x,y,z[,remark]`; header optional; delimiter auto‑detect (comma, tab, space, semicolon); column mapping UI | legacy field‑count rules (2/3/4/5+) reproduced as defaults |
| GeoJSON | Point / MultiPoint (z in coordinate or `z`/`elev` property), LineString for feature lines, Polygon for boundary/void; properties `remark`, `layer` | CRS assumed project CRS unless EPSG:4326 |
| DXF | POINT, INSERT of `POINTS` block (attributes PTNUM/DESC/ELEV), TEXT‑only elevations, POLYLINE/LWPOLYLINE (features, boundary, existing contours with elevation) by layer | legacy layer names pre‑mapped |
| LandXML | `<CgPoints>`, `<Surface>` (TIN), `<Alignments>` | Civil 3D / other survey software interchange |
| KML / KMZ, GPX | points and tracks in WGS84 | needs project CRS set |
| XLSX | same as CSV, sheet + column mapping | replaces Excel COM |
| GeoPackage | point/line layers | round‑trip of our own exports |

### Export
| Format | Content |
|---|---|
| **DXF (R2010)** | layers `Points`, `Points-Blk` (+ block `POINTS` with PTNUM/DESC/ELEV), `Features`, `Boundary`, `Triangle` (3DFACE), `Contour`, `Index_Contour` (LWPOLYLINE with elevation; optional arc smoothing as bulges), `Cont_Annotation` (TEXT), `H_ALIGN` (LWPOLYLINE with bulges), `Chainage` (ticks + TEXT `0+000.00`), `IPN`, `Cross_Section` lines |
| GeoJSON / GeoPackage | all vector layers, with CRS |
| CSV | points; `Profile.csv` (`Chainage,RL,Remarks`), `Cross.csv` (`Chainage,PD,RL,Remarks`) in legacy layout; `*_aln.csv` (`count,startCh` / `N,X,Y,R`) |
| LandXML | surface + alignment + profile |
| SVG / PNG | profile and cross‑section charts |
| glTF | TIN mesh (for other 3D viewers) |

---

## 5. Architecture

```
web/  (Vite + TS)                       plm/api (FastAPI)                   plm/engine (pure Python)
 ├ map/        Cesium viewer, layers     ├ routers/projects, imports,        ├ io/        csv, geojson, dxf, landxml, gpkg
 ├ panels/     contours, alignment, IPs  │           tin, contours,           ├ crs.py     presets, transforms
 ├ charts/     D3 profile & x-section    │           alignments, sections,    ├ points.py  dedupe, validate
 ├ state/      project store, sync bus   │           export, jobs, auth       ├ tin.py     Triangulator iface, triangle adapter,
 └ api/        typed client (OpenAPI)    ├ services/ orchestrate engine       │            boundary clip, edges/topology, STRtree
                                         ├ db/       sqlite models, gpkg      ├ contour.py marching, merge, smoothing, labels
                                         └ worker.py cron job runner          ├ alignment.py IPs→tangents/arcs, stationing, offsets
                                                                              ├ sections.py profile, x-sections from TIN
                                                                              └ dxf_export.py
```

Rules: `engine/` has no FastAPI or DB imports and is fully unit‑tested; `api/` translates HTTP ↔
engine; the frontend never computes geometry except for display.

### Data model (GeoPackage per project)
`project(id, name, crs, created)` · `layer(name, kind, colour, visible)` · `point(id, no, x, y, z, remark, layer)` ·
`line(id, kind feature|boundary|void|contour_in, geom LINESTRING Z, layer)` · `tin(run_id, params, stats)` ·
`tin_node(run_id, idx, x, y, z)` · `tin_tri(run_id, idx, n1, n2, n3)` · `tin_issue(run_id, kind, geom)` ·
`contour_set(id, run_id, interval, major, smoothing, style_json)` · `contour(set_id, level, is_major, geom)` ·
`alignment(id, name, start_ch, style_json)` · `alignment_ip(align_id, seq, label, x, y, radius)` ·
`section_set(id, align_id, run_id, interval, left, right, options)` · `section(set_id, ch, cx, cy, bearing)` ·
`section_pt(section_id, offset, rl, src)` · `profile_pt(set_id, ch, rl, src)`.

### Core API
```
POST /api/projects                          {name, crs}
POST /api/projects/{p}/import               multipart file + {format, mapping, target_layer}
GET  /api/projects/{p}/layers/{l}.geojson   (project CRS or ?crs=4326)
POST /api/projects/{p}/tin                  {dedupe_tol=0.001, use_features, boundary_mode inside|legacy_cross, drop_zero_z}
GET  /api/projects/{p}/tin/{r}/mesh         binary positions+indices (+ ?crs=4326)
GET  /api/projects/{p}/tin/{r}/elevation?x&y
POST /api/projects/{p}/tin/{r}/contours     {interval, major_every, smoothing none|chaikin|arc, min_spacing, label:{format,prefix,suffix,every_m}, style:{major_color,minor_color,ramp,weights}}
GET  /api/projects/{p}/contours/{c}.geojson
POST /api/projects/{p}/alignments           {name, start_ch, ips:[{label,x,y,r}]} | {polyline_with_bulges}
GET  /api/projects/{p}/alignments/{a}/geometry?chainage_every=20   tangents/arcs + tick points + labels
POST /api/projects/{p}/alignments/{a}/sections {run, interval, left, right, include_curve_pts, user_chainages}
GET  /api/projects/{p}/sections/{s}         JSON profile + sections;  .csv (legacy Cross/Profile), .svg
GET  /api/projects/{p}/export.dxf?layers=…  also .gpkg .geojson .xml (LandXML)
GET  /api/jobs/{id}                         status/progress for long runs
```

---

## 6. Algorithms to implement (from the legacy analysis, corrected)

1. **Dedupe**: round XY to tolerance (default 0.001 m), keep first, report duplicates; Z = 0 filtered only if option set.
2. **CDT**: `triangle.triangulate({'vertices', 'segments'}, 'pc')`; if the output vertex count > input,
   report crossing feature lines (compute crossing points with Shapely, offer auto‑split).
3. **Boundary**: default `inside` = keep triangles whose centroid is inside the boundary polygon and
   outside voids (`shapely.contains_xy`); `legacy_cross` option reproduces the old "delete crossing
   triangles" rule.
4. **Topology**: edges via `np.unique` on sorted node pairs; triangle→edge map; edge→triangle map.
5. **Spot height / sampling**: `STRtree` on triangles; barycentric Z. Profile along a line: STRtree on
   edges, intersection points sorted by distance; vertices at every TIN edge crossing (legacy behaviour).
6. **Contours**: per level, crossing points on edges (linear interp), segments per triangle,
   `linemerge` → polylines, closed‑ring detection; levels nudged by ε instead of mutating node Z;
   major flag when `level % major == 0`; optional Chaikin smoothing for display; arc‑bulge smoothing
   only in DXF export; label placement every N metres along each contour (text = format string,
   e.g. `{z:.2f}`, prefix/suffix).
7. **Alignment**: `HorizontalAlignment(ips, start_ch)`: WCB, deflection, `T = R·tan(Δ/2)`, `L = R·Δ`,
   BC/EC chainages, curve centres, `point_at(ch)`, `bearing_at(ch)`, `offset_point(ch, d)`; validity
   (tangent overlap, R ≥ min); densify arcs to chords for display; export bulges `tan(Δ/4)`.
8. **Sections**: chainage list (interval + BC/MC/EC + user), left/right corners, sample TIN, insert
   exact centre point, mark points outside the TIN as `null` (not 0), legacy CSV writers.
9. **DXF export** with legacy layer names and block definition.

---

## 7. Delivery phases

| Phase | Weeks (est.) | Deliverable | Acceptance |
|---|---|---|---|
| 0 Setup | 1 | Repo layout, CI (pytest, vitest, ruff, eslint), cPanel smoke deploy (**deferred by owner**) | – |
| 1 Engine core | **done 2026‑09‑19** | `plm/engine`: importers (CSV/GeoJSON/DXF), dedupe, CDT, boundary clip, topology, sampling, contours, labels, DXF export; CLI `plm tin points.csv --interval 1 --dxf out.dxf`; 29 pytest cases on analytic surfaces | 100k points: TIN 1.4 s, contours 2 s |
| 2 API + storage | **done 2026‑09‑19** | FastAPI, SQLite app db, GeoPackage per project, import/TIN/contours/alignments/sections/export endpoints, jobs + cron worker, auth (register/login), members, comments, activity, alignment locks + versions | 7 API test scenarios incl. two-user collaboration |
| 3 Viewer | **done 2026‑09‑19** | Vite+TS+Cesium: OSM/OpenTopo/Carto/Esri base maps, points, shaded TIN, styled contours + labels, 2D/3D/2.5D, layer toggles, CRS settings | Playwright smoke test on local-grid and MUTM 84 demo projects |
| 4 Alignment & sections | **done 2026‑09‑19** | IP drawing/dragging, radii with live geometry, chainage labels, versions + editing turns, profile + cross-sections, split-screen D3 charts with hover sync, legacy CSV export | verified in browser smoke test |
| 5 Export & polish | **done (LandXML deferred)** | DXF with alignment/chainage/sections, GeoPackage, GeoJSON, CSV; auth; project sharing; deployment docs (`docs/DEPLOYMENT.md`) | ezdxf audit clean; open in AutoCAD to confirm |
| 6 (v2) | – | Profile/cross‑section **sheets** (legacy `Draw_cs`/`Profile.exe` layout) as DXF/PDF; Plan Data Entry; remark‑code details; XLSX round trips | |

---

## 8. Testing without legacy data

* **Synthetic corpus** generated by a script: regular and random points on analytic surfaces
  (plane `z = ax + by + c`, paraboloid, ridge) with known contours; feature lines that must appear
  as TIN edges; boundary polygons; an alignment with straights and curves whose profile is known
  analytically on the plane surface.
* **Real corpus**: one or two current SSTN surveys (CSV export from the total station / GNSS) once
  available; keep them under `tests/data/real/` (git‑lfs or excluded from repo if confidential).


---

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Triangle licence for a hosted commercial service | `Triangulator` interface; PythonCDT adapter ready; ask author or use CDT if SSTN sells access |
| cPanel: no GDAL, WSGI only, request time limits | GDAL optional; a2wsgi; job worker via cron; size limits on inline jobs |
| MUTM datum shift parameters | make them editable; validate against a known control point |
| Cesium performance with large TINs | send indexed binary mesh; LOD by decimation for > 500k triangles; contours as GeoJSON per level, lazily |
| AutoCAD compatibility | export DXF R2010 via ezdxf; test in AutoCAD LT / TrueView / LibreCAD |
