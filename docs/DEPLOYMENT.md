# Picasso LandMesh — Deployment Guide

PLM is a browser application: people open a URL, sign in, and work in the map. The server runs a
FastAPI (Python) backend; every project is a GeoPackage on disk and the accounts, jobs, visitors and
history are in one SQLite file. Nothing is installed on the engineers' machines, and no database
server, GDAL or Cesium Ion token is required.

---

## 1. Run it locally

```powershell
cd plm
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev,server,local]"
cd web && npm install && npm run build && cd ..
.venv\Scripts\python -m plm.serve            # http://127.0.0.1:8000  (API docs at /api/docs)
```

Demo data (two surveys, alignment, contours, sections):

```powershell
.venv\Scripts\python examples\make_demo.py --load http://127.0.0.1:8000
```

Sign-in is off locally. Turn it on with `python -m plm.serve --auth on`; the first person to
register becomes the administrator.

---

## 2. Configuration

Everything is an environment variable, read once at start-up. On cPanel they go in the **Setup
Python App** screen; `deploy/plm.env.example` is the same list ready to copy, already set for the
production profile described in §3.

### Storage

| Variable | Default | Purpose |
|---|---|---|
| `PLM_DATA_DIR` | `./data` | SQLite database, per-project GeoPackages, uploads, archives. **Keep it outside `public_html`.** |
| `PLM_WEB_DIST` | `web/dist` if present | the built front end that the API serves. |
| `PLM_MAX_UPLOAD_MB` | `200` | rejected above this. |

### Accounts

| Variable | Default | Purpose |
|---|---|---|
| `PLM_AUTH_ENABLED` | `0` | **`1` in production.** Cookie sessions (HMAC-signed), PBKDF2 passwords. |
| `PLM_OPEN_REGISTRATION` | `0` | `1` lets anyone register. The very first account is always allowed, so that an administrator can exist. |
| `PLM_SECRET_KEY` | generated into `PLM_DATA_DIR/.secret_key` | session signing key. Set it by hand only if two servers share one database. |
| `PLM_SESSION_HOURS` | `72` | session lifetime. |
| `PLM_LOCK_MINUTES` | `15` | how long an alignment editing turn is held. |

### Guest sandbox — try before signing up

| Variable | Default | Purpose |
|---|---|---|
| `PLM_GUEST_ENABLED` | `1` | a private sandbox for people without an account. Signing in later claims their work. |
| `PLM_GUEST_MAX_POINTS` | `5000` | per project. |
| `PLM_GUEST_MAX_PROJECTS` | `2` | per guest. |
| `PLM_GUEST_MAX_TIN_RUNS` | `3` | per project. |
| `PLM_GUEST_TTL_DAYS` | `7` | sandbox projects are deleted after this. |

Guests never export and their jobs queue behind accounts.

### Visitor register — who is using the server

| Variable | Default | Purpose |
|---|---|---|
| `PLM_VISITOR_TRACKING` | `1` | recognise each person by cookie, and failing that by the address they connect from; log the major actions. |
| `PLM_VISITOR_INTAKE` | `1` | offer the introduction form (name, email, phone, designation, organisation, district). |
| `PLM_VISITOR_INTAKE_REQUIRED` | `0` | `1` makes it compulsory: major actions answer `428` until the form is filled. |
| `PLM_VISITOR_INTAKE_REPEAT_DAYS` | `7` | how long "Not now" is honoured. |
| `PLM_VISITOR_COOKIE_DAYS` | `365` | how long a visitor is remembered by cookie. |
| `PLM_VISITOR_VISIT_MINUTES` | `30` | idle gap that counts as a new visit. |
| `PLM_TRUST_PROXY` | `1` | take the address from `X-Forwarded-For`. **Required behind Apache/cPanel**, otherwise every visitor looks like the proxy. Set `0` only when the server faces the internet directly. |

### Load and jobs

| Variable | Default | Purpose |
|---|---|---|
| `PLM_JOB_MODE` | `inline` | **`worker` on shared hosting**: the web process only queues, and the cron job executes. |
| `PLM_SYNC_POINT_LIMIT` | `250000` | above this a triangulation becomes a background job. Lower it on a small plan. |
| `PLM_MAX_HEAVY` | `2` | heavy synchronous requests at once; beyond it the server answers `503 Retry-After` and the browser retries. |
| `PLM_BUSY_QUEUE` | `3` | pending jobs before the UI shows the "server is busy" banner. |
| `PLM_TIN_CACHE` | `2` | triangulations held in memory per process. |
| `PLM_TILE_TRIANGLES` | `100000` | triangles per mesh tile sent to the browser. |
| `PLM_CORS_ORIGINS` | `*` | set it to your own origin when the front end is served from the same host. |

Command line: `python -m plm.admin create-user <name> --role admin|editor|viewer`, `set-password`,
`list-users`, `list-projects`, `backup`, `flatten-constraints` (drop the levels from stored boundary
and void rings - they are plan geometry and the engine interpolates a vertex level from the survey).

---

## 3. cPanel (Apache + Phusion Passenger)

Passenger runs WSGI; FastAPI is ASGI, so `passenger_wsgi.py` wraps it with `a2wsgi`. No WebSockets
are used — the browser polls for activity — so nothing else is needed.

### 3.1 Build and upload

On your own machine:

```powershell
cd web && npm run build && cd ..          # the front end must be built before the bundle
.venv\Scripts\python deploy\make_bundle.py
```

That writes `dist/plm-deploy-<date>.zip` (about 11 MB: the Python package, the built front end, the
tests, the docs and these deployment helpers — no virtualenv, no `node_modules`, no data).

Upload the zip to your home directory with cPanel → **File Manager**, then in cPanel → **Terminal**:

```bash
cd ~ && unzip -o plm-deploy-<date>.zip     # unpacks into /home/USER/plm
```

### 3.2 Create the Python application

cPanel → **Setup Python App** → Create:

| Field | Value |
|---|---|
| Python version | 3.12 (3.11 is equally fine) |
| Application root | `plm` |
| Application URL | the domain or subdomain, at its root |
| Application startup file | `passenger_wsgi.py` |
| Application Entry point | `application` |

Then add the environment variables from `deploy/plm.env.example`, replacing `USER` with your cPanel
username and the domain with your own. At minimum:

```
PLM_DATA_DIR=/home/USER/plm_data
PLM_WEB_DIST=/home/USER/plm/web/dist
PLM_AUTH_ENABLED=1
PLM_OPEN_REGISTRATION=1        (set to 0 once you have registered yourself)
PLM_JOB_MODE=worker
PLM_TRUST_PROXY=1
PLM_CORS_ORIGINS=https://your-domain
```

Keep `PLM_DATA_DIR` **outside** `public_html`: it holds the survey data and the password hashes, and
anything under `public_html` is downloadable.

### 3.3 Install

Copy the `source .../activate` line that the app screen shows, run it in Terminal, then:

```bash
cd /home/USER/plm
bash deploy/install.sh
```

It installs the dependencies as wheels (no compiler needed), creates `plm_data` with tight
permissions, runs the test suite once so you know the geometry works on that machine, imports the
Passenger entry point, and prints the cron line with the real interpreter path filled in.

### 3.4 Start and prove it

1. **Setup Python App → Restart.**
2. From your own machine:
   ```powershell
   .venv\Scripts\python deploy\check_live.py https://your-domain
   ```
   Seven checks: the API, the application shell and its assets, the help pages, the visitor register
   (it also warns if `PLM_TRUST_PROXY` is wrong and every visitor looks like the proxy), the account
   settings, project access, and that pyproj's data files really loaded on that machine.
3. Open `https://your-domain/`, fill the visitor form and **register the first account** — it becomes
   the administrator.
4. Go back to the Python App screen, set `PLM_OPEN_REGISTRATION=0`, **Restart**. From now on you
   create every account from the **Admin** page.

### 3.4a One command instead of §3.1–3.4

Once the Python App exists and an SSH key is authorised (cPanel → **SSH Access** → Manage SSH Keys →
Import → Authorize), the whole of the above is one command from your own machine:

```bash
cd web && npm run build && cd ..
bash deploy/remote_deploy.sh --host server.example.com --user cpaneluser                              --domain https://plm.example.com --key ~/.ssh/plm_cpanel
```

It builds the bundle, checks the connection and finds the application virtualenv (stopping before it
changes anything if the Python App has not been created yet), uploads, unpacks, installs, restarts
Passenger by touching `tmp/restart.txt`, and finally runs `check_live.py` against the domain. Run it
again for every upgrade — it replaces the application files and never touches the data directory.

### 3.5 The job worker (required when `PLM_JOB_MODE=worker`)

Passenger recycles the request process, so long triangulations must not run inside it. cPanel →
**Cron Jobs**, every minute (`* * * * *`), using the path `install.sh` printed:

```
cd /home/USER/plm && /home/USER/virtualenv/plm/3.12/bin/python -m plm.worker >> /home/USER/plm_data/worker.log 2>&1
```

Without it, anything above `PLM_SYNC_POINT_LIMIT` points will sit in the queue for ever.

### 3.6 Optional: let Apache serve the static files

Lower latency on the 18 MB of Cesium assets. Copy `web/dist` into `public_html` together with
`web/.htaccess` (it already routes `/api` to Passenger and everything else to `index.html`), and
mount the Python app at `/api` instead of `/`.

---

## 4. Data and backups

* One GeoPackage per project: `PLM_DATA_DIR/projects/<id>/project.gpkg` — open it directly in QGIS.
* `PLM_DATA_DIR/plm.db`: accounts, projects, jobs, comments, activity, locks, alignment history,
  the catalogue, the visitor register and the usage log.
* `python -m plm.admin backup --out /home/USER/backups` writes a consistent copy of both while the
  application keeps running. Put it on a weekly cron job and download it.
* Owners can also archive a single project to a portable zip from inside the application.

---

## 5. Upgrading

1. Build and upload a new bundle (`npm run build`, `make_bundle.py`, unzip over `/home/USER/plm`).
2. `source .../activate && cd /home/USER/plm && pip install -e ".[server]"`.
3. **Restart** the Python app, then `python deploy/check_live.py https://your-domain`.

The data directory is never touched by an upgrade. New tables and columns are created on start-up
(`CREATE TABLE IF NOT EXISTS` plus explicit column migrations), so no manual database work is needed.

---

## 6. Coordinate systems

The default is the legacy **local grid** (plain metres, no map overlay). Assign MUTM 81/84/87, UTM
44N/45N, WGS84 or any EPSG/PROJ string per project in Settings to enable the base-map overlay. The
MUTM→WGS84 datum shift is the commonly published approximation — confirm it against a known control
point before relying on sub-metre map alignment.

---

## 7. If something is wrong

| Symptom | Cause |
|---|---|
| 500 on every page, "Application failed to start" | read `~/plm/stderr.log` or the Passenger log the app screen links to; usually a missing dependency — re-run `deploy/install.sh`. |
| `/api/health` works but `/` is blank | `PLM_WEB_DIST` is unset or points at the wrong place. |
| Every visitor shows the same address in the register | `PLM_TRUST_PROXY=0`, or a proxy that does not set `X-Forwarded-For`. |
| Big triangulations never finish | the cron worker of §3.5 is missing, or `PLM_JOB_MODE` is not `worker`. |
| "server is busy" all the time | `PLM_MAX_HEAVY` is too low for the plan, or one job is stuck — check `worker.log`. |
| Uploads fail at a certain size | `PLM_MAX_UPLOAD_MB`, and the hosting's own `LimitRequestBody` / PHP-style caps. |
