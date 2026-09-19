from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..auth import clear_session_cookie, current_user, require_admin, set_session_cookie
from ..db import AppDB
from ..deps import get_db, get_settings
from ..schemas import LoginIn, RegisterIn, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, response: Response, db: AppDB = Depends(get_db), settings=Depends(get_settings)):
    user = db.get_user_by_name(body.username)
    if not user or user.get("disabled") or not AppDB.verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid username or password")
    secure = request.url.scheme == "https"
    set_session_cookie(response, settings, user["id"], secure)
    return UserOut(id=user["id"], username=user["username"], role=user["role"], organisation=user.get("organisation", ""))


@router.post("/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: dict = Depends(current_user)):
    return UserOut(**user)


@router.get("/users")
def list_users(db: AppDB = Depends(get_db), _: dict = Depends(require_admin)):
    return db.list_users()


@router.get("/status")
def status(request: Request, settings=Depends(get_settings), db: AppDB = Depends(get_db)):
    return {"auth_enabled": settings.auth_enabled, "open_registration": settings.open_registration or db.count_users() == 0,
            "users": db.count_users(), "version": settings.version}


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: RegisterIn, request: Request, response: Response, db: AppDB = Depends(get_db), settings=Depends(get_settings)):
    """Self-registration. The first user becomes admin; later users need open registration enabled."""
    first = db.count_users() == 0
    if not first and not settings.open_registration:
        raise HTTPException(status_code=403, detail="registration is closed; ask an administrator for an account")
    if db.get_user_by_name(body.username):
        raise HTTPException(status_code=409, detail="username already taken")
    user = db.create_user(body.username, body.password, "admin" if first else "editor", body.organisation)
    set_session_cookie(response, settings, user["id"], request.url.scheme == "https")
    return UserOut(id=user["id"], username=user["username"], role=user["role"], organisation=user.get("organisation", ""))
