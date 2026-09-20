"""Asset catalogue (library): find terrain models and designs by place, text and tag; clone them."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .. import catalogue
from ..auth import current_user
from ..db import AppDB
from ..deps import get_db, get_settings, guest_check
from ..services import ServiceError

router = APIRouter(tags=["catalogue"])


@router.get("/catalogue")
def list_catalogue(kind: str | None = Query(None, pattern="^(tin|design)$"), q: str | None = None, tag: str | None = None,
                   bbox: str | None = Query(None, description="west,south,east,north in lon/lat"),
                   db: AppDB = Depends(get_db), user: dict = Depends(current_user)):
    box = None
    if bbox:
        try:
            box = [float(v) for v in bbox.split(",")]
            assert len(box) == 4
        except (ValueError, AssertionError):
            raise HTTPException(status_code=400, detail="bbox must be west,south,east,north")
    return catalogue.list_items(db, user, kind, q, box, tag)


class CloneIn(BaseModel):
    name: str | None = None


@router.post("/catalogue/{item_id}/clone", status_code=201)
def clone_item(item_id: str, body: CloneIn | None = None, db: AppDB = Depends(get_db), settings=Depends(get_settings),
               user: dict = Depends(current_user)):
    """Copy the asset's project into a new project owned by the caller."""
    item = db.catalogue_get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="catalogue item not found")
    p = db.get_project(item["project_id"])
    if p is None:
        raise HTTPException(status_code=404, detail="source project not found")
    from ..deps import project_role

    if project_role(db, p, user) is None:
        raise HTTPException(status_code=403, detail="you do not have access to this asset")
    guest_check(settings, db, user, new_project=True)
    try:
        return catalogue.clone_into_project(settings, db, item, user, (body or CloneIn()).name)
    except ServiceError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
