from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import current_user
from ..db import AppDB
from ..deps import get_db, get_project
from ..schemas import JobOut

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: AppDB = Depends(get_db), _: dict = Depends(current_user)):
    j = db.get_job(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JobOut(**j)


@router.get("/projects/{project_id}/jobs", response_model=list[JobOut])
def list_jobs(project_id: str, status: str | None = None, limit: int = 50, db: AppDB = Depends(get_db), p: dict = Depends(get_project), _: dict = Depends(current_user)):
    return [JobOut(**j) for j in db.list_jobs(project_id, status, limit)]
