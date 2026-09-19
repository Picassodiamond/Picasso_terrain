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


class UserOut(BaseModel):
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
    name: str
    description: str = ""
    crs: str = "local"
    crs_info: dict[str, Any] = Field(default_factory=dict)
    created: str
    updated: str
    settings: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- import
class ImportResult(BaseModel):
    filename: str
    format: str
    points_added: int = 0
    lines_added: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


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
