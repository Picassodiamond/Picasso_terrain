"""Runtime settings (environment variables, prefix PLM_)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("PLM_DATA_DIR", "data")).resolve())
    # jobs with more points than this run in the background (BackgroundTasks / cron worker)
    sync_point_limit: int = int(os.environ.get("PLM_SYNC_POINT_LIMIT", "250000"))
    auth_enabled: bool = field(default_factory=lambda: _env_bool("PLM_AUTH_ENABLED", False))
    open_registration: bool = field(default_factory=lambda: _env_bool("PLM_OPEN_REGISTRATION", True))
    lock_minutes: int = int(os.environ.get("PLM_LOCK_MINUTES", "15"))
    secret_key: str | None = os.environ.get("PLM_SECRET_KEY")
    session_hours: int = int(os.environ.get("PLM_SESSION_HOURS", "72"))
    cors_origins: list[str] = field(
        default_factory=lambda: [o for o in os.environ.get("PLM_CORS_ORIGINS", "*").split(",") if o]
    )
    max_upload_mb: int = int(os.environ.get("PLM_MAX_UPLOAD_MB", "200"))
    web_dist: Path | None = field(
        default_factory=lambda: Path(os.environ["PLM_WEB_DIST"]).resolve() if os.environ.get("PLM_WEB_DIST") else None
    )
    app_title: str = "Picasso LandMesh"
    version: str = "0.2.0"

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "projects").mkdir(exist_ok=True)
        (self.data_dir / "tmp").mkdir(exist_ok=True)
        if not self.secret_key:
            key_file = self.data_dir / ".secret_key"
            if key_file.exists():
                self.secret_key = key_file.read_text().strip()
            else:
                self.secret_key = secrets.token_urlsafe(48)
                key_file.write_text(self.secret_key)
        if self.web_dist is None:
            cand = Path(__file__).resolve().parents[2] / "web" / "dist"
            if cand.exists():
                self.web_dist = cand

    @property
    def app_db_path(self) -> Path:
        return self.data_dir / "plm.db"

    def project_dir(self, project_id: str) -> Path:
        d = self.data_dir / "projects" / project_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def project_gpkg(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "project.gpkg"
