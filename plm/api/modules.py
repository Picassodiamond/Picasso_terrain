"""Module registry.

The terrain workspace is the core; design modules (road, canal, building ...) are separate
workspaces on top of an immutable TIN run. A module declares what it needs from the terrain and
which design stages it offers, so the UI can render the switcher and the stage list without
knowing anything about the module's engine. Engines live in `plm.design.<module>` (road: pending
the legacy-code analysis) and register their own routers.
"""
from __future__ import annotations

MODULES: list[dict] = [
    {
        "id": "terrain", "label": "Terrain", "icon": "▲", "kind": "core", "status": "available",
        "description": "Survey points, constraints, constrained TIN, contours, alignments, sections.",
        "requires": [], "stages": [],
    },
    {
        "id": "road", "label": "Road design", "icon": "🛣", "kind": "design", "status": "available", "maturity": "shell",
        "description": "Horizontal and vertical alignment, templates and corridor, cut / fill, retaining walls, culverts and drainage.",
        "requires": ["tin_run"], "seeds": ["alignment"],
        "stages": [
            {"id": "alignment", "label": "Alignment", "description": "Horizontal alignment with standards checks"},
            {"id": "profile", "label": "Profile", "description": "Vertical alignment: PVIs, vertical curves, grades"},
            {"id": "templates", "label": "Templates", "description": "Typical sections by chainage range"},
            {"id": "earthworks", "label": "Earthworks", "description": "Corridor, cut / fill, volumes, mass haul"},
            {"id": "structures", "label": "Structures", "description": "Retaining walls"},
            {"id": "drainage", "label": "Drainage", "description": "Catchments, culverts, side drains"},
            {"id": "output", "label": "Output", "description": "Sheets, BoQ, revisions"},
        ],
    },
    {
        "id": "canal", "label": "Canal design", "icon": "〰", "kind": "design", "status": "planned",
        "description": "Canal alignment, bed slope, lined / unlined sections, hydraulics, structures.",
        "requires": ["tin_run"], "seeds": ["alignment"], "stages": [],
    },
    {
        "id": "building", "label": "Building site", "icon": "▦", "kind": "design", "status": "planned",
        "description": "Site grading: platforms, batters, surface-to-surface volumes, site drainage.",
        "requires": ["tin_run"], "seeds": [], "stages": [],
    },
]


def module(module_id: str) -> dict | None:
    return next((m for m in MODULES if m["id"] == module_id), None)


def available_design_modules() -> list[str]:
    return [m["id"] for m in MODULES if m["kind"] == "design" and m["status"] == "available"]
