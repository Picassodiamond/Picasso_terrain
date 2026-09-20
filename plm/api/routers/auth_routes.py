from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..auth import GUEST_COOKIE, clear_session_cookie, current_user, guest_id_from_request, require_admin, set_session_cookie
from ..db import AppDB
from ..deps import get_db, get_settings
from ..schemas import LoginIn, RegisterIn, UserCreateIn, UserOut, UserPatchIn


def _claim(request: Request, response: Response, db: AppDB, settings, user_id: str) -> int:
    """Move a guest's sandbox projects to the account that signed in / registered."""
    gid = guest_id_from_request(request, settings)
    n = db.claim_guest_projects(gid, user_id) if gid else 0
    if gid:
        response.delete_cookie(GUEST_COOKIE, path="/")
    return n

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, response: Response, db: AppDB = Depends(get_db), settings=Depends(get_settings)):
    user = db.get_user_by_name(body.username)
    if not user or user.get("disabled") or not AppDB.verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid username or password")
    secure = request.url.scheme == "https"
    set_session_cookie(response, settings, user["id"], secure)
    n = _claim(request, response, db, settings, user["id"])
    return UserOut(id=user["id"], username=user["username"], role=user["role"], organisation=user.get("organisation", ""), claimed_projects=n)


@router.post("/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: dict = Depends(current_user), settings=Depends(get_settings), db: AppDB = Depends(get_db)):
    out = UserOut(**{k: v for k, v in user.items() if k in UserOut.model_fields})
    if user.get("authenticated"):
        row = db.get_user(user["id"]) or {}
        out.full_name = row.get("full_name") or ""
        out.email = row.get("email") or ""
    if user.get("guest"):
        owned = db.projects_owned_by(user["id"])
        out.quota = {"max_points": settings.guest_max_points, "max_projects": settings.guest_max_projects,
                     "max_tin_runs": settings.guest_max_tin_runs, "ttl_days": settings.guest_ttl_days, "projects": len(owned)}
    return out


@router.get("/users")
def list_users(db: AppDB = Depends(get_db), _: dict = Depends(require_admin)):
    return db.list_users()


@router.post("/users", status_code=201)
def create_user(body: UserCreateIn, db: AppDB = Depends(get_db), admin: dict = Depends(require_admin)):
    """Invite-only: an administrator creates the account and shares the credentials."""
    if db.get_user_by_name(body.username):
        raise HTTPException(status_code=409, detail="username already taken")
    u = db.create_user(body.username, body.password, body.role, body.organisation, body.full_name, body.email, body.notes, created_by=admin["id"])
    return {k: v for k, v in u.items() if k != "password_hash"}


@router.patch("/users/{user_id}")
def patch_user(user_id: str, body: UserPatchIn, db: AppDB = Depends(get_db), admin: dict = Depends(require_admin)):
    """Change role, details, disable / enable, or set a new password (the admin tells the user)."""
    u = db.get_user(user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="user not found")
    if user_id == admin["id"] and (body.disabled or (body.role and body.role != "admin")):
        raise HTTPException(status_code=400, detail="you cannot disable or demote your own account")
    u2 = db.update_user(user_id, **body.model_dump(exclude_none=True))
    return {k: v for k, v in (u2 or {}).items() if k != "password_hash"}


@router.get("/status")
def status(request: Request, settings=Depends(get_settings), db: AppDB = Depends(get_db)):
    return {"auth_enabled": settings.auth_enabled, "open_registration": settings.open_registration or db.count_users() == 0,
            "guest_enabled": settings.auth_enabled and settings.guest_enabled,
            "guest_quota": {"max_points": settings.guest_max_points, "max_projects": settings.guest_max_projects, "ttl_days": settings.guest_ttl_days},
            "users": db.count_users(), "version": settings.version}


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: RegisterIn, request: Request, response: Response, db: AppDB = Depends(get_db), settings=Depends(get_settings)):
    """Bootstrap only: the first user becomes admin. Afterwards accounts are created by an administrator
    (invite-only), unless PLM_OPEN_REGISTRATION is set for a public deployment."""
    first = db.count_users() == 0
    if not first and not settings.open_registration:
        raise HTTPException(status_code=403, detail="accounts are created by an administrator - ask for your username and password")
    if db.get_user_by_name(body.username):
        raise HTTPException(status_code=409, detail="username already taken")
    user = db.create_user(body.username, body.password, "admin" if first else "editor", body.organisation)
    set_session_cookie(response, settings, user["id"], request.url.scheme == "https")
    n = _claim(request, response, db, settings, user["id"])
    return UserOut(id=user["id"], username=user["username"], role=user["role"], organisation=user.get("organisation", ""), claimed_projects=n)
