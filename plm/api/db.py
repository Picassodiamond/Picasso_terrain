"""Application database (SQLite): users, projects, jobs, visitors and the usage log."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(n: int = 12) -> str:
    return secrets.token_hex(n // 2)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'editor', organisation TEXT DEFAULT '', created TEXT NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '', crs TEXT DEFAULT 'local',
    owner_id TEXT, created TEXT NOT NULL, updated TEXT NOT NULL, settings TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
    progress REAL DEFAULT 0, message TEXT DEFAULT '', params TEXT DEFAULT '{}', result TEXT,
    error TEXT, created TEXT NOT NULL, started TEXT, finished TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id, created);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS project_members (
    project_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'editor', added TEXT NOT NULL,
    PRIMARY KEY (project_id, user_id)
);
CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, user_id TEXT, username TEXT, target_type TEXT DEFAULT 'project',
    target_id TEXT DEFAULT '', parent_id TEXT, x REAL, y REAL, chainage REAL, text TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, updated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_project ON comments(project_id, created);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, user_id TEXT, username TEXT,
    action TEXT NOT NULL, target_type TEXT DEFAULT '', target_id TEXT DEFAULT '', detail TEXT DEFAULT '{}', created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_project ON activity(project_id, id);
CREATE TABLE IF NOT EXISTS locks (
    project_id TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL, user_id TEXT NOT NULL,
    username TEXT NOT NULL, acquired TEXT NOT NULL, expires TEXT NOT NULL,
    PRIMARY KEY (project_id, target_type, target_id)
);
CREATE TABLE IF NOT EXISTS alignment_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, alignment_id INTEGER NOT NULL, version INTEGER NOT NULL,
    user_id TEXT, username TEXT, note TEXT DEFAULT '', data TEXT NOT NULL, created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aln_versions ON alignment_versions(project_id, alignment_id, version);
CREATE TABLE IF NOT EXISTS visitors (
    id TEXT PRIMARY KEY, ip TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    visits INTEGER NOT NULL DEFAULT 1, user_agent TEXT DEFAULT '', registered INTEGER NOT NULL DEFAULT 0,
    registered_at TEXT, declined_at TEXT, user_id TEXT,
    name TEXT DEFAULT '', email TEXT DEFAULT '', phone TEXT DEFAULT '', designation TEXT DEFAULT '',
    organisation TEXT DEFAULT '', district TEXT DEFAULT '', purpose TEXT DEFAULT '', notes TEXT DEFAULT '',
    actions INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_visitors_ip ON visitors(ip, last_seen);
CREATE TABLE IF NOT EXISTS usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT NOT NULL, visitor_id TEXT, ip TEXT DEFAULT '',
    user_id TEXT, username TEXT DEFAULT '', visitor_name TEXT DEFAULT '', project_id TEXT DEFAULT '',
    module TEXT DEFAULT '', action TEXT NOT NULL, label TEXT DEFAULT '', target_id TEXT DEFAULT '',
    method TEXT DEFAULT '', path TEXT DEFAULT '', status INTEGER DEFAULT 0, ms INTEGER DEFAULT 0,
    detail TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_usage_created ON usage_log(id DESC);
CREATE INDEX IF NOT EXISTS idx_usage_visitor ON usage_log(visitor_id, id);
CREATE TABLE IF NOT EXISTS catalogue (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL, ref_id INTEGER NOT NULL, name TEXT DEFAULT '',
    module TEXT DEFAULT '', crs TEXT, footprint TEXT, bounds TEXT, lon REAL, lat REAL, points INTEGER, triangles INTEGER,
    z_min REAL, z_max REAL, spacing REAL, tags TEXT DEFAULT '[]', created TEXT NOT NULL, updated TEXT NOT NULL,
    UNIQUE(project_id, kind, ref_id)
);
"""


class AppDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        with self._connect() as c:
            c.executescript(_SCHEMA)
            self._migrate(c)

    @staticmethod
    def _migrate(c: sqlite3.Connection) -> None:
        """Columns added after the first release."""
        cols = {r[1] for r in c.execute("PRAGMA table_info(projects)")}
        if "status" not in cols:
            c.execute("ALTER TABLE projects ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
        ucols = {r[1] for r in c.execute("PRAGMA table_info(users)")}
        for col, ddl in (("full_name", "TEXT DEFAULT ''"), ("email", "TEXT DEFAULT ''"), ("notes", "TEXT DEFAULT ''"), ("created_by", "TEXT")):
            if col not in ucols:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
        jcols = {r[1] for r in c.execute("PRAGMA table_info(jobs)")}
        if "user_id" not in jcols:
            c.execute("ALTER TABLE jobs ADD COLUMN user_id TEXT")
        if "priority" not in jcols:
            c.execute("ALTER TABLE jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    # ------------------------------------------------------------------ users
    @staticmethod
    def hash_password(password: str, salt: str | None = None) -> str:
        salt = salt or secrets.token_hex(16)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
        return f"pbkdf2_sha256${salt}${dk.hex()}"

    @staticmethod
    def verify_password(password: str, stored: str) -> bool:
        try:
            _, salt, digest = stored.split("$")
        except ValueError:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
        return hmac.compare_digest(dk.hex(), digest)

    def create_user(self, username: str, password: str, role: str = "editor", organisation: str = "", full_name: str = "",
                    email: str = "", notes: str = "", created_by: str | None = None) -> dict:
        uid = new_id()
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO users(id, username, password_hash, role, organisation, created, full_name, email, notes, created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (uid, username, self.hash_password(password), role, organisation, now_iso(), full_name, email, notes, created_by),
            )
        return self.get_user(uid)  # type: ignore[return-value]

    def update_user(self, uid: str, **fields) -> dict | None:
        sets, vals = [], []
        for k in ("role", "organisation", "full_name", "email", "notes"):
            if fields.get(k) is not None:
                sets.append(f"{k}=?")
                vals.append(fields[k])
        if fields.get("disabled") is not None:
            sets.append("disabled=?")
            vals.append(int(bool(fields["disabled"])))
        if fields.get("password"):
            sets.append("password_hash=?")
            vals.append(self.hash_password(fields["password"]))
        if sets:
            vals.append(uid)
            with self._lock, self._connect() as c:
                c.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", vals)
        return self.get_user(uid)

    def get_user(self, uid: str) -> dict | None:
        with self._connect() as c:
            r = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return dict(r) if r else None

    def get_user_by_name(self, username: str) -> dict | None:
        with self._connect() as c:
            r = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(r) if r else None

    def list_users(self) -> list[dict]:
        with self._connect() as c:
            return [dict(r) for r in c.execute("SELECT id, username, role, organisation, created, disabled, full_name, email, notes, created_by FROM users ORDER BY username")]

    def set_password(self, username: str, password: str) -> bool:
        with self._lock, self._connect() as c:
            cur = c.execute("UPDATE users SET password_hash=? WHERE username=?", (self.hash_password(password), username))
            return cur.rowcount > 0

    def count_users(self) -> int:
        with self._connect() as c:
            return int(c.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    # ------------------------------------------------------------------ projects
    def create_project(self, name: str, crs: str = "local", description: str = "", owner_id: str | None = None,
                       settings: dict | None = None) -> dict:
        pid = new_id()
        ts = now_iso()
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO projects(id, name, description, crs, owner_id, created, updated, settings) VALUES (?,?,?,?,?,?,?,?)",
                (pid, name, description, crs or "local", owner_id, ts, ts, json.dumps(settings or {})),
            )
        return self.get_project(pid)  # type: ignore[return-value]

    def get_project(self, pid: str) -> dict | None:
        with self._connect() as c:
            r = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        return self._project_row(r) if r else None

    def list_projects(self) -> list[dict]:
        with self._connect() as c:
            rows = c.execute("SELECT * FROM projects ORDER BY updated DESC").fetchall()
        return [self._project_row(r) for r in rows]

    def update_project(self, pid: str, **fields: Any) -> dict | None:
        allowed = {"name", "description", "crs", "settings", "owner_id", "status"}
        sets, vals = [], []
        for k, v in fields.items():
            if k in allowed and v is not None:
                sets.append(f"{k}=?")
                vals.append(json.dumps(v) if k == "settings" else v)
        if sets:
            sets.append("updated=?")
            vals.append(now_iso())
            vals.append(pid)
            with self._lock, self._connect() as c:
                c.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id=?", vals)
        return self.get_project(pid)

    def touch_project(self, pid: str) -> None:
        with self._lock, self._connect() as c:
            c.execute("UPDATE projects SET updated=? WHERE id=?", (now_iso(), pid))

    def delete_project(self, pid: str) -> bool:
        with self._lock, self._connect() as c:
            c.execute("DELETE FROM jobs WHERE project_id=?", (pid,))
            c.execute("DELETE FROM catalogue WHERE project_id=?", (pid,))
            c.execute("DELETE FROM project_members WHERE project_id=?", (pid,))
            cur = c.execute("DELETE FROM projects WHERE id=?", (pid,))
            return cur.rowcount > 0

    # ------------------------------------------------------------------ guests
    def projects_owned_by(self, owner_id: str) -> list[dict]:
        with self._connect() as c:
            rows = c.execute("SELECT * FROM projects WHERE owner_id=? ORDER BY created", (owner_id,)).fetchall()
        return [self._project_row(r) for r in rows]

    def claim_guest_projects(self, guest_id: str, user_id: str) -> int:
        """Hand a guest's sandbox projects to the account that was just created / signed in."""
        with self._lock, self._connect() as c:
            rows = c.execute("SELECT id FROM projects WHERE owner_id=?", (guest_id,)).fetchall()
            for r in rows:
                c.execute("INSERT INTO project_members(project_id, user_id, role, added) VALUES (?,?,?,?) "
                          "ON CONFLICT(project_id, user_id) DO UPDATE SET role='owner'", (r[0], user_id, "owner", now_iso()))
            c.execute("UPDATE projects SET owner_id=?, updated=? WHERE owner_id=?", (user_id, now_iso(), guest_id))
            c.execute("UPDATE jobs SET user_id=? WHERE user_id=?", (user_id, guest_id))
        return len(rows)

    def expired_guest_projects(self, ttl_days: int) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
        with self._connect() as c:
            rows = c.execute("SELECT * FROM projects WHERE owner_id LIKE 'guest:%' AND updated < ?", (cutoff,)).fetchall()
        return [self._project_row(r) for r in rows]

    @staticmethod
    def _project_row(r) -> dict:
        d = dict(r)
        d["settings"] = json.loads(d.get("settings") or "{}")
        return d

    # ------------------------------------------------------------------ jobs
    def create_job(self, project_id: str, kind: str, params: dict, user_id: str | None = None, priority: int = 0) -> dict:
        jid = new_id()
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO jobs(id, project_id, kind, status, params, created, user_id, priority) VALUES (?,?,?,?,?,?,?,?)",
                (jid, project_id, kind, "pending", json.dumps(params), now_iso(), user_id, int(priority)),
            )
        return self.get_job(jid)  # type: ignore[return-value]

    def queue_info(self, job: dict) -> dict:
        """Position of a pending job in the fair queue, queue length and a rough ETA from recent runs."""
        with self._connect() as c:
            pending = int(c.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0])
            running = int(c.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0])
            pos = None
            if job.get("status") == "pending":
                # timestamps have one-second resolution: break ties by insertion order (rowid)
                pos = 1 + int(c.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status='pending' AND (priority < ? OR (priority = ? AND (created < ? OR "
                    "(created = ? AND rowid < (SELECT rowid FROM jobs WHERE id=?)))))",
                    (job.get("priority", 0), job.get("priority", 0), job["created"], job["created"], job["id"])).fetchone()[0])
            rows = c.execute("SELECT started, finished FROM jobs WHERE kind=? AND status='done' AND started IS NOT NULL AND finished IS NOT NULL "
                             "ORDER BY finished DESC LIMIT 20", (job["kind"],)).fetchall()
        durs = []
        for st, fi in rows:
            try:
                durs.append((datetime.fromisoformat(fi) - datetime.fromisoformat(st)).total_seconds())
            except ValueError:
                continue
        avg = (sum(durs) / len(durs)) if durs else None
        eta = None
        if avg is not None and pos is not None:
            eta = round(avg * pos + (avg * 0.5 if running else 0.0))
        elif avg is not None and job.get("status") == "running":
            eta = round(avg)
        return {"queue_position": pos, "queue_length": pending, "running": running, "eta_seconds": eta}

    def queue_counts(self) -> dict:
        with self._connect() as c:
            pending = int(c.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0])
            running = int(c.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0])
        return {"pending": pending, "running": running}

    def get_job(self, jid: str) -> dict | None:
        with self._connect() as c:
            r = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        return self._job_row(r) if r else None

    @staticmethod
    def _job_row(r) -> dict:
        d = dict(r)
        d["params"] = json.loads(d.get("params") or "{}")
        d["result"] = json.loads(d["result"]) if d.get("result") else None
        return d

    def list_jobs(self, project_id: str | None = None, status: str | None = None, limit: int = 50) -> list[dict]:
        q = "SELECT * FROM jobs"
        cond, vals = [], []
        if project_id:
            cond.append("project_id=?")
            vals.append(project_id)
        if status:
            cond.append("status=?")
            vals.append(status)
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY created DESC LIMIT ?"
        vals.append(limit)
        with self._connect() as c:
            return [self._job_row(r) for r in c.execute(q, vals)]

    def claim_job(self, jid: str) -> bool:
        """Atomically move pending -> running; False if someone else already took it."""
        with self._lock, self._connect() as c:
            cur = c.execute("UPDATE jobs SET status='running', started=? WHERE id=? AND status='pending'", (now_iso(), jid))
            return cur.rowcount > 0

    def next_pending_job(self) -> dict | None:
        """Fair FIFO: lower priority number first, then oldest; a user with a job already running
        waits until it finishes (one running job per user)."""
        with self._connect() as c:
            r = c.execute(
                "SELECT * FROM jobs j WHERE status='pending' AND (user_id IS NULL OR user_id NOT IN "
                "(SELECT user_id FROM jobs WHERE status='running' AND user_id IS NOT NULL)) "
                "ORDER BY priority, created, rowid LIMIT 1").fetchone()
        return self._job_row(r) if r else None

    # ------------------------------------------------------------------ catalogue
    def catalogue_upsert(self, item: dict) -> dict:
        ts = now_iso()
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO catalogue(id, project_id, kind, ref_id, name, module, crs, footprint, bounds, lon, lat, points, triangles, z_min, z_max, spacing, tags, created, updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id, kind, ref_id) DO UPDATE SET "
                "name=excluded.name, module=excluded.module, crs=excluded.crs, footprint=excluded.footprint, bounds=excluded.bounds, lon=excluded.lon, lat=excluded.lat, "
                "points=excluded.points, triangles=excluded.triangles, z_min=excluded.z_min, z_max=excluded.z_max, spacing=excluded.spacing, tags=excluded.tags, updated=excluded.updated",
                (new_id(), item["project_id"], item["kind"], int(item["ref_id"]), item.get("name", ""), item.get("module", ""), item.get("crs"),
                 json.dumps(item["footprint"]) if item.get("footprint") is not None else None, json.dumps(item.get("bounds")) if item.get("bounds") is not None else None,
                 item.get("lon"), item.get("lat"), item.get("points"), item.get("triangles"), item.get("z_min"), item.get("z_max"), item.get("spacing"),
                 json.dumps(item.get("tags") or []), ts, ts),
            )
            r = c.execute("SELECT * FROM catalogue WHERE project_id=? AND kind=? AND ref_id=?", (item["project_id"], item["kind"], int(item["ref_id"]))).fetchone()
        return self._cat_row(r)

    @staticmethod
    def _cat_row(r) -> dict:
        d = dict(r)
        d["footprint"] = json.loads(d["footprint"]) if d.get("footprint") else None
        d["bounds"] = json.loads(d["bounds"]) if d.get("bounds") else None
        d["tags"] = json.loads(d.get("tags") or "[]")
        return d

    def catalogue_items(self, kind: str | None = None, project_id: str | None = None) -> list[dict]:
        q = "SELECT * FROM catalogue"
        cond, vals = [], []
        if kind:
            cond.append("kind=?")
            vals.append(kind)
        if project_id:
            cond.append("project_id=?")
            vals.append(project_id)
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY updated DESC"
        with self._connect() as c:
            return [self._cat_row(r) for r in c.execute(q, vals)]

    def catalogue_get(self, item_id: str) -> dict | None:
        with self._connect() as c:
            r = c.execute("SELECT * FROM catalogue WHERE id=?", (item_id,)).fetchone()
        return self._cat_row(r) if r else None

    def catalogue_delete(self, project_id: str, kind: str | None = None, ref_id: int | None = None) -> int:
        with self._lock, self._connect() as c:
            if kind is not None and ref_id is not None:
                return c.execute("DELETE FROM catalogue WHERE project_id=? AND kind=? AND ref_id=?", (project_id, kind, int(ref_id))).rowcount
            return c.execute("DELETE FROM catalogue WHERE project_id=?", (project_id,)).rowcount

    # ------------------------------------------------------------------ membership
    def add_member(self, project_id: str, user_id: str, role: str = "editor") -> None:
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO project_members(project_id, user_id, role, added) VALUES (?,?,?,?) "
                "ON CONFLICT(project_id, user_id) DO UPDATE SET role=excluded.role",
                (project_id, user_id, role, now_iso()),
            )

    def remove_member(self, project_id: str, user_id: str) -> bool:
        with self._lock, self._connect() as c:
            return c.execute("DELETE FROM project_members WHERE project_id=? AND user_id=?", (project_id, user_id)).rowcount > 0

    def members(self, project_id: str) -> list[dict]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT m.user_id, m.role, m.added, u.username, u.organisation FROM project_members m "
                "JOIN users u ON u.id = m.user_id WHERE m.project_id=? ORDER BY u.username", (project_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def member_role(self, project_id: str, user_id: str) -> str | None:
        with self._connect() as c:
            r = c.execute("SELECT role FROM project_members WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone()
        return r[0] if r else None

    def project_ids_for_user(self, user_id: str) -> set[str]:
        with self._connect() as c:
            return {r[0] for r in c.execute("SELECT project_id FROM project_members WHERE user_id=?", (user_id,))}

    # ------------------------------------------------------------------ comments
    def add_comment(self, project_id: str, user: dict, text: str, target_type: str = "project", target_id: str = "",
                    parent_id: str | None = None, x: float | None = None, y: float | None = None, chainage: float | None = None) -> dict:
        cid = new_id()
        ts = now_iso()
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO comments(id, project_id, user_id, username, target_type, target_id, parent_id, x, y, chainage, text, created, updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, project_id, user.get("id"), user.get("username"), target_type, target_id, parent_id, x, y, chainage, text, ts, ts),
            )
        return self.get_comment(cid)  # type: ignore[return-value]

    def get_comment(self, cid: str) -> dict | None:
        with self._connect() as c:
            r = c.execute("SELECT * FROM comments WHERE id=?", (cid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["resolved"] = bool(d["resolved"])
        return d

    def comments(self, project_id: str, target_type: str | None = None, target_id: str | None = None,
                 include_resolved: bool = True) -> list[dict]:
        q = "SELECT * FROM comments WHERE project_id=?"
        vals: list[Any] = [project_id]
        if target_type:
            q += " AND target_type=?"
            vals.append(target_type)
        if target_id is not None:
            q += " AND target_id=?"
            vals.append(str(target_id))
        if not include_resolved:
            q += " AND resolved=0"
        q += " ORDER BY created"
        with self._connect() as c:
            out = []
            for r in c.execute(q, vals):
                d = dict(r)
                d["resolved"] = bool(d["resolved"])
                out.append(d)
            return out

    def update_comment(self, cid: str, text: str | None = None, resolved: bool | None = None) -> dict | None:
        sets, vals = [], []
        if text is not None:
            sets.append("text=?")
            vals.append(text)
        if resolved is not None:
            sets.append("resolved=?")
            vals.append(int(resolved))
        if sets:
            sets.append("updated=?")
            vals.append(now_iso())
            vals.append(cid)
            with self._lock, self._connect() as c:
                c.execute(f"UPDATE comments SET {', '.join(sets)} WHERE id=?", vals)
        return self.get_comment(cid)

    def delete_comment(self, cid: str) -> bool:
        with self._lock, self._connect() as c:
            c.execute("DELETE FROM comments WHERE parent_id=?", (cid,))
            return c.execute("DELETE FROM comments WHERE id=?", (cid,)).rowcount > 0

    # ------------------------------------------------------------------ activity
    def log(self, project_id: str, user: dict | None, action: str, target_type: str = "", target_id: str | int = "",
            detail: dict | None = None) -> None:
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO activity(project_id, user_id, username, action, target_type, target_id, detail, created) VALUES (?,?,?,?,?,?,?,?)",
                (project_id, (user or {}).get("id"), (user or {}).get("username"), action, target_type, str(target_id),
                 json.dumps(detail or {}), now_iso()),
            )

    def activity(self, project_id: str, since_id: int = 0, limit: int = 100) -> list[dict]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT * FROM activity WHERE project_id=? AND id>? ORDER BY id DESC LIMIT ?", (project_id, since_id, limit)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d.get("detail") or "{}")
            out.append(d)
        return out

    # ------------------------------------------------------------------ locks
    def get_lock(self, project_id: str, target_type: str, target_id: str | int) -> dict | None:
        with self._lock, self._connect() as c:
            r = c.execute("SELECT * FROM locks WHERE project_id=? AND target_type=? AND target_id=?",
                          (project_id, target_type, str(target_id))).fetchone()
            if r and r["expires"] < now_iso():
                c.execute("DELETE FROM locks WHERE project_id=? AND target_type=? AND target_id=?", (project_id, target_type, str(target_id)))
                return None
        return dict(r) if r else None

    def acquire_lock(self, project_id: str, target_type: str, target_id: str | int, user: dict, minutes: int = 15) -> tuple[bool, dict]:
        """Returns (acquired, lock). Renews when the same user already holds it."""
        from datetime import timedelta

        cur = self.get_lock(project_id, target_type, target_id)
        if cur and cur["user_id"] != user["id"]:
            return False, cur
        exp = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).replace(microsecond=0).isoformat()
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO locks(project_id, target_type, target_id, user_id, username, acquired, expires) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(project_id, target_type, target_id) DO UPDATE SET expires=excluded.expires, user_id=excluded.user_id, username=excluded.username",
                (project_id, target_type, str(target_id), user["id"], user["username"], cur["acquired"] if cur else now_iso(), exp),
            )
        return True, self.get_lock(project_id, target_type, target_id)  # type: ignore[return-value]

    def release_lock(self, project_id: str, target_type: str, target_id: str | int, user: dict | None = None, force: bool = False) -> bool:
        with self._lock, self._connect() as c:
            if force or user is None:
                cur = c.execute("DELETE FROM locks WHERE project_id=? AND target_type=? AND target_id=?", (project_id, target_type, str(target_id)))
            else:
                cur = c.execute("DELETE FROM locks WHERE project_id=? AND target_type=? AND target_id=? AND user_id=?",
                                (project_id, target_type, str(target_id), user["id"]))
            return cur.rowcount > 0

    def locks(self, project_id: str) -> list[dict]:
        with self._lock, self._connect() as c:
            c.execute("DELETE FROM locks WHERE expires < ?", (now_iso(),))
            return [dict(r) for r in c.execute("SELECT * FROM locks WHERE project_id=?", (project_id,))]

    # ------------------------------------------------------------------ alignment versions
    def add_alignment_version(self, project_id: str, alignment_id: int, data: dict, user: dict | None, note: str = "") -> dict:
        with self._lock, self._connect() as c:
            r = c.execute("SELECT COALESCE(MAX(version), 0) FROM alignment_versions WHERE project_id=? AND alignment_id=?",
                          (project_id, alignment_id)).fetchone()
            v = int(r[0]) + 1
            c.execute(
                "INSERT INTO alignment_versions(project_id, alignment_id, version, user_id, username, note, data, created) VALUES (?,?,?,?,?,?,?,?)",
                (project_id, alignment_id, v, (user or {}).get("id"), (user or {}).get("username"), note, json.dumps(data), now_iso()),
            )
        return {"alignment_id": alignment_id, "version": v}

    def alignment_versions(self, project_id: str, alignment_id: int, with_data: bool = False) -> list[dict]:
        with self._connect() as c:
            rows = c.execute("SELECT * FROM alignment_versions WHERE project_id=? AND alignment_id=? ORDER BY version DESC",
                             (project_id, alignment_id)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if with_data:
                d["data"] = json.loads(d["data"])
            else:
                d.pop("data", None)
            out.append(d)
        return out

    def get_alignment_version(self, project_id: str, alignment_id: int, version: int) -> dict | None:
        with self._connect() as c:
            r = c.execute("SELECT * FROM alignment_versions WHERE project_id=? AND alignment_id=? AND version=?",
                          (project_id, alignment_id, version)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["data"] = json.loads(d["data"])
        return d

    def update_job(self, jid: str, **fields: Any) -> None:
        allowed = {"status", "progress", "message", "result", "error", "started", "finished"}
        sets, vals = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(json.dumps(v) if k == "result" and v is not None else v)
        if not sets:
            return
        vals.append(jid)
        with self._lock, self._connect() as c:
            c.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", vals)

    # ------------------------------------------------------------------ visitors
    VISITOR_FIELDS = ("name", "email", "phone", "designation", "organisation", "district", "purpose", "notes")

    def get_visitor(self, vid: str) -> dict | None:
        with self._connect() as c:
            r = c.execute("SELECT * FROM visitors WHERE id=?", (vid,)).fetchone()
        return dict(r) if r else None

    def visitor_by_ip(self, ip: str) -> dict | None:
        """The most useful earlier visit from this address: a registered one if there is one, else the
        latest. This is what makes a returning person on the same connection a known visitor."""
        with self._connect() as c:
            r = c.execute("SELECT * FROM visitors WHERE ip=? ORDER BY registered DESC, last_seen DESC LIMIT 1",
                          (ip,)).fetchone()
        return dict(r) if r else None

    def visitor_for_user(self, user_id: str) -> dict | None:
        with self._connect() as c:
            r = c.execute("SELECT * FROM visitors WHERE user_id=? ORDER BY registered DESC, last_seen DESC LIMIT 1",
                          (user_id,)).fetchone()
        return dict(r) if r else None

    def create_visitor(self, ip: str, user_agent: str = "", user_id: str | None = None) -> dict:
        vid = new_id(16)
        ts = now_iso()
        with self._lock, self._connect() as c:
            c.execute("INSERT INTO visitors(id, ip, first_seen, last_seen, visits, user_agent, user_id) VALUES (?,?,?,?,1,?,?)",
                      (vid, ip, ts, ts, user_agent[:400], user_id))
        return self.get_visitor(vid)  # type: ignore[return-value]

    def touch_visitor(self, vid: str, ip: str = "", user_id: str | None = None, new_visit: bool = False) -> None:
        with self._lock, self._connect() as c:
            c.execute("UPDATE visitors SET last_seen=?, visits=visits+?, ip=COALESCE(NULLIF(?,''), ip),"
                      " user_id=COALESCE(?, user_id) WHERE id=?",
                      (now_iso(), 1 if new_visit else 0, ip, user_id, vid))

    def register_visitor(self, vid: str, fields: dict, user_id: str | None = None) -> dict | None:
        sets = ["registered=1", "registered_at=?", "declined_at=NULL"]
        vals: list[Any] = [now_iso()]
        for k in self.VISITOR_FIELDS:
            if fields.get(k) is not None:
                sets.append(f"{k}=?")
                vals.append(str(fields[k])[:200])
        if user_id:
            sets.append("user_id=?")
            vals.append(user_id)
        vals.append(vid)
        with self._lock, self._connect() as c:
            c.execute(f"UPDATE visitors SET {', '.join(sets)} WHERE id=?", vals)
        return self.get_visitor(vid)

    def decline_visitor(self, vid: str) -> None:
        with self._lock, self._connect() as c:
            c.execute("UPDATE visitors SET declined_at=? WHERE id=?", (now_iso(), vid))

    def list_visitors(self, registered: bool | None = None, q: str = "", limit: int = 500) -> list[dict]:
        sql = "SELECT * FROM visitors"
        where, vals = [], []
        if registered is not None:
            where.append("registered=?")
            vals.append(int(registered))
        if q:
            like = f"%{q}%"
            where.append("(ip LIKE ? OR name LIKE ? OR email LIKE ? OR phone LIKE ? OR organisation LIKE ? OR designation LIKE ?)")
            vals += [like] * 6
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY last_seen DESC LIMIT ?"
        vals.append(limit)
        with self._connect() as c:
            return [dict(r) for r in c.execute(sql, vals)]

    def visitor_counts(self) -> dict:
        with self._connect() as c:
            row = c.execute("SELECT COUNT(*) n, SUM(registered) reg FROM visitors").fetchone()
            today = c.execute("SELECT COUNT(*) n FROM visitors WHERE last_seen>=?",
                              (now_iso()[:10],)).fetchone()
            acts = c.execute("SELECT COUNT(*) n FROM usage_log").fetchone()
        return {"visitors": row["n"] or 0, "registered": row["reg"] or 0, "active_today": today["n"] or 0,
                "logged_actions": acts["n"] or 0}

    # ------------------------------------------------------------------ usage log
    def log_usage(self, action: str, *, label: str = "", visitor: dict | None = None, user: dict | None = None,
                  project_id: str = "", module: str = "", target_id: str | int = "", method: str = "", path: str = "",
                  status: int = 0, ms: int = 0, detail: dict | None = None) -> None:
        v = visitor or {}
        u = user or {}
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO usage_log(created, visitor_id, ip, user_id, username, visitor_name, project_id, module,"
                " action, label, target_id, method, path, status, ms, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now_iso(), v.get("id"), v.get("ip", ""), u.get("id"), u.get("username", ""), v.get("name", ""),
                 project_id, module, action, label, str(target_id), method, path, int(status), int(ms),
                 json.dumps(detail or {})),
            )
            if v.get("id"):
                c.execute("UPDATE visitors SET actions=actions+1 WHERE id=?", (v["id"],))

    def usage_log(self, visitor_id: str = "", project_id: str = "", action: str = "", since_id: int = 0,
                  limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM usage_log"
        where, vals = [], []
        for col, val in (("visitor_id", visitor_id), ("project_id", project_id), ("action", action)):
            if val:
                where.append(f"{col}=?")
                vals.append(val)
        if since_id:
            where.append("id>?")
            vals.append(since_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        vals.append(limit)
        with self._connect() as c:
            rows = [dict(r) for r in c.execute(sql, vals)]
        for r in rows:
            r["detail"] = json.loads(r.get("detail") or "{}")
        return rows

    def usage_by_action(self, days: int = 30) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()
        with self._connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT action, label, COUNT(*) n, COUNT(DISTINCT visitor_id) visitors, MAX(created) last"
                " FROM usage_log WHERE created>=? GROUP BY action ORDER BY n DESC", (since,))]
