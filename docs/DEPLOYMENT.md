# Picasso LandMesh (PLM) – Deployment Guide

PLM is a browser application: users open a URL, sign in, and work in the CesiumJS map. The
server runs a FastAPI (Python) backend with SQLite/GeoPackage storage. Nothing is installed on
user machines.

## 1. Run locally (Windows / Linux / macOS)

```powershell
cd plm
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]" fastapi "uvicorn[standard]" python-multipart a2wsgi openpyxl
cd web && npm install && npm run build && cd ..
.venv\Scripts\python -m plm.serve            # http://127.0.0.1:8000  (API docs at /api/docs)
```

Demo data (two surveys, alignment, contours, sections):

```powershell
.venv\Scripts\python examples\make_demo.py --load http://127.0.0.1:8000
```

Authentication is off by default in development. Turn it on with `PLM_AUTH_ENABLED=1`
(or `python -m plm.serve --auth on`); the first person to register becomes the admin.

## 2. Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `PLM_DATA_DIR` | `./data` | SQLite app database, per-project GeoPackages, uploads. **Keep outside `public_html`.** |
| `PLM_AUTH_ENABLED` | `0` | `1` in production. Cookie sessions (HMAC-signed), PBKDF2 passwords. |
| `PLM_OPEN_REGISTRATION` | `1` | allow self-registration after the first admin (set `0` and create users with `python -m plm.admin`). |
| `PLM_SECRET_KEY` | auto-generated in data dir | session signing key. |
| `PLM_SESSION_HOURS` | `72` | session lifetime. |
| `PLM_LOCK_MINUTES` | `15` | alignment editing-turn lock duration (renewed on save). |
| `PLM_SYNC_POINT_LIMIT` | `250000` | larger triangulations run as background jobs. |
| `PLM_MAX_UPLOAD_MB` | `200` | upload size limit. |
| `PLM_WEB_DIST` | `web/dist` if present | built front end served by FastAPI. |
| `PLM_CORS_ORIGINS` | `*` | comma-separated origins if the front end is served from another host. |

Admin CLI: `python -m plm.admin create-user <name> --role admin|editor|viewer`, `set-password`,
`list-users`, `list-projects`.

## 3. cPanel (Apache + Passenger) deployment

1. **Upload** the `plm/` folder (without `.venv`, `node_modules`, `data`) to e.g. `/home/USER/plm`.
   Build the front end locally first (`npm run build`) and upload `web/dist` too.
2. cPanel → **Setup Python App**: Python 3.11 or 3.12, application root `plm`, application URL `/`
   (or a sub-path), application startup file `passenger_wsgi.py`, entry point `application`.
3. In the app screen add environment variables: `PLM_DATA_DIR=/home/USER/plm_data`,
   `PLM_AUTH_ENABLED=1`, `PLM_OPEN_REGISTRATION=1` (switch to 0 after the team has registered),
   `PLM_WEB_DIST=/home/USER/plm/web/dist`.
4. Open the app's virtualenv shell (command shown in cPanel) and install:
   `pip install -e . fastapi a2wsgi python-multipart openpyxl`
   (numpy, scipy, shapely, pyproj, triangle, ezdxf come as wheels; no GDAL required).
5. **Restart** the app. Visit `https://your-domain/api/health` → `{"status":"ok"}` and
   `https://your-domain/` → the login screen. Register the first (admin) user.
6. **Cron** (cPanel → Cron Jobs), every minute, so large background jobs finish even if Passenger
   recycles the request process:
   `cd /home/USER/plm && /home/USER/virtualenv/plm/3.11/bin/python -m plm.worker`
7. Optional: serve `web/dist` directly from Apache (copy to `public_html` with the provided
   `web/.htaccess`) and mount the Python app at `/api` for lower latency on static files.

Notes for shared hosting: Passenger is WSGI-only, so FastAPI is wrapped with `a2wsgi`
(no WebSockets are used; the browser polls `/activity` every 6 s). Cesium loads base-map tiles
directly from OpenStreetMap / OpenTopoMap; no Cesium Ion token is needed.

## 4. Data & backups

* One GeoPackage per project in `PLM_DATA_DIR/projects/<id>/project.gpkg` – open it in QGIS.
* `PLM_DATA_DIR/plm.db` holds users, projects, jobs, comments, activity, locks, alignment history.
* Back up `PLM_DATA_DIR` (both files are SQLite; copy while the app is idle or use `sqlite3 .backup`).

## 5. Upgrading

`git pull` (or re-upload), `pip install -e .`, rebuild `web/dist`, restart the Python app.
Database schemas are created with `CREATE TABLE IF NOT EXISTS`; new tables appear automatically.

## 6. Coordinate systems

Default is the legacy **local grid** (plain metres, no map overlay). Assign MUTM 81/84/87,
UTM 44N/45N, WGS84 or any EPSG/PROJ string per project in Settings to enable the base-map overlay.
The MUTM→WGS84 datum shift is the commonly published approximation – confirm it with a known
control point before relying on sub-metre map alignment.
