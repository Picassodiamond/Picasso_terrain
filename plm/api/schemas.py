"""Pydantic request/response models."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- auth
class LoginIn(BaseModel):
    username: str
    password: str


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=8, max_length=200)
    organisation: str = ""


class UserCreateIn(BaseModel):
    """Admin creates an account and hands the username / password to the person."""
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=8, max_length=200)
    role: Literal["viewer", "editor", "admin"] = "editor"
    organisation: str = ""
    full_name: str = ""
    email: str = ""
    notes: str = ""


class UserPatchIn(BaseModel):
    role: Literal["viewer", "editor", "admin"] | None = None
    organisation: str | None = None
    full_name: str | None = None
    email: str | None = None
    notes: str | None = None
    disabled: bool | None = None
    password: str | None = Field(None, min_length=8, max_length=200)


class UserOut(BaseModel):
    authenticated: bool = True
    guest: bool = False
    quota: dict[str, Any] | None = None
    claimed_projects: int = 0
    full_name: str = ""
    email: str = ""

    id: str
    username: str
    role: str = "editor"
    organisation: str = ""
    authenticated: bool = True


# ---------------------------------------------------------------- projects
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    crs: str = "local"
    description: str = ""
    settings: dict[str, Any] = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    name: str | None = None
    crs: str | None = None
    description: str | None = None
    settings: dict[str, Any] | None = None


class ProjectOut(BaseModel):
    id: str
    owner_id: str | None = None
    status: str = "active"
    my_role: str | None = None
    name: str
    description: str = ""
    crs: str = "local"
    crs_info: dict[str, Any] = Field(default_factory=dict)
    created: str
    updated: str
    settings: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- import
class DesignIn(BaseModel):
    module: str
    name: str = ""
    tin_run_id: int | None = None  # default: latest run
    alignment_id: int | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class DesignPatch(BaseModel):
    name: str | None = None
    tin_run_id: int | None = None
    alignment_id: int | None = None
    status: Literal["draft", "review", "approved", "archived"] | None = None
    settings: dict[str, Any] | None = None


class DesignOut(BaseModel):
    id: int
    module: str
    name: str = ""
    tin_run_id: int | None = None
    alignment_id: int | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    status: str = "draft"
    created: str
    updated: str


class ImportResult(BaseModel):
    filename: str
    format: str
    points_added: int = 0
    lines_added: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- constraints
class DetectParams(BaseModel):
    """Automatic constraint detection (data-limit boundary + gaps) from the survey points."""
    edge_factor: float = Field(3.0, gt=1.0, description="long-edge threshold = factor x median Delaunay edge")
    max_edge: float | None = Field(None, gt=0, description="absolute long-edge threshold in metres (overrides edge_factor)")
    min_hole_area: float | None = Field(None, gt=0, description="smallest gap to report (default 8 x median triangle area)")
    min_hole_triangles: int = Field(3, ge=1)
    detect_holes: bool = True
    point_layers: list[str] | None = None


class ConstraintFeatureIn(BaseModel):
    kind: Literal["boundary", "hole", "void", "breakline", "feature", "contour"]
    coords: list[list[float]]
    name: str = ""
    layer: str | None = None


class AcceptConstraintsIn(BaseModel):
    features: list[ConstraintFeatureIn]
    source: str = "accepted"
    replace_auto: bool = Field(False, description="delete previously auto-detected constraints first")


# ---------------------------------------------------------------- TIN
class TinParams(BaseModel):
    name: str = ""
    dedupe_tol: float = Field(0.001, gt=0)
    use_features: bool = True
    use_boundary: bool = True
    use_voids: bool = True
    boundary_mode: Literal["inside", "legacy_cross"] = "inside"
    drop_zero_z: bool = False
    point_layers: list[str] | None = None
    feature_layers: list[str] | None = None
    # constraint workflow: auto = detect boundary/gaps when the user has not supplied a boundary,
    # semi = use the stored (reviewed) constraints, manual = stored constraints only
    constraint_mode: Literal["auto", "semi", "manual"] = "auto"
    detect: DetectParams | None = None
    # validated-triangle filters (peel from the outer edge; constraint edges are never removed)
    max_edge_length: float | None = Field(None, gt=0)
    max_edge_factor: float | None = Field(None, gt=0)
    min_angle_deg: float = Field(0.0, ge=0, lt=60)
    keep_rejected: bool = True
    sync: bool | None = None


class TinRunOut(BaseModel):
    id: int
    created: str
    name: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    stats: dict[str, Any] = Field(default_factory=dict)
    n_nodes: int
    n_triangles: int
    bounds: list[float | None]
    z_range: list[float | None]
    issues_count: int = 0
    issues: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------- contours
class ContourStyle(BaseModel):
    major_color: str = "#c2410c"
    minor_color: str = "#f59e0b"
    major_width: float = 2.0
    minor_width: float = 1.0
    ramp: str | None = None                    # None | 'viridis' | 'terrain' ...
    opacity: float = 1.0
    label_format: str = "{z:.2f}"
    label_prefix: str = ""
    label_suffix: str = ""
    label_every: float = 100.0
    label_major_only: bool = True
    text_height: float = 1.0
    show_labels: bool = True


class ContourParams(BaseModel):
    run_id: int | None = None
    name: str = ""
    interval: float = Field(1.0, gt=0)
    major_every: int = Field(5, ge=0)
    base: float = 0.0
    min_spacing: float = Field(0.0, ge=0)
    smoothing: Literal["none", "chaikin"] = "none"
    smooth_iterations: int = Field(2, ge=1, le=5)
    min_length: float = Field(0.0, ge=0)
    style: ContourStyle = Field(default_factory=ContourStyle)
    sync: bool | None = None


class ContourSetPatch(BaseModel):
    name: str | None = None
    style: ContourStyle | None = None


class ContourSetOut(BaseModel):
    id: int
    run_id: int
    created: str
    name: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    style: dict[str, Any] = Field(default_factory=dict)
    n_lines: int
    levels: list[float] = Field(default_factory=list)


# ---------------------------------------------------------------- alignments
class IPIn(BaseModel):
    x: float
    y: float
    radius: float = Field(0.0, ge=0)
    transition: float = Field(0.0, ge=0)
    label: str = ""


class AlignmentIn(BaseModel):
    name: str = "Alignment"
    start_chainage: float = 0.0
    ips: list[IPIn] = Field(min_length=2)
    style: dict[str, Any] = Field(default_factory=dict)
    min_radius: float = 4.0


class AlignmentOut(BaseModel):
    id: int
    name: str
    lock: dict[str, Any] | None = None
    version: int | None = None
    start_chainage: float
    end_chainage: float
    length: float
    valid: bool
    ips: list[dict[str, Any]]
    geometry: list[dict[str, Any]]
    elements: list[dict[str, Any]]
    key_points: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    style: dict[str, Any] = Field(default_factory=dict)
    created: str | None = None
    updated: str | None = None


# ---------------------------------------------------------------- sections
class SectionParams(BaseModel):
    alignment_id: int
    run_id: int | None = None
    name: str = ""
    interval: float = Field(20.0, gt=0)
    left: float = Field(15.0, gt=0)
    right: float = Field(15.0, gt=0)
    include_curve_points: bool = True
    extra_chainages: list[float] = Field(default_factory=list)
    include_edge_crossings: bool = True
    profile_interval: float | None = None
    sync: bool | None = None


class SectionSetOut(BaseModel):
    id: int
    alignment_id: int
    run_id: int
    created: str
    name: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    profile: list[dict[str, Any]] | None = None
    sections: list[dict[str, Any]] | None = None


# ---------------------------------------------------------------- misc
class ProfileRequest(BaseModel):
    coords: list[list[float]] = Field(min_length=2)
    run_id: int | None = None


class HelmertRequest(BaseModel):
    source: list[list[float]] = Field(min_length=2)
    target: list[list[float]] = Field(min_length=2)


class JobOut(BaseModel):
    id: str
    user_id: str | None = None
    priority: int = 0
    queue_position: int | None = None
    queue_length: int | None = None
    eta_seconds: int | None = None
    project_id: str
    kind: str
    status: str
    progress: float = 0
    message: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    created: str
    started: str | None = None
    finished: str | None = None
