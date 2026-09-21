"""Visitor tracking: who is using the service, and what they did.

Two things live here.

**Identity.** Every request that reaches `/api` is attributed to a *visitor* - a person at an IP
address, with or without an account. The visitor is recognised by a long-lived signed cookie
(`plm_visitor`); when the browser brings no cookie the address it connects from is looked up, so a
person who returns from the same office connection is not asked to introduce themselves twice.
Behind a proxy (cPanel / Passenger, nginx) the address comes from `X-Forwarded-For`, otherwise from
the socket.

**Usage log.** A middleware matches the request against `ACTIONS` below - the list of *major*
activities: importing a survey, building a TIN, generating contours, saving an alignment, cutting
sections, designing a road, exporting drawings - and writes one row per action into `usage_log`,
with the visitor, the account (if any), the project and how long the request took. Ordinary reads
(map tiles, panel refreshes) are not logged, so the table stays readable.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from .auth import COOKIE, make_token, parse_token
from .config import Settings
from .db import AppDB

VISITOR_COOKIE = "plm_visitor"

#: (method, path pattern, action key, human label, module). The first match wins.
_ACTIONS: list[tuple[str, str, str, str, str]] = [
    ("POST", r"/api/projects$", "project.create", "Created a project", ""),
    ("DELETE", r"/api/projects/(?P<pid>[^/]+)$", "project.delete", "Deleted a project", ""),
    ("POST", r"/api/projects/(?P<pid>[^/]+)/archive$", "project.archive", "Archived a project", ""),
    ("POST", r"/api/projects/(?P<pid>[^/]+)/restore$", "project.restore", "Restored a project", ""),
    ("POST", r"/api/projects/(?P<pid>[^/]+)/import$", "data.import", "Imported survey data", "terrain"),
    ("POST", r"/api/projects/(?P<pid>[^/]+)/lines$", "data.line", "Drew a breakline", "terrain"),
    ("POST", r"/api/projects/(?P<pid>[^/]+)/constraints/detect$", "constraint.detect", "Detected constraints", "terrain"),
    ("POST", r"/api/projects/(?P<pid>[^/]+)/constraints/accept$", "constraint.accept", "Accepted constraints", "terrain"),
    ("POST", r"/api/projects/(?P<pid>[^/]+)/tin$", "tin.build", "Built a TIN surface", "terrain"),
    ("DELETE", r"/api/projects/(?P<pid>[^/]+)/tin/(?P<id>\d+)$", "tin.delete", "Deleted a TIN run", "terrain"),
    ("POST", r"/api/projects/(?P<pid>[^/]+)/tin/(?P<id>\d+)/profile$", "tin.profile", "Took a ground profile", "terrain"),
    ("POST", r"/api/projects/(?P<pid>[^/]+)/contours$", "contour.generate", "Generated contours", "terrain"),
    ("PATCH", r"/api/projects/(?P<pid>[^/]+)/contours/(?P<id>\d+)$", "contour.style", "Restyled a contour set", "terrain"),
    ("POST", r"/api/projects/(?P<pid>[^/]+)/alignments$", "alignment.create", "Created an alignment", "terrain"),
    ("POST", r"/api/projects/(?P<pid>[^/]+)/alignments/import$", "alignment.import", "Imported an alignment", "terrain"),
    ("PUT", r"/api/projects/(?P<pid>[^/]+)/alignments/(?P<id>\d+)$", "alignment.edit", "Edited an alignment", "terrain"),
    ("POST", r"/api/projects/(?P<pid>[^/]+)/sections$", "section.extract", "Cut cross-sections", "terrain"),
    ("POST", r"/api/projects/(?P<pid>[^/]+)/designs$", "design.create", "Started a design", ""),
    ("PATCH", r"/api/projects/(?P<pid>[^/]+)/designs/(?P<id>\d+)$", "design.edit", "Changed design settings", ""),
    ("PUT", r"/api/projects/(?P<pid>[^/]+)/designs/(?P<id>\d+)/road/horizontal$", "road.horizontal", "Saved the road alignment", "road"),
    ("PUT", r"/api/projects/(?P<pid>[^/]+)/designs/(?P<id>\d+)/road/vertical$", "road.vertical", "Saved the grade line", "road"),
    ("POST", r"/api/projects/(?P<pid>[^/]+)/designs/(?P<id>\d+)/road/vertical/auto$", "road.vertical_auto", "Fitted a grade line", "road"),
    ("PUT", r"/api/projects/(?P<pid>[^/]+)/designs/(?P<id>\d+)/road/templates$", "road.template", "Edited the road template", "road"),
    ("POST", r"/api/projects/(?P<pid>[^/]+)/designs/(?P<id>\d+)/road/corridor$", "road.corridor", "Built the corridor and earthworks", "road"),
    ("PUT", r"/api/projects/(?P<pid>[^/]+)/designs/(?P<id>\d+)/road/structures$", "road.structures", "Edited walls, drains and culverts", "road"),
    ("GET", r"/api/projects/(?P<pid>[^/]+)/designs/(?P<id>\d+)/road/export/sheets\.dxf$", "export.sheets_dxf", "Exported drawing sheets (DXF)", "road"),
    ("GET", r"/api/projects/(?P<pid>[^/]+)/designs/(?P<id>\d+)/road/export/model\.dxf$", "export.model_dxf", "Exported the road model (DXF)", "road"),
    ("GET", r"/api/projects/(?P<pid>[^/]+)/designs/(?P<id>\d+)/road/export/design\.xlsx$", "export.xlsx", "Exported the design workbook (Excel)", "road"),
    ("GET", r"/api/projects/(?P<pid>[^/]+)/designs/(?P<id>\d+)/road/corridor/volumes\.csv$", "export.volumes", "Exported earthwork volumes (CSV)", "road"),
    ("GET", r"/api/projects/(?P<pid>[^/]+)/export\.dxf$", "export.dxf", "Exported the terrain (DXF)", "terrain"),
    ("GET", r"/api/projects/(?P<pid>[^/]+)/export\.gpkg$", "export.gpkg", "Exported the project (GeoPackage)", "terrain"),
    ("GET", r"/api/projects/(?P<pid>[^/]+)/export\.geojson$", "export.geojson", "Exported the project (GeoJSON)", "terrain"),
    ("POST", r"/api/auth/register$", "account.register", "Created an account", ""),
    ("POST", r"/api/auth/login$", "account.login", "Signed in", ""),
    ("POST", r"/api/visitor/intake$", "visitor.intake", "Filled the visitor form", ""),
]

ACTIONS = [(m, re.compile(p), key, label, mod) for m, p, key, label, mod in _ACTIONS]
#: action key -> human label, for the administration screen and the CSV export
ACTION_LABELS = {key: label for _, _, key, label, _ in _ACTIONS}


def match_action(method: str, path: str) -> tuple[str, str, str, str] | None:
    """(action key, label, module, target id) for a major activity, or None for an ordinary request."""
    for m, rx, key, label, mod in ACTIONS:
        if m == method:
            hit = rx.match(path)
            if hit:
                g = hit.groupdict()
                return key, label, mod, g.get("id") or g.get("pid") or ""
    return None


def client_ip(request: Request, settings: Settings) -> str:
    """The visitor's address. Behind a proxy the socket is the proxy, so the first hop of
    `X-Forwarded-For` (or `X-Real-IP`) is used instead; set PLM_TRUST_PROXY=0 when the server is
    exposed directly and the header cannot be trusted."""
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()[:64]
        real = request.headers.get("x-real-ip", "")
        if real:
            return real.strip()[:64]
    return (request.client.host if request.client else "") or "unknown"


def _user_id(request: Request, settings: Settings) -> str | None:
    """The account id from the session cookie, without running the auth dependency (no Response here)."""
    token = request.cookies.get(COOKIE) or (request.headers.get("authorization", "").removeprefix("Bearer ").strip() or None)
    if not token:
        return None
    uid = parse_token(settings.secret_key or "", token)
    return None if not uid or uid.startswith("guest:") else uid


def visitor_id_from_cookie(request: Request, settings: Settings) -> str | None:
    tok = request.cookies.get(VISITOR_COOKIE)
    if not tok:
        return None
    val = parse_token(settings.secret_key or "", tok)
    return val[2:] if val and val.startswith("v:") else None


def set_visitor_cookie(response: Response, settings: Settings, vid: str, secure: bool) -> None:
    days = settings.visitor_cookie_days
    response.set_cookie(VISITOR_COOKIE, make_token(settings.secret_key or "", f"v:{vid}", days * 24),
                        max_age=days * 86400, httponly=True, samesite="lax", secure=secure, path="/")


def _minutes_since(iso: str | None) -> float:
    if not iso:
        return 1e9
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return 1e9
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds() / 60.0


def resolve_visitor(request: Request, db: AppDB, settings: Settings) -> tuple[dict | None, bool]:
    """(visitor record, whether the cookie must be re-issued).

    Cookie first; failing that the address, so a returning person on a known connection keeps their
    record; failing that a new record is created for this address."""
    if not settings.visitor_tracking:
        return None, False
    uid = _user_id(request, settings)
    vid = visitor_id_from_cookie(request, settings)
    ip = client_ip(request, settings)
    v = db.get_visitor(vid) if vid else None
    fresh = False
    if v is None and uid:
        v = db.visitor_for_user(uid)  # the same person on a new device / after clearing cookies
    if v is None:
        v = db.visitor_by_ip(ip)  # first-time check: has this address been here before?
        fresh = v is not None
    if v is None:
        v = db.create_visitor(ip, request.headers.get("user-agent", ""), uid)
        v["new"] = True  # never seen before: this is the visit the form is offered on
        return v, True
    new_visit = _minutes_since(v.get("last_seen")) > settings.visitor_visit_minutes
    if new_visit or ip != v.get("ip") or (uid and not v.get("user_id")):
        db.touch_visitor(v["id"], ip=ip, user_id=uid, new_visit=new_visit)
        v = db.get_visitor(v["id"]) or v
    return v, fresh or vid is None


def needs_intake(v: dict | None, settings: Settings) -> bool:
    """Should the introduction form be put up? Not for someone who already filled it, and not again
    until `visitor_intake_repeat_days` have passed for someone who answered "Not now"."""
    if not settings.visitor_tracking or not settings.visitor_intake or v is None:
        return False
    if v.get("registered"):
        return False
    declined = v.get("declined_at")
    if declined and _minutes_since(declined) < settings.visitor_intake_repeat_days * 1440:
        return False
    return True


async def visitor_middleware(request: Request, call_next):
    """Attribute every /api request to a visitor and log the major ones."""
    app = request.app
    settings: Settings = app.state.settings
    path = request.url.path
    if not settings.visitor_tracking or not path.startswith("/api") or path.startswith("/api/health"):
        return await call_next(request)
    db: AppDB = app.state.db
    try:
        visitor, issue = resolve_visitor(request, db, settings)
    except Exception:  # noqa: BLE001 - tracking must never break a request
        visitor, issue = None, False
    request.state.visitor = visitor
    hit = match_action(request.method, path)
    if (settings.visitor_intake_required and hit and not hit[0].startswith(("account.", "visitor."))
            and needs_intake(visitor, settings)):
        resp = JSONResponse(status_code=428, content={"detail": "please tell us who you are before using this - "
                                                                "the short form takes a moment", "code": "visitor_intake"})
        if visitor and issue:
            set_visitor_cookie(resp, settings, visitor["id"], request.url.scheme == "https")
        return resp
    t0 = time.perf_counter()
    response = await call_next(request)
    logged = hit and response.status_code < 400
    created = ""
    if logged and hit[0] in _ID_IN_BODY:
        response, created = await _read_created_id(response)
    if visitor and issue:
        set_visitor_cookie(response, settings, visitor["id"], request.url.scheme == "https")
    if logged:
        key, label, module, target = hit
        if key == "visitor.intake" and visitor:  # the details were given during this very request
            visitor = db.get_visitor(visitor["id"]) or visitor
        project = _project_from_path(path)
        if key == "project.create":
            project = created
        try:
            user = db.get_user(visitor.get("user_id")) if visitor and visitor.get("user_id") else None
            db.log_usage(key, label=label, visitor=visitor, user=user, module=module, target_id=target or created,
                         project_id=project, method=request.method, path=path, status=response.status_code,
                         ms=int((time.perf_counter() - t0) * 1000))
        except Exception:  # noqa: BLE001
            pass
    return response


#: actions whose subject only exists in the reply (a project or design that has just been created),
#: so the small JSON body is read back to give the log row something to point at
_ID_IN_BODY = {"project.create", "design.create"}


async def _read_created_id(response) -> tuple[object, str]:
    """Drain the reply, pick `id` out of it and hand back an equivalent response to send on."""
    try:
        body = b"".join([chunk async for chunk in response.body_iterator])
    except AttributeError:  # an already-buffered response: nothing to drain
        return response, ""
    new_id = ""
    try:
        if response.headers.get("content-type", "").startswith("application/json"):
            new_id = str(json.loads(body).get("id", "") or "")
    except Exception:  # noqa: BLE001
        new_id = ""
    out = Response(content=body, status_code=response.status_code)
    out.raw_headers = response.raw_headers  # keeps content-length and any Set-Cookie already there
    return out, new_id


_PID = re.compile(r"/api/projects/([^/]+)")


def _project_from_path(path: str) -> str:
    m = _PID.match(path)
    return m.group(1) if m else ""
