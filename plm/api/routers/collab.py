"""Collaboration: project members, comments (feedback), activity feed, edit locks, alignment history."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import current_user, require_editor
from ..db import AppDB
from ..deps import get_db, get_project, get_store
from ..gpkg import ProjectStore
from ..schemas import AlignmentOut
from .. import services

router = APIRouter(prefix="/projects/{project_id}", tags=["collaboration"])

LOCK_MINUTES = 15


class MemberIn(BaseModel):
    username: str
    role: str = Field("editor", pattern="^(viewer|editor|owner)$")


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    target_type: str = "project"       # project | alignment | contour_set | tin | section_set | point | location
    target_id: str = ""
    parent_id: str | None = None
    x: float | None = None
    y: float | None = None
    chainage: float | None = None


class CommentPatch(BaseModel):
    text: str | None = None
    resolved: bool | None = None


class VersionRestore(BaseModel):
    note: str = ""


# ---------------------------------------------------------------- members
@router.get("/members")
def list_members(project_id: str, db: AppDB = Depends(get_db), p: dict = Depends(get_project), _: dict = Depends(current_user)):
    return db.members(project_id)


@router.post("/members", status_code=201)
def add_member(project_id: str, body: MemberIn, db: AppDB = Depends(get_db), p: dict = Depends(get_project), user: dict = Depends(require_editor)):
    u = db.get_user_by_name(body.username)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    db.add_member(project_id, u["id"], body.role)
    db.log(project_id, user, "member_added", "user", u["id"], {"username": u["username"], "role": body.role})
    return db.members(project_id)


@router.delete("/members/{user_id}")
def remove_member(project_id: str, user_id: str, db: AppDB = Depends(get_db), p: dict = Depends(get_project), user: dict = Depends(require_editor)):
    if p.get("owner_id") == user_id:
        raise HTTPException(status_code=400, detail="the owner cannot be removed")
    db.remove_member(project_id, user_id)
    db.log(project_id, user, "member_removed", "user", user_id)
    return db.members(project_id)


# ---------------------------------------------------------------- comments
@router.get("/comments")
def list_comments(project_id: str, target_type: str | None = None, target_id: str | None = None, include_resolved: bool = True,
                  db: AppDB = Depends(get_db), p: dict = Depends(get_project), _: dict = Depends(current_user)):
    return db.comments(project_id, target_type, target_id, include_resolved)


@router.post("/comments", status_code=201)
def add_comment(project_id: str, body: CommentIn, db: AppDB = Depends(get_db), p: dict = Depends(get_project), user: dict = Depends(current_user)):
    c = db.add_comment(project_id, user, body.text, body.target_type, body.target_id, body.parent_id, body.x, body.y, body.chainage)
    db.log(project_id, user, "comment", body.target_type, body.target_id, {"comment_id": c["id"], "text": body.text[:120]})
    return c


@router.patch("/comments/{comment_id}")
def patch_comment(project_id: str, comment_id: str, body: CommentPatch, db: AppDB = Depends(get_db), p: dict = Depends(get_project), user: dict = Depends(current_user)):
    c = db.get_comment(comment_id)
    if not c or c["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="comment not found")
    if body.text is not None and c["user_id"] not in (user["id"], None) and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="only the author can edit the text")
    out = db.update_comment(comment_id, body.text, body.resolved)
    if body.resolved is not None:
        db.log(project_id, user, "comment_resolved" if body.resolved else "comment_reopened", c["target_type"], c["target_id"], {"comment_id": comment_id})
    return out


@router.delete("/comments/{comment_id}", status_code=204)
def delete_comment(project_id: str, comment_id: str, db: AppDB = Depends(get_db), p: dict = Depends(get_project), user: dict = Depends(current_user)):
    c = db.get_comment(comment_id)
    if not c or c["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="comment not found")
    if c["user_id"] not in (user["id"], None) and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="only the author can delete a comment")
    db.delete_comment(comment_id)
    return None


# ---------------------------------------------------------------- activity (polled by the browser)
@router.get("/activity")
def activity(project_id: str, since: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
             db: AppDB = Depends(get_db), p: dict = Depends(get_project), _: dict = Depends(current_user)):
    return {"activity": db.activity(project_id, since, limit), "locks": db.locks(project_id)}


# ---------------------------------------------------------------- locks (turn-by-turn editing)
@router.get("/locks")
def list_locks(project_id: str, db: AppDB = Depends(get_db), p: dict = Depends(get_project), _: dict = Depends(current_user)):
    return db.locks(project_id)


@router.post("/alignments/{alignment_id}/lock")
def acquire_alignment_lock(project_id: str, alignment_id: int, minutes: int = Query(LOCK_MINUTES, ge=1, le=240),
                           db: AppDB = Depends(get_db), p: dict = Depends(get_project), store: ProjectStore = Depends(get_store),
                           user: dict = Depends(require_editor)):
    if store.get_alignment(alignment_id) is None:
        raise HTTPException(status_code=404, detail="alignment not found")
    ok, lock = db.acquire_lock(project_id, "alignment", alignment_id, user, minutes)
    if not ok:
        raise HTTPException(status_code=409, detail=f"alignment is being edited by {lock['username']} until {lock['expires']}")
    db.log(project_id, user, "lock_acquired", "alignment", alignment_id)
    return lock


@router.delete("/alignments/{alignment_id}/lock")
def release_alignment_lock(project_id: str, alignment_id: int, force: bool = False, db: AppDB = Depends(get_db), p: dict = Depends(get_project),
                           user: dict = Depends(require_editor)):
    if force and user["role"] != "admin" and p.get("owner_id") != user["id"]:
        raise HTTPException(status_code=403, detail="only the project owner or an admin can force-release")
    released = db.release_lock(project_id, "alignment", alignment_id, user, force=force)
    if released:
        db.log(project_id, user, "lock_released", "alignment", alignment_id, {"force": force})
    return {"released": released}


# ---------------------------------------------------------------- alignment history
@router.get("/alignments/{alignment_id}/versions")
def alignment_versions(project_id: str, alignment_id: int, db: AppDB = Depends(get_db), p: dict = Depends(get_project), _: dict = Depends(current_user)):
    return db.alignment_versions(project_id, alignment_id)


@router.get("/alignments/{alignment_id}/versions/{version}")
def alignment_version(project_id: str, alignment_id: int, version: int, db: AppDB = Depends(get_db), p: dict = Depends(get_project), _: dict = Depends(current_user)):
    v = db.get_alignment_version(project_id, alignment_id, version)
    if not v:
        raise HTTPException(status_code=404, detail="version not found")
    return v


@router.post("/alignments/{alignment_id}/versions/{version}/restore", response_model=AlignmentOut)
def restore_alignment_version(project_id: str, alignment_id: int, version: int, body: VersionRestore, db: AppDB = Depends(get_db),
                              p: dict = Depends(get_project), store: ProjectStore = Depends(get_store), user: dict = Depends(require_editor)):
    v = db.get_alignment_version(project_id, alignment_id, version)
    if not v:
        raise HTTPException(status_code=404, detail="version not found")
    lock = db.get_lock(project_id, "alignment", alignment_id)
    if lock and lock["user_id"] != user["id"]:
        raise HTTPException(status_code=409, detail=f"alignment is being edited by {lock['username']}")
    out = services.save_alignment(store, v["data"], alignment_id=alignment_id)
    db.add_alignment_version(project_id, alignment_id, v["data"], user, body.note or f"restored version {version}")
    db.log(project_id, user, "alignment_restored", "alignment", alignment_id, {"version": version})
    return AlignmentOut(**out)
