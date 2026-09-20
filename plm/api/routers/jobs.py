from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import current_user
from ..db import AppDB
from ..deps import get_db, get_project, project_role, require_project
from ..schemas import JobOut

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: AppDB = Depends(get_db), user: dict = Depends(current_user)):
    """Job status with its place in the queue (queue_position, queue_length, eta_seconds)."""
    j = db.get_job(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    p = db.get_project(j["project_id"])
    if p is not None and project_role(db, p, user) is None:
        raise HTTPException(status_code=403, detail="you do not have access to this project")
    return JobOut(**(j | db.queue_info(j)))


@router.get("/projects/{project_id}/jobs", response_model=list[JobOut])
def list_jobs(project_id: str, status: str | None = None, limit: int = 50, db: AppDB = Depends(get_db), p: dict = Depends(get_project),
              _: dict = Depends(require_project("viewer"))):
    return [JobOut(**(j | db.queue_info(j))) for j in db.list_jobs(project_id, status, limit)]
