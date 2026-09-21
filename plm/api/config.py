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
    open_registration: bool = field(default_factory=lambda: _env_bool("PLM_OPEN_REGISTRATION", False))  # invite-only: admins create accounts
    lock_minutes: int = int(os.environ.get("PLM_LOCK_MINUTES", "15"))
    secret_key: str | None = os.environ.get("PLM_SECRET_KEY")
    session_hours: int = int(os.environ.get("PLM_SESSION_HOURS", "72"))
    cors_origins: list[str] = field(
        default_factory=lambda: [o for o in os.environ.get("PLM_CORS_ORIGINS", "*").split(",") if o]
    )
    max_upload_mb: int = int(os.environ.get("PLM_MAX_UPLOAD_MB", "200"))
    # read by plm.api.services at import: PLM_TIN_CACHE (loaded TINs kept per process, default 2)
    # and PLM_TILE_TRIANGLES (triangles per mesh tile for the browser, default 100000)
    tin_cache_size: int = int(os.environ.get("PLM_TIN_CACHE", "2"))
    tile_triangles: int = int(os.environ.get("PLM_TILE_TRIANGLES", "100000"))
    # guest sandbox (only meaningful when auth is enabled): try without an account, within quotas
    guest_enabled: bool = field(default_factory=lambda: _env_bool("PLM_GUEST_ENABLED", True))
    guest_max_points: int = int(os.environ.get("PLM_GUEST_MAX_POINTS", "5000"))
    guest_max_projects: int = int(os.environ.get("PLM_GUEST_MAX_PROJECTS", "2"))
    guest_max_tin_runs: int = int(os.environ.get("PLM_GUEST_MAX_TIN_RUNS", "3"))
    guest_ttl_days: int = int(os.environ.get("PLM_GUEST_TTL_DAYS", "7"))
    # visitor tracking: recognise a person by cookie, else by the address they connect from, offer the
    # introduction form once, and log the major actions (see plm/api/visitors.py)
    visitor_tracking: bool = field(default_factory=lambda: _env_bool("PLM_VISITOR_TRACKING", True))
    visitor_intake: bool = field(default_factory=lambda: _env_bool("PLM_VISITOR_INTAKE", True))
    # True makes the form compulsory before any major action; the default only offers it
    visitor_intake_required: bool = field(default_factory=lambda: _env_bool("PLM_VISITOR_INTAKE_REQUIRED", False))
    visitor_intake_repeat_days: int = int(os.environ.get("PLM_VISITOR_INTAKE_REPEAT_DAYS", "7"))
    visitor_cookie_days: int = int(os.environ.get("PLM_VISITOR_COOKIE_DAYS", "365"))
    visitor_visit_minutes: int = int(os.environ.get("PLM_VISITOR_VISIT_MINUTES", "30"))  # gap that starts a new visit
    trust_proxy: bool = field(default_factory=lambda: _env_bool("PLM_TRUST_PROXY", True))  # read X-Forwarded-For
    # jobs: "inline" runs background jobs inside the web process (single server, default);
    # "worker" only enqueues and a separate `python -m plm.worker --loop 2` process executes them
    job_mode: str = os.environ.get("PLM_JOB_MODE", "inline")
    max_concurrent_heavy: int = int(os.environ.get("PLM_MAX_HEAVY", "2"))  # heavy synchronous requests at once
    busy_queue_threshold: int = int(os.environ.get("PLM_BUSY_QUEUE", "3"))  # pending jobs before the UI shows "busy"
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

    @property
    def archive_dir(self) -> Path:
        d = self.data_dir / "archive"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def project_dir(self, project_id: str) -> Path:
        d = self.data_dir / "projects" / project_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def project_gpkg(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "project.gpkg"
