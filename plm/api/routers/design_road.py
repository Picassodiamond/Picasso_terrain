"""Road design module API: horizontal / vertical alignment, templates, corridor, structures, standards.

All routes live under /projects/{project_id}/designs/{design_id}/road. Reads need the viewer role,
writes the editor role; the design must belong to the road module.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import road, road_output
from ..db import AppDB
from ..deps import get_db, get_project, get_settings, get_store, heavy, no_guest, raise_service, require_project
from ..gpkg import ProjectStore
from ..services import ServiceError

router = APIRouter(prefix="/projects/{project_id}/designs/{design_id}/road", tags=["road design"])


def get_design(design_id: int, store: ProjectStore = Depends(get_store)) -> dict:
    d = store.get_design(design_id)
    if d is None:
        raise HTTPException(status_code=404, detail="design not found")
    if d["module"] != "road":
        raise HTTPException(status_code=400, detail=f"design {design_id} is a {d['module']} design, not a road design")
    return d


class IPBody(BaseModel):
    x: float
    y: float
    radius: float = Field(0.0, ge=0)
    transition: float = Field(0.0, ge=0)
    label: str = ""


class HorizontalIn(BaseModel):
    ips: list[IPBody]
    start_chainage: float = 0.0
    follow: str = "keep"      # keep | stretch | refit | trim: what happens to the grade line


class PVIBody(BaseModel):
    chainage: float
    elevation: float
    length: float = Field(0.0, ge=0)
    label: str = ""


class VerticalIn(BaseModel):
    pvis: list[PVIBody]
    source: str = "manual"


class AutoVerticalIn(BaseModel):
    spacing: float = Field(200.0, gt=0)
    curve_length: float | None = Field(None, ge=0)


class FollowIn(BaseModel):
    mode: str = "stretch"    # stretch | refit | trim
    spacing: float | None = Field(None, gt=0)


class TemplatesIn(BaseModel):
    templates: list[dict[str, Any]]
    assignments: list[dict[str, Any]] = Field(default_factory=list)
    superelevation: dict[str, Any] = Field(default_factory=dict)


class CorridorIn(BaseModel):
    interval: float = Field(20.0, gt=0)
    prismoidal: bool = False
    cut_factor: float = Field(1.0, gt=0)
    fill_factor: float = Field(1.0, gt=0)
    swath: float | None = Field(None, gt=0)
    extra_chainages: list[float] = Field(default_factory=list)


class StructuresIn(BaseModel):
    structures: list[dict[str, Any]]


class SuggestIn(BaseModel):
    walls: bool = True
    culverts: bool = True
    drains: bool = False       # longitudinal drains: side, catch water and toe drains
    outlets: bool = True       # side-drain outlets every 500 m (NRS 2070 cl. 13.8 i)
    soil: str | None = None    # sandy | clayey, for the lining rule of NRS 2070 Table 13-3
    max_fill_height: float | None = Field(None, gt=0)
    max_cut_depth: float | None = Field(None, gt=0)


class SheetSettingsIn(BaseModel):
    template: str | None = None
    paper: str | None = None
    plan_scale: float | None = Field(None, gt=0)
    profile_h_scale: float | None = Field(None, gt=0)
    profile_v_scale: float | None = Field(None, gt=0)
    section_scale: float | None = Field(None, gt=0)
    chainage_interval: float | None = Field(None, gt=0)
    swath: float | None = Field(None, gt=0)
    rotate: str | None = None
    fields: dict[str, str] | None = None
    kinds: list[str] | None = None
    notes: list[str] | None = None


def _svc(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ServiceError as e:
        raise raise_service(e)


@router.get("")
def overview(project_id: str, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store), _: dict = Depends(require_project("viewer"))):
    return _svc(road.overview, store, design)


# ---------------------------------------------------------------- horizontal
@router.get("/horizontal")
def get_horizontal(project_id: str, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store), _: dict = Depends(require_project("viewer"))):
    doc = _svc(road.horizontal_doc, store, design)
    if doc is None:
        return {"ips": [], "is_valid": False, "geometry": None, "table": [], "checks": [], "message": "no horizontal alignment yet: add IPs or seed from a project alignment"}
    return _svc(road.horizontal_payload, store, design, doc)


@router.put("/horizontal")
def put_horizontal(project_id: str, body: HorizontalIn, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store),
                   db: AppDB = Depends(get_db), user: dict = Depends(require_project("editor"))):
    out = _svc(road.put_horizontal, store, design, body.model_dump())
    db.log(project_id, user, "road_horizontal_saved", "design", str(design["id"]), {"ips": len(body.ips)})
    return out


@router.post("/horizontal/preview")
def preview_horizontal(project_id: str, body: HorizontalIn, ground: bool = Query(False, description="also return the ground profile under the preview"),
                       design: dict = Depends(get_design), store: ProjectStore = Depends(get_store), _: dict = Depends(require_project("viewer"))):
    """Geometry, curve table and checks for an alignment that is being edited (nothing is stored)."""
    doc = {"ips": [p.model_dump() for p in body.ips], "start_chainage": body.start_chainage}
    out = _svc(road.horizontal_payload, store, design, doc)
    if ground:
        out["ground"] = road.preview_ground(store, design, doc)
    return out


@router.get("/ground")
def ground(project_id: str, interval: float = Query(10.0, gt=0), design: dict = Depends(get_design), store: ProjectStore = Depends(get_store),
           _: dict = Depends(require_project("viewer"))):
    doc = _svc(road.horizontal_doc, store, design)
    if doc is None:
        raise HTTPException(status_code=400, detail="define the horizontal alignment first")
    al = _svc(road.horizontal_from_doc, doc)
    return {"points": _svc(road.ground_profile, store, design, al, interval), "start_chainage": al.start_chainage, "end_chainage": al.end_chainage}


# ---------------------------------------------------------------- vertical
@router.get("/vertical")
def get_vertical(project_id: str, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store), _: dict = Depends(require_project("viewer"))):
    doc = store.design_get(design["id"], "vertical")
    if doc is None:
        return {"pvis": [], "is_valid": False, "line": [], "table": [], "checks": [], "message": "no vertical alignment yet: fit to ground or add PVIs"}
    hdoc = _svc(road.horizontal_doc, store, design)
    return _svc(road.vertical_payload, store, design, doc, road.horizontal_from_doc(hdoc) if hdoc else None)


@router.put("/vertical")
def put_vertical(project_id: str, body: VerticalIn, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store),
                 db: AppDB = Depends(get_db), user: dict = Depends(require_project("editor"))):
    out = _svc(road.put_vertical, store, design, body.model_dump())
    db.log(project_id, user, "road_vertical_saved", "design", str(design["id"]), {"pvis": len(body.pvis)})
    return out


@router.post("/vertical/auto")
def auto_vertical(project_id: str, body: AutoVerticalIn | None = None, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store),
                  db: AppDB = Depends(get_db), user: dict = Depends(require_project("editor"))):
    b = body or AutoVerticalIn()
    out = _svc(road.auto_vertical, store, design, b.spacing, b.curve_length)
    db.log(project_id, user, "road_vertical_fitted", "design", str(design["id"]), {"spacing": b.spacing})
    return out


@router.post("/vertical/follow")
def follow_vertical(project_id: str, body: FollowIn | None = None, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store),
                    db: AppDB = Depends(get_db), user: dict = Depends(require_project("editor"))):
    """Bring the grade line up to date with the current alignment: stretch (keep positions and cut / fill
    depths), refit (fit to ground again) or trim (clip / extend to the new range)."""
    b = body or FollowIn()
    out = _svc(road.follow_vertical, store, design, b.mode, b.spacing)
    if out is None:
        raise HTTPException(status_code=400, detail="there is no grade line to update yet")
    db.log(project_id, user, "road_vertical_followed", "design", str(design["id"]), {"mode": b.mode})
    return out


# ---------------------------------------------------------------- templates
@router.get("/templates")
def get_templates(project_id: str, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store), _: dict = Depends(require_project("viewer"))):
    doc = _svc(road.templates_doc, store, design)
    return _svc(road.templates_payload, store, design, doc)


@router.put("/templates")
def put_templates(project_id: str, body: TemplatesIn, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store),
                  db: AppDB = Depends(get_db), user: dict = Depends(require_project("editor"))):
    out = _svc(road.put_templates, store, design, body.model_dump())
    db.log(project_id, user, "road_templates_saved", "design", str(design["id"]), {"templates": len(body.templates)})
    return out


# ---------------------------------------------------------------- corridor / earthworks
@router.post("/corridor", dependencies=[Depends(heavy)])
def build_corridor(project_id: str, body: CorridorIn | None = None, request: Request = None, design: dict = Depends(get_design),
                   store: ProjectStore = Depends(get_store), db: AppDB = Depends(get_db), user: dict = Depends(require_project("editor"))):
    """Sweep the templates along the alignment: sections, cut / fill areas, volumes and mass haul."""
    b = body or CorridorIn()
    out = _svc(road.build_corridor_run, store, design, b.model_dump())
    db.log(project_id, user, "road_corridor_built", "design", str(design["id"]), {"sections": out["summary"]["sections"], **{k: round(v) for k, v in out["summary"]["totals"].items() if isinstance(v, (int, float))}})
    return out


@router.get("/corridor")
def get_corridor(project_id: str, full: bool = False, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store),
                 _: dict = Depends(require_project("viewer"))):
    out = _svc(road.corridor_latest, store, design, full)
    if out is None:
        raise HTTPException(status_code=404, detail="no corridor has been built yet")
    return out


@router.get("/corridor/section")
def get_section(project_id: str, chainage: float, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store),
                _: dict = Depends(require_project("viewer"))):
    s = _svc(road.corridor_section, store, design, chainage)
    if s is None:
        raise HTTPException(status_code=404, detail="no corridor has been built yet")
    return s


@router.get("/corridor/volumes.csv")
def volumes_csv(project_id: str, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store), _: dict = Depends(require_project("viewer"))):
    from fastapi.responses import PlainTextResponse

    out = _svc(road.corridor_latest, store, design, False)
    if out is None:
        raise HTTPException(status_code=404, detail="no corridor has been built yet")
    rows = ["from,to,length,cut_area_from,cut_area_to,fill_area_from,fill_area_to,cut_volume,fill_volume,cumulative"]
    secs = {round(s["chainage"], 3): s for s in out["sections"]}
    for v, m in zip(out["volumes"], out["mass_haul"][1:]):
        a, b = secs.get(round(v["from"], 3), {}), secs.get(round(v["to"], 3), {})
        rows.append(f"{v['from']:.2f},{v['to']:.2f},{v['length']:.2f},{a.get('cut_area', 0):.3f},{b.get('cut_area', 0):.3f},{a.get('fill_area', 0):.3f},{b.get('fill_area', 0):.3f},{v['cut']:.2f},{v['fill']:.2f},{m['cumulative']:.2f}")
    return PlainTextResponse("\n".join(rows) + "\n", media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{design["name"]}_volumes.csv"'})


# ---------------------------------------------------------------- structures
@router.get("/structures")
def get_structures(project_id: str, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store), _: dict = Depends(require_project("viewer"))):
    """Structures with the catalogue of wall, drain and culvert types, the bill of quantities and the checks."""
    return _svc(road.structures_payload, store, design)


@router.put("/structures")
def put_structures(project_id: str, body: StructuresIn, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store),
                   db: AppDB = Depends(get_db), user: dict = Depends(require_project("editor"))):
    out = _svc(road.put_structures, store, design, body.structures)
    db.log(project_id, user, "road_structures_saved", "design", str(design["id"]), {"structures": len(out)})
    return _svc(road.structures_payload, store, design, out)


@router.post("/structures/suggest")
def suggest_structures(project_id: str, body: SuggestIn | None = None, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store),
                       _: dict = Depends(require_project("viewer"))):
    """Wall and culvert suggestions from the latest corridor and the terrain's stream lines (nothing is stored)."""
    return {"suggestions": _svc(road.suggest_structures, store, design, (body or SuggestIn()).model_dump())}


# ---------------------------------------------------------------- standards
@router.get("/standards")
def standards(project_id: str, design: dict = Depends(get_design), _: dict = Depends(require_project("viewer"))):
    return _svc(road.standards_payload, design)


# ---------------------------------------------------------------- drawings and data exports
@router.get("/sheets")
def sheets(project_id: str, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store), p: dict = Depends(get_project),
           user: dict = Depends(require_project("viewer"))):
    """Sheet settings (paper, scales, title block) and the list of plan / profile / cross-section sheets the design produces."""
    return road_output.sheets_index(store, design, p, user)


@router.put("/sheets/settings")
def put_sheet_settings(project_id: str, body: SheetSettingsIn, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store),
                       p: dict = Depends(get_project), db: AppDB = Depends(get_db), user: dict = Depends(require_project("editor"))):
    _svc(road_output.put_settings, store, design, body.model_dump(exclude_none=True))
    db.log(project_id, user, "road_sheets_settings", "design", str(design["id"]), {})
    return road_output.sheets_index(store, design, p, user)


@router.get("/sheets/{kind}/{index}.svg")
def sheet_svg(project_id: str, kind: str, index: int, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store),
              p: dict = Depends(get_project), user: dict = Depends(require_project("viewer"))):
    """Preview of one sheet (1-based index) as SVG - the same primitives the DXF export writes."""
    from fastapi.responses import Response

    svg = _svc(road_output.sheet_svg, store, design, p, kind, index - 1, user)
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@router.get("/export/sheets.dxf", dependencies=[Depends(no_guest)])
def export_sheets_dxf(project_id: str, kinds: str | None = Query(None, description="comma separated: plan,profile,sections"), design: dict = Depends(get_design),
                      store: ProjectStore = Depends(get_store), p: dict = Depends(get_project), settings=Depends(get_settings), db: AppDB = Depends(get_db),
                      user: dict = Depends(require_project("viewer"))):
    """Drawing sheets for AutoCAD: model space in mm (one row per kind) and a paper-space layout per sheet."""
    from fastapi.responses import FileResponse

    ks = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else None
    out = settings.project_dir(project_id) / f"{road_output.safe_name(design['name'])}_sheets.dxf"
    _svc(road_output.sheets_dxf, store, design, p, out, ks, user)
    db.log(project_id, user, "road_export", "design", str(design["id"]), {"what": "sheets.dxf", "kinds": ks})
    return FileResponse(out, media_type="application/dxf", filename=out.name)


@router.get("/export/model.dxf", dependencies=[Depends(no_guest)])
def export_model_dxf(project_id: str, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store), p: dict = Depends(get_project),
                     settings=Depends(get_settings), db: AppDB = Depends(get_db), user: dict = Depends(require_project("viewer"))):
    """The design in project coordinates (m): centreline, IPs, chainages, 3-D design lines, sections, structures."""
    from fastapi.responses import FileResponse

    out = settings.project_dir(project_id) / f"{road_output.safe_name(design['name'])}_model.dxf"
    _svc(road_output.design_model_dxf, store, design, p, out, user)
    db.log(project_id, user, "road_export", "design", str(design["id"]), {"what": "model.dxf"})
    return FileResponse(out, media_type="application/dxf", filename=out.name)


@router.get("/export/design.xlsx", dependencies=[Depends(no_guest)])
def export_xlsx(project_id: str, design: dict = Depends(get_design), store: ProjectStore = Depends(get_store), p: dict = Depends(get_project),
                db: AppDB = Depends(get_db), user: dict = Depends(require_project("viewer"))):
    """Excel workbook: horizontal, superelevation, vertical, levels, sections, volumes (live factors), mass haul, section points, structures, checks, standard."""
    from fastapi.responses import Response

    data = _svc(road_output.workbook, store, design, p, user)
    db.log(project_id, user, "road_export", "design", str(design["id"]), {"what": "design.xlsx"})
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{road_output.safe_name(design["name"])}_design.xlsx"'})
