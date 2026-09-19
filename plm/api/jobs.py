"""Job execution shared by the API (BackgroundTasks) and the cron worker."""
from __future__ import annotations

import traceback
from typing import Callable

from .config import Settings
from .db import AppDB, now_iso
from .gpkg import ProjectStore
from . import services

JOB_KINDS: dict[str, Callable] = {
    "tin": services.run_tin,
    "contours": services.make_contours,
    "sections": services.run_sections,
}


def execute_job(job_id: str, settings: Settings, db: AppDB | None = None) -> dict | None:
    db = db or AppDB(settings.app_db_path)
    job = db.get_job(job_id)
    if job is None:
        return None
    if job["status"] == "pending" and not db.claim_job(job_id):
        return db.get_job(job_id)
    if job["status"] not in ("pending", "running"):
        return job
    if job["status"] == "running" and job.get("started") is None:
        db.update_job(job_id, started=now_iso())

    def progress(frac: float, message: str = "") -> None:
        db.update_job(job_id, progress=float(frac), message=message)

    try:
        fn = JOB_KINDS[job["kind"]]
        store = ProjectStore.open(settings.project_gpkg(job["project_id"]))
        result = fn(store, job["params"], progress=progress)
        # keep job results compact: strip bulky arrays
        compact = {k: v for k, v in result.items() if k not in ("profile", "sections", "issues")}
        if "issues" in result:
            compact["issues_count"] = len(result["issues"])
        db.update_job(job_id, status="done", progress=1.0, message="done", result=compact, finished=now_iso())
        db.touch_project(job["project_id"])
    except services.ServiceError as e:
        db.update_job(job_id, status="error", error=str(e), finished=now_iso())
    except Exception as e:  # noqa: BLE001
        db.update_job(job_id, status="error", error=f"{type(e).__name__}: {e}\n{traceback.format_exc()[-2000:]}", finished=now_iso())
    return db.get_job(job_id)


def run_pending_jobs(settings: Settings, max_jobs: int = 10) -> int:
    """Process pending jobs (cron worker). Returns the number of jobs executed."""
    db = AppDB(settings.app_db_path)
    n = 0
    while n < max_jobs:
        job = db.next_pending_job()
        if job is None:
            break
        execute_job(job["id"], settings, db)
        n += 1
    return n
