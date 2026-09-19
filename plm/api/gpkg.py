"""Per-project GeoPackage store (SQLite + GeoPackage binary geometry, written with the standard
library so no GDAL is needed). QGIS and other GIS tools can open the file directly.

Feature tables (with geometry): points, lines, contours, alignment_geom, section_lines
Attribute tables: tin_runs, contour_sets, alignments, section_sets, meta
"""
from __future__ import annotations

import json
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import shapely

from ..engine.contour import ContourLine
from ..engine.points import PointSet
from ..engine.tin import TIN, TinIssue
from ..engine import crs as crsmod

SCHEMA_VERSION = 1
CUSTOM_SRS_ID = 100001


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ------------------------------------------------------------------------- geometry blobs
def gpkg_blob(geom, srs_id: int) -> bytes:
    """StandardGeoPackageBinary: 'GP' + version + flags (LE, no envelope) + srs_id + ISO WKB."""
    wkb = shapely.to_wkb(geom, output_dimension=3, byte_order=1, flavor="iso")
    return b"GP" + bytes([0, 0b00000001]) + struct.pack("<i", srs_id) + wkb


def from_gpkg_blob(blob: bytes):
    flags = blob[3]
    env = (flags >> 1) & 0b111
    env_len = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(env, 0)
    return shapely.from_wkb(blob[8 + env_len:])


_BASE_SQL = """
PRAGMA application_id = 0x47504B47;
PRAGMA user_version = 10300;
CREATE TABLE IF NOT EXISTS gpkg_spatial_ref_sys (
  srs_name TEXT NOT NULL, srs_id INTEGER NOT NULL PRIMARY KEY, organization TEXT NOT NULL,
  organization_coordsys_id INTEGER NOT NULL, definition TEXT NOT NULL, description TEXT);
CREATE TABLE IF NOT EXISTS gpkg_contents (
  table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL, identifier TEXT UNIQUE,
  description TEXT DEFAULT '', last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE, srs_id INTEGER,
  CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id));
CREATE TABLE IF NOT EXISTS gpkg_geometry_columns (
  table_name TEXT NOT NULL, column_name TEXT NOT NULL, geometry_type_name TEXT NOT NULL,
  srs_id INTEGER NOT NULL, z TINYINT NOT NULL, m TINYINT NOT NULL,
  CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
  CONSTRAINT uk_gc_table_name UNIQUE (table_name),
  CONSTRAINT fk_gc_tn FOREIGN KEY (table_name) REFERENCES gpkg_contents(table_name),
  CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys (srs_id));
INSERT OR IGNORE INTO gpkg_spatial_ref_sys VALUES ('Undefined cartesian SRS', -1, 'NONE', -1, 'undefined', 'undefined cartesian coordinate reference system');
INSERT OR IGNORE INTO gpkg_spatial_ref_sys VALUES ('Undefined geographic SRS', 0, 'NONE', 0, 'undefined', 'undefined geographic coordinate reference system');
INSERT OR IGNORE INTO gpkg_spatial_ref_sys VALUES ('WGS 84 geodetic', 4326, 'EPSG', 4326,
  'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]]',
  'longitude/latitude coordinates in decimal degrees on the WGS 84 spheroid');

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS points (
  fid INTEGER PRIMARY KEY AUTOINCREMENT, geom BLOB, pt_no TEXT DEFAULT '', x REAL NOT NULL, y REAL NOT NULL,
  z REAL NOT NULL, remark TEXT DEFAULT '', layer TEXT DEFAULT '', source TEXT DEFAULT '', created TEXT);
CREATE INDEX IF NOT EXISTS idx_points_layer ON points(layer);

CREATE TABLE IF NOT EXISTS lines (
  fid INTEGER PRIMARY KEY AUTOINCREMENT, geom BLOB, kind TEXT NOT NULL, layer TEXT DEFAULT '',
  name TEXT DEFAULT '', closed INTEGER DEFAULT 0, n_vertices INTEGER, source TEXT DEFAULT '', created TEXT);
CREATE INDEX IF NOT EXISTS idx_lines_kind ON lines(kind);

CREATE TABLE IF NOT EXISTS tin_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, name TEXT DEFAULT '', params TEXT, stats TEXT,
  n_nodes INTEGER, n_triangles INTEGER, nodes BLOB, triangles BLOB, node_source BLOB, issues TEXT,
  min_x REAL, min_y REAL, max_x REAL, max_y REAL, min_z REAL, max_z REAL);

CREATE TABLE IF NOT EXISTS contour_sets (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, created TEXT, name TEXT DEFAULT '',
  params TEXT, style TEXT, n_lines INTEGER, levels TEXT);
CREATE TABLE IF NOT EXISTS contours (
  fid INTEGER PRIMARY KEY AUTOINCREMENT, geom BLOB, set_id INTEGER NOT NULL, level REAL NOT NULL,
  major INTEGER NOT NULL, closed INTEGER NOT NULL, length REAL, n_vertices INTEGER);
CREATE INDEX IF NOT EXISTS idx_contours_set ON contours(set_id);

CREATE TABLE IF NOT EXISTS alignments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, start_chainage REAL DEFAULT 0, ips TEXT NOT NULL,
  style TEXT DEFAULT '{}', length REAL, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS alignment_geom (
  fid INTEGER PRIMARY KEY AUTOINCREMENT, geom BLOB, alignment_id INTEGER NOT NULL, name TEXT, start_chainage REAL, end_chainage REAL);

CREATE TABLE IF NOT EXISTS section_sets (
  id INTEGER PRIMARY KEY AUTOINCREMENT, alignment_id INTEGER NOT NULL, run_id INTEGER NOT NULL, created TEXT,
  name TEXT DEFAULT '', params TEXT, profile TEXT, sections TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS section_lines (
  fid INTEGER PRIMARY KEY AUTOINCREMENT, geom BLOB, set_id INTEGER NOT NULL, chainage REAL, label TEXT);
CREATE INDEX IF NOT EXISTS idx_section_lines_set ON section_lines(set_id);
"""

_FEATURE_TABLES = {
    "points": ("POINT", "Survey points"),
    "lines": ("LINESTRING", "Feature lines, boundaries, voids, digitised contours"),
    "contours": ("LINESTRING", "Generated contours"),
    "alignment_geom": ("LINESTRING", "Horizontal alignments (densified)"),
    "section_lines": ("LINESTRING", "Cross-section lines"),
}
_ATTR_TABLES = ("tin_runs", "contour_sets", "alignments", "section_sets")


class ProjectStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.srs_id = -1

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, path: str | Path, crs_spec: str | None = None, name: str = "") -> "ProjectStore":
        st = cls(path)
        st.path.parent.mkdir(parents=True, exist_ok=True)
        with st._connect() as c:
            c.executescript(_BASE_SQL)
            st._set_crs(c, crs_spec)
            for t, (gtype, desc) in _FEATURE_TABLES.items():
                c.execute(
                    "INSERT OR REPLACE INTO gpkg_contents(table_name, data_type, identifier, description, srs_id) VALUES (?,?,?,?,?)",
                    (t, "features", t, desc, st.srs_id),
                )
                c.execute(
                    "INSERT OR REPLACE INTO gpkg_geometry_columns VALUES (?,?,?,?,?,?)",
                    (t, "geom", gtype, st.srs_id, 1, 0),
                )
            for t in _ATTR_TABLES:
                c.execute(
                    "INSERT OR REPLACE INTO gpkg_contents(table_name, data_type, identifier, description, srs_id) VALUES (?,?,?,?,?)",
                    (t, "attributes", t, "", None),
                )
            c.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            c.execute("INSERT OR REPLACE INTO meta VALUES ('name', ?)", (name,))
            c.execute("INSERT OR REPLACE INTO meta VALUES ('crs', ?)", (crs_spec or "local",))
        return st

    @classmethod
    def open(cls, path: str | Path) -> "ProjectStore":
        st = cls(path)
        if not st.path.exists():
            raise FileNotFoundError(path)
        with st._connect() as c:
            r = c.execute("SELECT srs_id FROM gpkg_geometry_columns WHERE table_name='points'").fetchone()
            st.srs_id = int(r[0]) if r else -1
        return st

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def _set_crs(self, c: sqlite3.Connection, spec: str | None) -> None:
        crs = crsmod.resolve(spec)
        if crs is None:
            self.srs_id = -1
            return
        epsg = crs.to_epsg()
        if epsg:
            self.srs_id = int(epsg)
            c.execute(
                "INSERT OR IGNORE INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
                (crs.name, epsg, "EPSG", epsg, crs.to_wkt(), spec or ""),
            )
        else:
            self.srs_id = CUSTOM_SRS_ID
            c.execute(
                "INSERT OR REPLACE INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
                (crs.name, CUSTOM_SRS_ID, "PLM", CUSTOM_SRS_ID, crs.to_wkt(), spec or ""),
            )

    def set_crs(self, spec: str | None) -> None:
        """Change the declared CRS (coordinates are NOT transformed)."""
        with self._connect() as c:
            self._set_crs(c, spec)
            c.execute("UPDATE gpkg_geometry_columns SET srs_id=?", (self.srs_id,))
            c.execute("UPDATE gpkg_contents SET srs_id=? WHERE data_type='features'", (self.srs_id,))
            c.execute("INSERT OR REPLACE INTO meta VALUES ('crs', ?)", (spec or "local",))

    def meta(self, key: str) -> str | None:
        with self._connect() as c:
            r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def _bump_extent(self, c: sqlite3.Connection, table: str, xs, ys) -> None:
        if len(xs) == 0:
            return
        mnx, mxx, mny, mxy = float(np.min(xs)), float(np.max(xs)), float(np.min(ys)), float(np.max(ys))
        r = c.execute("SELECT min_x, min_y, max_x, max_y FROM gpkg_contents WHERE table_name=?", (table,)).fetchone()
        if r and r[0] is not None:
            mnx, mny = min(mnx, r[0]), min(mny, r[1])
            mxx, mxy = max(mxx, r[2]), max(mxy, r[3])
        c.execute(
            "UPDATE gpkg_contents SET min_x=?, min_y=?, max_x=?, max_y=?, last_change=? WHERE table_name=?",
            (mnx, mny, mxx, mxy, _now(), table),
        )

    def _reset_extent(self, c: sqlite3.Connection, table: str) -> None:
        c.execute("UPDATE gpkg_contents SET min_x=NULL, min_y=NULL, max_x=NULL, max_y=NULL WHERE table_name=?", (table,))

    # ------------------------------------------------------------------ points
    def add_points(self, ps: PointSet, source: str = "") -> int:
        if not len(ps):
            return 0
        ts = _now()
        pts = shapely.points(ps.xyz[:, 0], ps.xyz[:, 1], ps.xyz[:, 2])
        rows = [
            (gpkg_blob(pts[i], self.srs_id), ps.ids[i], float(ps.x[i]), float(ps.y[i]), float(ps.z[i]),
             ps.remarks[i], ps.layers[i], source, ts)
            for i in range(len(ps))
        ]
        with self._connect() as c:
            c.executemany(
                "INSERT INTO points(geom, pt_no, x, y, z, remark, layer, source, created) VALUES (?,?,?,?,?,?,?,?,?)", rows
            )
            self._bump_extent(c, "points", ps.x, ps.y)
        return len(rows)

    def points(self, layers: Iterable[str] | None = None, with_fid: bool = False) -> PointSet | tuple[PointSet, np.ndarray]:
        q = "SELECT fid, pt_no, x, y, z, remark, layer FROM points"
        vals: list[Any] = []
        if layers:
            lays = list(layers)
            q += f" WHERE layer IN ({','.join('?' * len(lays))})"
            vals = lays
        q += " ORDER BY fid"
        with self._connect() as c:
            rows = c.execute(q, vals).fetchall()
        if not rows:
            ps = PointSet.empty()
            return (ps, np.zeros(0, int)) if with_fid else ps
        arr = np.array([[r["x"], r["y"], r["z"]] for r in rows], dtype=float)
        ps = PointSet(arr, [r["pt_no"] or "" for r in rows], [r["remark"] or "" for r in rows], [r["layer"] or "" for r in rows])
        if with_fid:
            return ps, np.array([r["fid"] for r in rows], dtype=int)
        return ps

    def point_layers(self) -> list[dict]:
        with self._connect() as c:
            return [dict(r) for r in c.execute("SELECT layer, COUNT(*) AS n, MIN(z) AS zmin, MAX(z) AS zmax FROM points GROUP BY layer ORDER BY layer")]

    def count_points(self) -> int:
        with self._connect() as c:
            return int(c.execute("SELECT COUNT(*) FROM points").fetchone()[0])

    def delete_points(self, fids: Iterable[int] | None = None, layer: str | None = None) -> int:
        with self._connect() as c:
            if fids is not None:
                ids = list(fids)
                cur = c.execute(f"DELETE FROM points WHERE fid IN ({','.join('?' * len(ids))})", ids) if ids else None
                n = cur.rowcount if cur else 0
            elif layer is not None:
                n = c.execute("DELETE FROM points WHERE layer=?", (layer,)).rowcount
            else:
                n = c.execute("DELETE FROM points").rowcount
            self._reset_extent(c, "points")
            r = c.execute("SELECT MIN(x), MIN(y), MAX(x), MAX(y) FROM points").fetchone()
            if r[0] is not None:
                c.execute("UPDATE gpkg_contents SET min_x=?, min_y=?, max_x=?, max_y=? WHERE table_name='points'", tuple(r))
        return int(n)

    # ------------------------------------------------------------------ lines
    def add_lines(self, lines: Sequence[np.ndarray], kind: str, layer: str = "", source: str = "",
                  names: Sequence[str] | None = None, closed: Sequence[bool] | None = None) -> int:
        rows = []
        ts = _now()
        xs, ys = [], []
        for i, c in enumerate(lines):
            c = np.asarray(c, dtype=float)
            if c.ndim != 2 or len(c) < 2:
                continue
            if c.shape[1] == 2:
                c = np.column_stack([c, np.zeros(len(c))])
            geom = shapely.LineString(c[:, :3])
            is_closed = bool(closed[i]) if closed is not None else bool(np.allclose(c[0, :2], c[-1, :2]))
            rows.append((gpkg_blob(geom, self.srs_id), kind, layer, (names[i] if names else ""), int(is_closed), len(c), source, ts))
            xs.append(c[:, 0])
            ys.append(c[:, 1])
        if not rows:
            return 0
        with self._connect() as c:
            c.executemany(
                "INSERT INTO lines(geom, kind, layer, name, closed, n_vertices, source, created) VALUES (?,?,?,?,?,?,?,?)", rows
            )
            self._bump_extent(c, "lines", np.concatenate(xs), np.concatenate(ys))
        return len(rows)

    def lines(self, kind: str | None = None, layers: Iterable[str] | None = None) -> list[dict]:
        q = "SELECT fid, geom, kind, layer, name, closed, n_vertices FROM lines"
        cond, vals = [], []
        if kind:
            cond.append("kind=?")
            vals.append(kind)
        if layers:
            lays = list(layers)
            cond.append(f"layer IN ({','.join('?' * len(lays))})")
            vals.extend(lays)
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY fid"
        out = []
        with self._connect() as c:
            for r in c.execute(q, vals):
                g = from_gpkg_blob(r["geom"])
                coords = shapely.get_coordinates(g, include_z=True)
                out.append({"fid": r["fid"], "coords": coords, "kind": r["kind"], "layer": r["layer"] or "",
                            "name": r["name"] or "", "closed": bool(r["closed"])})
        return out

    def line_summary(self) -> list[dict]:
        with self._connect() as c:
            return [dict(r) for r in c.execute("SELECT kind, layer, COUNT(*) AS n, SUM(n_vertices) AS vertices FROM lines GROUP BY kind, layer ORDER BY kind, layer")]

    def delete_lines(self, fids: Iterable[int] | None = None, kind: str | None = None) -> int:
        with self._connect() as c:
            if fids is not None:
                ids = list(fids)
                n = c.execute(f"DELETE FROM lines WHERE fid IN ({','.join('?' * len(ids))})", ids).rowcount if ids else 0
            elif kind:
                n = c.execute("DELETE FROM lines WHERE kind=?", (kind,)).rowcount
            else:
                n = c.execute("DELETE FROM lines").rowcount
        return int(n)

    def update_line(self, fid: int, kind: str | None = None, layer: str | None = None, name: str | None = None) -> None:
        sets, vals = [], []
        for k, v in (("kind", kind), ("layer", layer), ("name", name)):
            if v is not None:
                sets.append(f"{k}=?")
                vals.append(v)
        if sets:
            vals.append(fid)
            with self._connect() as c:
                c.execute(f"UPDATE lines SET {', '.join(sets)} WHERE fid=?", vals)

    # ------------------------------------------------------------------ TIN runs
    def save_tin(self, tin: TIN, params: dict, stats: dict, issues: Sequence[TinIssue] = (),
                 node_source: np.ndarray | None = None, name: str = "") -> int:
        nodes = np.ascontiguousarray(tin.nodes, dtype=np.float64).tobytes()
        tris = np.ascontiguousarray(tin.triangles, dtype=np.int32).tobytes()
        src = np.ascontiguousarray(node_source, dtype=np.int8).tobytes() if node_source is not None else None
        iss = json.dumps([{"kind": i.kind, "x": i.x, "y": i.y, "message": i.message} for i in issues])
        zmin, zmax = tin.z_range()
        b = tin.bounds()
        with self._connect() as c:
            cur = c.execute(
                "INSERT INTO tin_runs(created, name, params, stats, n_nodes, n_triangles, nodes, triangles, node_source, issues, min_x, min_y, max_x, max_y, min_z, max_z) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), name, json.dumps(params), json.dumps(stats), tin.n_nodes, tin.n_triangles, nodes, tris, src, iss,
                 b[0], b[1], b[2], b[3], zmin, zmax),
            )
            return int(cur.lastrowid)

    def tin_runs(self) -> list[dict]:
        with self._connect() as c:
            rows = c.execute("SELECT id, created, name, params, stats, n_nodes, n_triangles, min_x, min_y, max_x, max_y, min_z, max_z, issues FROM tin_runs ORDER BY id").fetchall()
        return [self._run_row(r) for r in rows]

    @staticmethod
    def _run_row(r) -> dict:
        d = dict(r)
        d["params"] = json.loads(d.get("params") or "{}")
        d["stats"] = json.loads(d.get("stats") or "{}")
        iss = json.loads(d.pop("issues") or "[]")
        d["issues_count"] = len(iss)
        d["issues"] = iss
        d["bounds"] = [d.pop("min_x"), d.pop("min_y"), d.pop("max_x"), d.pop("max_y")]
        d["z_range"] = [d.pop("min_z"), d.pop("max_z")]
        return d

    def get_tin_run(self, run_id: int) -> dict | None:
        with self._connect() as c:
            r = c.execute("SELECT id, created, name, params, stats, n_nodes, n_triangles, min_x, min_y, max_x, max_y, min_z, max_z, issues FROM tin_runs WHERE id=?", (run_id,)).fetchone()
        return self._run_row(r) if r else None

    def latest_tin_run_id(self) -> int | None:
        with self._connect() as c:
            r = c.execute("SELECT MAX(id) FROM tin_runs").fetchone()
        return int(r[0]) if r and r[0] is not None else None

    def load_tin(self, run_id: int) -> tuple[TIN, np.ndarray | None]:
        with self._connect() as c:
            r = c.execute("SELECT nodes, triangles, node_source, n_nodes, n_triangles FROM tin_runs WHERE id=?", (run_id,)).fetchone()
        if not r:
            raise KeyError(f"tin run {run_id} not found")
        nodes = np.frombuffer(r["nodes"], dtype=np.float64).reshape(r["n_nodes"], 3).copy()
        tris = np.frombuffer(r["triangles"], dtype=np.int32).reshape(r["n_triangles"], 3).astype(np.int64)
        src = np.frombuffer(r["node_source"], dtype=np.int8).copy() if r["node_source"] else None
        return TIN(nodes, tris), src

    def delete_tin_run(self, run_id: int) -> bool:
        with self._connect() as c:
            for s in c.execute("SELECT id FROM contour_sets WHERE run_id=?", (run_id,)).fetchall():
                c.execute("DELETE FROM contours WHERE set_id=?", (s[0],))
            c.execute("DELETE FROM contour_sets WHERE run_id=?", (run_id,))
            for s in c.execute("SELECT id FROM section_sets WHERE run_id=?", (run_id,)).fetchall():
                c.execute("DELETE FROM section_lines WHERE set_id=?", (s[0],))
            c.execute("DELETE FROM section_sets WHERE run_id=?", (run_id,))
            return c.execute("DELETE FROM tin_runs WHERE id=?", (run_id,)).rowcount > 0

    # ------------------------------------------------------------------ contours
    def save_contours(self, run_id: int, params: dict, style: dict, lines: Sequence[ContourLine], name: str = "") -> int:
        levels = sorted({float(c.level) for c in lines})
        with self._connect() as c:
            cur = c.execute(
                "INSERT INTO contour_sets(run_id, created, name, params, style, n_lines, levels) VALUES (?,?,?,?,?,?,?)",
                (run_id, _now(), name, json.dumps(params), json.dumps(style), len(lines), json.dumps(levels)),
            )
            set_id = int(cur.lastrowid)
            rows = []
            xs, ys = [], []
            for cl in lines:
                coords = np.column_stack([cl.coords, np.full(len(cl.coords), cl.level)])
                g = shapely.LineString(coords)
                rows.append((gpkg_blob(g, self.srs_id), set_id, float(cl.level), int(cl.is_major), int(cl.closed), float(cl.length), len(cl.coords)))
                xs.append(cl.coords[:, 0])
                ys.append(cl.coords[:, 1])
            if rows:
                c.executemany("INSERT INTO contours(geom, set_id, level, major, closed, length, n_vertices) VALUES (?,?,?,?,?,?,?)", rows)
                self._bump_extent(c, "contours", np.concatenate(xs), np.concatenate(ys))
        return set_id

    @staticmethod
    def _set_row(r) -> dict:
        d = dict(r)
        d["params"] = json.loads(d.get("params") or "{}")
        d["style"] = json.loads(d.get("style") or "{}")
        d["levels"] = json.loads(d.get("levels") or "[]")
        return d

    def contour_sets(self, run_id: int | None = None) -> list[dict]:
        q = "SELECT * FROM contour_sets"
        vals: list[Any] = []
        if run_id is not None:
            q += " WHERE run_id=?"
            vals.append(run_id)
        with self._connect() as c:
            return [self._set_row(r) for r in c.execute(q + " ORDER BY id", vals)]

    def get_contour_set(self, set_id: int) -> dict | None:
        with self._connect() as c:
            r = c.execute("SELECT * FROM contour_sets WHERE id=?", (set_id,)).fetchone()
        return self._set_row(r) if r else None

    def update_contour_set(self, set_id: int, style: dict | None = None, name: str | None = None) -> None:
        with self._connect() as c:
            if style is not None:
                c.execute("UPDATE contour_sets SET style=? WHERE id=?", (json.dumps(style), set_id))
            if name is not None:
                c.execute("UPDATE contour_sets SET name=? WHERE id=?", (name, set_id))

    def load_contours(self, set_id: int, major_only: bool = False) -> list[ContourLine]:
        q = "SELECT geom, level, major, closed FROM contours WHERE set_id=?"
        if major_only:
            q += " AND major=1"
        out = []
        with self._connect() as c:
            for r in c.execute(q + " ORDER BY level, fid", (set_id,)):
                g = from_gpkg_blob(r["geom"])
                coords = shapely.get_coordinates(g)
                out.append(ContourLine(level=float(r["level"]), is_major=bool(r["major"]), coords=coords, closed=bool(r["closed"])))
        return out

    def delete_contour_set(self, set_id: int) -> bool:
        with self._connect() as c:
            c.execute("DELETE FROM contours WHERE set_id=?", (set_id,))
            return c.execute("DELETE FROM contour_sets WHERE id=?", (set_id,)).rowcount > 0

    # ------------------------------------------------------------------ alignments
    def save_alignment(self, name: str, start_chainage: float, ips: list[dict], style: dict,
                       length: float, dense_coords: np.ndarray, end_chainage: float, alignment_id: int | None = None) -> int:
        ts = _now()
        with self._connect() as c:
            if alignment_id is None:
                cur = c.execute(
                    "INSERT INTO alignments(name, start_chainage, ips, style, length, created, updated) VALUES (?,?,?,?,?,?,?)",
                    (name, start_chainage, json.dumps(ips), json.dumps(style), length, ts, ts),
                )
                alignment_id = int(cur.lastrowid)
            else:
                c.execute(
                    "UPDATE alignments SET name=?, start_chainage=?, ips=?, style=?, length=?, updated=? WHERE id=?",
                    (name, start_chainage, json.dumps(ips), json.dumps(style), length, ts, alignment_id),
                )
                c.execute("DELETE FROM alignment_geom WHERE alignment_id=?", (alignment_id,))
            dc = np.asarray(dense_coords, float)
            if len(dc) >= 2:
                g = shapely.LineString(np.column_stack([dc[:, :2], np.zeros(len(dc))]))
                c.execute(
                    "INSERT INTO alignment_geom(geom, alignment_id, name, start_chainage, end_chainage) VALUES (?,?,?,?,?)",
                    (gpkg_blob(g, self.srs_id), alignment_id, name, start_chainage, end_chainage),
                )
                self._bump_extent(c, "alignment_geom", dc[:, 0], dc[:, 1])
        return alignment_id

    @staticmethod
    def _aln_row(r) -> dict:
        d = dict(r)
        d["ips"] = json.loads(d.get("ips") or "[]")
        d["style"] = json.loads(d.get("style") or "{}")
        return d

    def alignments(self) -> list[dict]:
        with self._connect() as c:
            return [self._aln_row(r) for r in c.execute("SELECT * FROM alignments ORDER BY id")]

    def get_alignment(self, alignment_id: int) -> dict | None:
        with self._connect() as c:
            r = c.execute("SELECT * FROM alignments WHERE id=?", (alignment_id,)).fetchone()
        return self._aln_row(r) if r else None

    def delete_alignment(self, alignment_id: int) -> bool:
        with self._connect() as c:
            c.execute("DELETE FROM alignment_geom WHERE alignment_id=?", (alignment_id,))
            for s in c.execute("SELECT id FROM section_sets WHERE alignment_id=?", (alignment_id,)).fetchall():
                c.execute("DELETE FROM section_lines WHERE set_id=?", (s[0],))
            c.execute("DELETE FROM section_sets WHERE alignment_id=?", (alignment_id,))
            return c.execute("DELETE FROM alignments WHERE id=?", (alignment_id,)).rowcount > 0

    # ------------------------------------------------------------------ section sets
    def save_section_set(self, alignment_id: int, run_id: int, params: dict, profile: list[dict],
                         sections: list[dict], summary: dict, name: str = "") -> int:
        with self._connect() as c:
            cur = c.execute(
                "INSERT INTO section_sets(alignment_id, run_id, created, name, params, profile, sections, summary) VALUES (?,?,?,?,?,?,?,?)",
                (alignment_id, run_id, _now(), name, json.dumps(params), json.dumps(profile), json.dumps(sections), json.dumps(summary)),
            )
            set_id = int(cur.lastrowid)
            rows, xs, ys = [], [], []
            for s in sections:
                xy = np.asarray(s["xy"], float)
                if len(xy) < 2:
                    continue
                z = np.asarray([0.0 if v is None else v for v in s["z"]], float)
                g = shapely.LineString(np.column_stack([xy, z]))
                rows.append((gpkg_blob(g, self.srs_id), set_id, float(s["chainage"]), s.get("label", "")))
                xs.append(xy[:, 0])
                ys.append(xy[:, 1])
            if rows:
                c.executemany("INSERT INTO section_lines(geom, set_id, chainage, label) VALUES (?,?,?,?)", rows)
                self._bump_extent(c, "section_lines", np.concatenate(xs), np.concatenate(ys))
        return set_id

    @staticmethod
    def _sec_row(r, full: bool) -> dict:
        d = dict(r)
        d["params"] = json.loads(d.get("params") or "{}")
        d["summary"] = json.loads(d.get("summary") or "{}")
        if full:
            d["profile"] = json.loads(d.get("profile") or "[]")
            d["sections"] = json.loads(d.get("sections") or "[]")
        else:
            d.pop("profile", None)
            d.pop("sections", None)
        return d

    def section_sets(self, alignment_id: int | None = None) -> list[dict]:
        q = "SELECT id, alignment_id, run_id, created, name, params, summary FROM section_sets"
        vals: list[Any] = []
        if alignment_id is not None:
            q += " WHERE alignment_id=?"
            vals.append(alignment_id)
        with self._connect() as c:
            return [self._sec_row(r, False) for r in c.execute(q + " ORDER BY id", vals)]

    def get_section_set(self, set_id: int, full: bool = True) -> dict | None:
        with self._connect() as c:
            r = c.execute("SELECT * FROM section_sets WHERE id=?", (set_id,)).fetchone()
        return self._sec_row(r, full) if r else None

    def delete_section_set(self, set_id: int) -> bool:
        with self._connect() as c:
            c.execute("DELETE FROM section_lines WHERE set_id=?", (set_id,))
            return c.execute("DELETE FROM section_sets WHERE id=?", (set_id,)).rowcount > 0

    # ------------------------------------------------------------------ misc
    def summary(self) -> dict:
        with self._connect() as c:
            n_pts = c.execute("SELECT COUNT(*) FROM points").fetchone()[0]
            lines = {r[0]: r[1] for r in c.execute("SELECT kind, COUNT(*) FROM lines GROUP BY kind")}
            n_runs = c.execute("SELECT COUNT(*) FROM tin_runs").fetchone()[0]
            n_sets = c.execute("SELECT COUNT(*) FROM contour_sets").fetchone()[0]
            n_aln = c.execute("SELECT COUNT(*) FROM alignments").fetchone()[0]
            n_sec = c.execute("SELECT COUNT(*) FROM section_sets").fetchone()[0]
            ext = c.execute("SELECT min_x, min_y, max_x, max_y FROM gpkg_contents WHERE table_name='points'").fetchone()
            zr = c.execute("SELECT MIN(z), MAX(z) FROM points").fetchone()
        return {
            "points": int(n_pts), "lines": lines, "tin_runs": int(n_runs), "contour_sets": int(n_sets),
            "alignments": int(n_aln), "section_sets": int(n_sec),
            "bounds": [ext[0], ext[1], ext[2], ext[3]] if ext and ext[0] is not None else None,
            "z_range": [zr[0], zr[1]] if zr and zr[0] is not None else None,
        }

    def vacuum(self) -> None:
        with sqlite3.connect(self.path) as c:
            c.execute("VACUUM")
