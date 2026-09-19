"""Application database (SQLite): users, projects, jobs."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
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
"""


class AppDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        with self._connect() as c:
            c.executescript(_SCHEMA)

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

    def create_user(self, username: str, password: str, role: str = "editor", organisation: str = "") -> dict:
        uid = new_id()
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO users(id, username, password_hash, role, organisation, created) VALUES (?,?,?,?,?,?)",
                (uid, username, self.hash_password(password), role, organisation, now_iso()),
            )
        return self.get_user(uid)  # type: ignore[return-value]

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
            return [dict(r) for r in c.execute("SELECT id, username, role, organisation, created, disabled FROM users ORDER BY username")]

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
        if not r:
            return None
        d = dict(r)
        d["settings"] = json.loads(d.get("settings") or "{}")
        return d

    def list_projects(self) -> list[dict]:
        with self._connect() as c:
            rows = c.execute("SELECT * FROM projects ORDER BY updated DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["settings"] = json.loads(d.get("settings") or "{}")
            out.append(d)
        return out

    def update_project(self, pid: str, **fields: Any) -> dict | None:
        allowed = {"name", "description", "crs", "settings"}
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
            cur = c.execute("DELETE FROM projects WHERE id=?", (pid,))
            return cur.rowcount > 0

    # ------------------------------------------------------------------ jobs
    def create_job(self, project_id: str, kind: str, params: dict) -> dict:
        jid = new_id()
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO jobs(id, project_id, kind, status, params, created) VALUES (?,?,?,?,?,?)",
                (jid, project_id, kind, "pending", json.dumps(params), now_iso()),
            )
        return self.get_job(jid)  # type: ignore[return-value]

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
        with self._connect() as c:
            r = c.execute("SELECT * FROM jobs WHERE status='pending' ORDER BY created LIMIT 1").fetchone()
        return self._job_row(r) if r else None

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
