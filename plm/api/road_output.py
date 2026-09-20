"""Road design outputs: drawing sheets (preview SVG, DXF), the model-space design DXF and the
Excel workbook. Joins the stores to `plm.design.drawing`."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from ..design.drawing.dxf import model_dxf, sheets_to_dxf
from ..design.drawing.excel import design_workbook
from ..design.drawing.frame import DrawingInputs, SheetSettings, list_templates, resolve_settings
from ..design.drawing.model import PAPERS, Sheet
from ..design.drawing.plan import plan_sheets
from ..design.drawing.profile import profile_sheets
from ..design.drawing.sections import section_sheets
from ..design.drawing.svg import sheet_to_svg
from ..design.horizontal import check_horizontal, curve_table
from ..design.vertical import VerticalAlignment
from . import road
from .gpkg import ProjectStore
from .services import ServiceError

KINDS = ("plan", "profile", "sections")
SETTINGS_KEYS = ("template", "paper", "plan_scale", "profile_h_scale", "profile_v_scale", "section_scale", "chainage_interval", "swath", "rotate", "fields", "kinds", "notes")


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "design"


# ---------------------------------------------------------------------- settings
def settings_doc(store: ProjectStore, design: dict) -> dict:
    return store.design_get(design["id"], "sheets", {}) or {}


def put_settings(store: ProjectStore, design: dict, body: dict) -> dict:
    doc = {k: body[k] for k in SETTINGS_KEYS if k in body and body[k] is not None}
    if "paper" in doc and str(doc["paper"]).upper() not in PAPERS:
        raise ServiceError(f"unknown paper size {doc['paper']!r}; use one of {', '.join(PAPERS)}", 400)
    for k in ("plan_scale", "profile_h_scale", "profile_v_scale", "section_scale", "chainage_interval", "swath"):
        if k in doc:
            try:
                doc[k] = float(doc[k])
            except (TypeError, ValueError):
                raise ServiceError(f"{k} must be a number", 400)
            if doc[k] <= 0:
                raise ServiceError(f"{k} must be positive", 400)
    if "rotate" in doc and doc["rotate"] not in ("along_road", "north_up"):
        raise ServiceError("rotate must be along_road or north_up", 400)
    if "fields" in doc:
        doc["fields"] = {str(k): str(v) for k, v in (doc["fields"] or {}).items()}
    if "kinds" in doc:
        doc["kinds"] = [k for k in doc["kinds"] if k in KINDS]
    store.design_set(design["id"], "sheets", doc)
    return doc


def settings_for(store: ProjectStore, design: dict, project: dict, user: dict | None = None) -> SheetSettings:
    return resolve_settings(settings_doc(store, design), project_name=project.get("name", ""), design_name=design.get("name", ""),
                            organisation=(user or {}).get("organisation", "") or "")


# ---------------------------------------------------------------------- inputs
def drawing_inputs(store: ProjectStore, design: dict, project: dict, *, with_terrain: bool = True) -> DrawingInputs:
    hdoc = road.horizontal_doc(store, design)
    if not hdoc or len(hdoc.get("ips") or []) < 2:
        raise ServiceError("define the horizontal alignment first", 400)
    std, ctx = road.design_context(design)
    al = road.horizontal_from_doc(hdoc)
    checks = [{"stage": "horizontal", **c.as_dict()} for c in check_horizontal(al, std, ctx)]
    vdoc = store.design_get(design["id"], "vertical")
    va = None
    if vdoc and len(vdoc.get("pvis") or []) >= 2:
        va = VerticalAlignment.make(vdoc["pvis"])
        checks += [{"stage": "vertical", **c.as_dict()} for c in va.check(std, ctx)]
    tdoc = road.templates_doc(store, design)
    sp = road.supere_profile(al, std, ctx, tdoc)
    ground: list[tuple[float, float]] = []
    if design.get("tin_run_id") is not None:
        try:
            ground = [(g["chainage"], g["z"]) for g in road.ground_profile(store, design, al, interval=5.0)]
        except ServiceError:
            ground = []
    latest = road.corridor_latest(store, design, full=True) or {}
    contours = []
    if with_terrain and design.get("tin_run_id") is not None:
        sets = store.contour_sets(int(design["tin_run_id"])) or store.contour_sets()
        if sets:
            contours = store.load_contours(sets[-1]["id"])
    crs = project.get("crs_info", {}) if isinstance(project.get("crs_info"), dict) else {}
    return DrawingInputs(
        project_name=project.get("name", ""), design_name=design.get("name", ""), al=al, curve_rows=curve_table(al, std, ctx), va=va, ground=ground,
        sections=latest.get("sections") or [], volumes=latest.get("volumes") or [], mass_haul=latest.get("mass_haul") or [],
        totals=(latest.get("summary") or {}).get("totals") or {}, structures=road.structures_doc(store, design), contours=contours, supere=sp,
        standard=std.summary(), context=ctx, checks=checks, standard_params=road.standards_payload(design)["parameters"], templates=tdoc,
        crs_name=str(crs.get("name") or project.get("crs") or ""), corridor_params=(latest.get("summary") or {}).get("params") or {},
    )


# ---------------------------------------------------------------------- sheets
def build_sheets(inp: DrawingInputs, st: SheetSettings, kinds: tuple[str, ...] | list[str] | None = None, *, with_terrain: bool = True) -> list[Sheet]:
    out: list[Sheet] = []
    for k in kinds or st.kinds:
        try:
            if k == "plan":
                out += plan_sheets(inp, st, with_terrain=with_terrain)
            elif k == "profile":
                out += profile_sheets(inp, st)
            elif k == "sections":
                out += section_sheets(inp, st)
        except ValueError as e:
            raise ServiceError(str(e), 400)
    return out


def sheets_index(store: ProjectStore, design: dict, project: dict, user: dict | None = None) -> dict:
    st = settings_for(store, design, project, user)
    doc = settings_doc(store, design)
    out = {"settings": st.as_dict(), "stored": doc, "papers": {k: list(v) for k, v in PAPERS.items()}, "templates": list_templates(), "sheets": [], "problems": []}
    try:
        inp = drawing_inputs(store, design, project, with_terrain=False)
    except ServiceError as e:
        out["problems"].append(str(e))
        return out
    for k in KINDS:
        try:
            for s in build_sheets(inp, st, [k], with_terrain=False):
                out["sheets"].append(s.summary())
        except ServiceError as e:
            out["problems"].append(f"{k}: {e}")
    if not inp.ground and inp.va is None:
        out["problems"].append("no ground under the alignment and no vertical alignment: profile sheets skipped")
    if not inp.sections:
        out["problems"].append("build the corridor to get cross-section sheets, daylight lines and quantities")
    out["counts"] = {k: sum(1 for s in out["sheets"] if s["kind"] == k) for k in KINDS}
    return out


def sheet_svg(store: ProjectStore, design: dict, project: dict, kind: str, index: int, user: dict | None = None) -> str:
    if kind not in KINDS:
        raise ServiceError(f"unknown sheet kind {kind!r}", 404)
    st = settings_for(store, design, project, user)
    inp = drawing_inputs(store, design, project, with_terrain=(kind == "plan"))
    sheets = build_sheets(inp, st, [kind])
    if index < 0 or index >= len(sheets):
        raise ServiceError(f"{kind} sheet {index + 1} does not exist ({len(sheets)} sheets)", 404)
    return sheet_to_svg(sheets[index])


def sheets_dxf(store: ProjectStore, design: dict, project: dict, out_path: Path, kinds: list[str] | None = None, user: dict | None = None) -> Path:
    st = settings_for(store, design, project, user)
    ks = [k for k in (kinds or list(st.kinds)) if k in KINDS] or list(st.kinds)
    inp = drawing_inputs(store, design, project, with_terrain="plan" in ks)
    sheets = build_sheets(inp, st, ks)
    if not sheets:
        raise ServiceError("nothing to draw yet: define the alignment (and build the corridor for cross-sections)", 400)
    return sheets_to_dxf(sheets, out_path)


def design_model_dxf(store: ProjectStore, design: dict, project: dict, out_path: Path, user: dict | None = None) -> Path:
    st = settings_for(store, design, project, user)
    inp = drawing_inputs(store, design, project, with_terrain=False)
    return model_dxf(inp, out_path, chainage_interval=st.chainage_interval, text_height=2.5 * st.plan_scale / 1000.0)


def workbook(store: ProjectStore, design: dict, project: dict, user: dict | None = None) -> bytes:
    inp = drawing_inputs(store, design, project, with_terrain=False)
    return design_workbook(inp, design=design, project=project, sheet_settings=settings_doc(store, design))


__all__ = ["KINDS", "build_sheets", "design_model_dxf", "drawing_inputs", "put_settings", "safe_name", "settings_doc", "settings_for", "sheet_svg", "sheets_dxf",
           "sheets_index", "workbook", "np"]
