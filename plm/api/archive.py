"""Archive bundles, restore and server backups.

An archive is a portable zip of one project: its GeoPackage (open format, readable by QGIS) plus
meta.json with the project record, members and designs. Archiving marks the project read-only;
restoring makes it editable again. A backup is a consistent copy of the whole server state.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .db import AppDB, now_iso
from .gpkg import ProjectStore


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _consistent_copy(src: Path, dst: Path) -> None:
    """Copy a SQLite/GeoPackage file with the WAL folded in (sqlite backup API)."""
    a = sqlite3.connect(src)
    b = sqlite3.connect(dst)
    try:
        a.backup(b)
    finally:  # the context manager only commits; Windows needs the handles closed before the file moves
        b.close()
        a.close()


def archive_project(settings: Settings, db: AppDB, p: dict, user: dict | None, mark: bool = True) -> Path:
    pid = p["id"]
    gpkg = settings.project_gpkg(pid)
    out = settings.archive_dir / f"{pid}-{_stamp()}.zip"
    tmp_gpkg = settings.data_dir / "tmp" / f"{pid}-archive.gpkg"
    tmp_gpkg.parent.mkdir(parents=True, exist_ok=True)
    designs: list[dict] = []
    if gpkg.exists():
        _consistent_copy(gpkg, tmp_gpkg)
        try:
            designs = ProjectStore.open(gpkg).designs()
        except Exception:  # noqa: BLE001
            designs = []
    meta = {
        "format": "plm-archive/1", "archived": now_iso(), "archived_by": (user or {}).get("username"),
        "project": {k: v for k, v in p.items() if k != "settings"} | {"settings": p.get("settings") or {}},
        "members": db.members(pid), "designs": designs, "version": settings.version,
    }
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        if tmp_gpkg.exists():
            z.write(tmp_gpkg, "project.gpkg")
        z.writestr("meta.json", json.dumps(meta, indent=1, default=str))
    tmp_gpkg.unlink(missing_ok=True)
    if mark:
        db.update_project(pid, status="archived")
        db.log(pid, user, "project_archived", "project", pid, {"file": out.name})
    return out


def restore_project(settings: Settings, db: AppDB, p: dict, user: dict | None) -> None:
    db.update_project(p["id"], status="active")
    db.log(p["id"], user, "project_restored", "project", p["id"], {})


def list_archives(settings: Settings) -> list[dict]:
    items = []
    for f in sorted(settings.archive_dir.glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True):
        pid = f.name.rsplit("-", 1)[0]
        items.append({"file": f.name, "project_id": pid, "size": f.stat().st_size,
                      "created": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()})
    return items


def latest_archive(settings: Settings, project_id: str) -> Path | None:
    files = sorted(settings.archive_dir.glob(f"{project_id}-*.zip"), key=lambda x: x.stat().st_mtime, reverse=True)
    return files[0] if files else None


def import_archive(settings: Settings, db: AppDB, zip_path: Path, owner: dict | None, name: str | None = None) -> dict:
    """Bring an archive bundle back as a new project (e.g. on another server)."""
    with zipfile.ZipFile(zip_path) as z:
        meta = json.loads(z.read("meta.json"))
        proj = meta["project"]
        p = db.create_project(name or proj["name"], proj.get("crs") or "local", proj.get("description") or "",
                              owner_id=(owner or {}).get("id"), settings=proj.get("settings") or {})
        dst = settings.project_gpkg(p["id"])
        dst.parent.mkdir(parents=True, exist_ok=True)
        with z.open("project.gpkg") as src, open(dst, "wb") as f:
            shutil.copyfileobj(src, f)
    if owner and owner.get("authenticated"):
        db.add_member(p["id"], owner["id"], "owner")
    db.log(p["id"], owner, "project_imported", "project", p["id"], {"archive": Path(zip_path).name})
    return db.get_project(p["id"])  # type: ignore[return-value]


def backup(settings: Settings, out_dir: Path | None = None) -> Path:
    """Consistent copy of the app database and every project file into one zip."""
    out_dir = out_dir or (settings.data_dir / "backups")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"plm-backup-{_stamp()}.zip"
    tmp = settings.data_dir / "tmp" / "backup"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    _consistent_copy(settings.app_db_path, tmp / "plm.db")
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.write(tmp / "plm.db", "plm.db")
        for d in (settings.data_dir / "projects").iterdir():
            g = d / "project.gpkg"
            if g.exists():
                t = tmp / f"{d.name}.gpkg"
                _consistent_copy(g, t)
                z.write(t, f"projects/{d.name}/project.gpkg")
    shutil.rmtree(tmp, ignore_errors=True)
    return out
