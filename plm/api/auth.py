"""Session-cookie authentication (HMAC signed token, PBKDF2 passwords).

Disabled by default for development (PLM_AUTH_ENABLED=0); when enabled every /api route except
/api/auth/* and /api/health requires a valid session. Create users with `python -m plm.admin`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

from fastapi import Depends, HTTPException, Request, Response

from .config import Settings
from .db import AppDB

COOKIE = "plm_session"


def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(secret: str, user_id: str, hours: int) -> str:
    exp = int(time.time()) + hours * 3600
    payload = f"{user_id}:{exp}"
    b = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{b}.{_sign(secret, payload)}"


def parse_token(secret: str, token: str) -> str | None:
    try:
        b, sig = token.split(".", 1)
        pad = "=" * (-len(b) % 4)
        payload = base64.urlsafe_b64decode(b + pad).decode()
        if not hmac.compare_digest(_sign(secret, payload), sig):
            return None
        user_id, exp = payload.rsplit(":", 1)
        if int(exp) < time.time():
            return None
        return user_id
    except Exception:  # noqa: BLE001
        return None


def set_session_cookie(resp: Response, settings: Settings, user_id: str, secure: bool) -> None:
    resp.set_cookie(
        COOKIE, make_token(settings.secret_key or "", user_id, settings.session_hours),
        max_age=settings.session_hours * 3600, httponly=True, samesite="lax", secure=secure, path="/",
    )


def clear_session_cookie(resp: Response) -> None:
    resp.delete_cookie(COOKIE, path="/")


ANONYMOUS = {"id": "anonymous", "username": "anonymous", "role": "admin", "organisation": "", "authenticated": False}


def current_user(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    db: AppDB = request.app.state.db
    token = request.cookies.get(COOKIE) or (request.headers.get("authorization", "").removeprefix("Bearer ").strip() or None)
    user = None
    if token:
        uid = parse_token(settings.secret_key or "", token)
        if uid:
            user = db.get_user(uid)
            if user and user.get("disabled"):
                user = None
    if user:
        return {"id": user["id"], "username": user["username"], "role": user["role"],
                "organisation": user.get("organisation", ""), "authenticated": True}
    if not settings.auth_enabled:
        return ANONYMOUS
    raise HTTPException(status_code=401, detail="authentication required")


def require_editor(user: dict = Depends(current_user)) -> dict:
    if user["role"] not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail="editor role required")
    return user


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return user
