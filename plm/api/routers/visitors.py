"""Visitor register and usage log.

`/api/visitor/me` tells the browser whether this is a first visit and whether to put the
introduction form up; `/api/visitor/intake` stores the answers against the visitor's address. The
`/api/visitor/admin/...` routes are the register itself - who has used the service and what they
did - readable and downloadable by an administrator.
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..auth import current_user, require_admin
from ..db import AppDB
from ..deps import get_db, get_settings
from ..schemas import VisitorIntakeIn, VisitorOut, VisitorStateOut
from ..visitors import ACTION_LABELS, needs_intake, resolve_visitor, set_visitor_cookie

router = APIRouter(prefix="/visitor", tags=["visitors"])


def _public(v: dict | None) -> VisitorOut | None:
    if not v:
        return None
    return VisitorOut(**{k: v[k] for k in VisitorOut.model_fields if v.get(k) is not None})


def _visitor(request: Request, db: AppDB, settings) -> dict | None:
    """The visitor the middleware attached, resolved on demand if the middleware was not reached."""
    v = getattr(request.state, "visitor", None)
    if v is None and settings.visitor_tracking:
        v, _ = resolve_visitor(request, db, settings)
    return v


@router.get("/me", response_model=VisitorStateOut)
def me(request: Request, db: AppDB = Depends(get_db), settings=Depends(get_settings),
       user: dict = Depends(current_user)):
    v = _visitor(request, db, settings)  # the middleware has already issued the cookie
    prefill: dict[str, str] = {}
    if user.get("authenticated"):
        row = db.get_user(user["id"]) or {}
        prefill = {"name": row.get("full_name") or user.get("username", ""), "email": row.get("email") or "",
                   "organisation": row.get("organisation") or ""}
    return VisitorStateOut(
        tracking=settings.visitor_tracking, intake_enabled=settings.visitor_intake,
        intake_required=settings.visitor_intake_required,
        first_visit=bool(v and v.get("new")),
        needs_intake=needs_intake(v, settings), visitor=_public(v), prefill=prefill)


@router.post("/intake", response_model=VisitorStateOut)
def intake(body: VisitorIntakeIn, request: Request, response: Response, db: AppDB = Depends(get_db),
           settings=Depends(get_settings), user: dict = Depends(current_user)):
    """Store the introduction the visitor gave. Answering again simply updates the record."""
    v = _visitor(request, db, settings)
    if v is None:
        raise HTTPException(status_code=409, detail="visitor tracking is switched off on this server")
    uid = user["id"] if user.get("authenticated") else None
    v = db.register_visitor(v["id"], body.model_dump(), user_id=uid) or v
    set_visitor_cookie(response, settings, v["id"], request.url.scheme == "https")
    return VisitorStateOut(tracking=True, intake_enabled=settings.visitor_intake,
                           intake_required=settings.visitor_intake_required, first_visit=False,
                           needs_intake=False, visitor=_public(v))


@router.post("/skip")
def skip(request: Request, db: AppDB = Depends(get_db), settings=Depends(get_settings)):
    """"Not now" - the form is not shown again until `PLM_VISITOR_INTAKE_REPEAT_DAYS` have passed."""
    v = _visitor(request, db, settings)
    if v:
        db.decline_visitor(v["id"])
    return {"ok": True, "repeat_days": settings.visitor_intake_repeat_days}


# ------------------------------------------------------------------ administration
@router.get("/admin/summary")
def summary(db: AppDB = Depends(get_db), _: dict = Depends(require_admin), days: int = 30):
    return {**db.visitor_counts(), "by_action": db.usage_by_action(days), "days": days,
            "labels": ACTION_LABELS}


@router.get("/admin/visitors")
def visitor_list(db: AppDB = Depends(get_db), _: dict = Depends(require_admin), registered: bool | None = None,
                 q: str = "", limit: int = 500):
    return db.list_visitors(registered=registered, q=q, limit=min(limit, 2000))


@router.get("/admin/usage")
def usage(db: AppDB = Depends(get_db), _: dict = Depends(require_admin), visitor_id: str = "", project_id: str = "",
          action: str = "", limit: int = 200):
    return {"rows": db.usage_log(visitor_id=visitor_id, project_id=project_id, action=action, limit=min(limit, 5000)),
            "labels": ACTION_LABELS}


def _csv(rows: list[dict], columns: list[str], filename: str) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for r in rows:
        w.writerow([r.get(c, "") for c in columns])
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/admin/visitors.csv")
def visitors_csv(db: AppDB = Depends(get_db), _: dict = Depends(require_admin)):
    cols = ["ip", "name", "email", "phone", "designation", "organisation", "district", "purpose",
            "registered", "visits", "actions", "first_seen", "last_seen", "user_id", "notes"]
    return _csv(db.list_visitors(limit=5000), cols, "plm-visitors.csv")


@router.get("/admin/usage.csv")
def usage_csv(db: AppDB = Depends(get_db), _: dict = Depends(require_admin), visitor_id: str = "", action: str = ""):
    rows = db.usage_log(visitor_id=visitor_id, action=action, limit=20000)
    cols = ["created", "ip", "visitor_name", "username", "action", "label", "module", "project_id",
            "target_id", "ms", "status"]
    return _csv(rows, cols, "plm-usage-log.csv")
