/** Typed client for the PLM FastAPI backend (same origin, cookie session). */

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(`${status}: ${detail}`);
    this.status = status;
    this.detail = detail;
  }
}

export interface User { id: string; username: string; role: string; organisation: string; authenticated: boolean; guest?: boolean; full_name?: string; email?: string; claimed_projects?: number;
  quota?: { max_points: number; max_projects: number; max_tin_runs: number; ttl_days: number; projects: number } | null }
export interface AuthStatus { auth_enabled: boolean; open_registration: boolean; users: number; version: string; guest_enabled?: boolean; guest_quota?: { max_points: number; max_projects: number; ttl_days: number } }
export interface AdminUser { id: string; username: string; role: string; organisation: string; created: string; disabled: number; full_name?: string; email?: string; notes?: string }
export interface CatalogueItem { id: string; project_id: string; kind: "tin" | "design"; ref_id: number; name: string; module: string; crs: string | null; footprint: any | null; bounds: number[] | null;
  lon: number | null; lat: number | null; points: number | null; triangles: number | null; z_min: number | null; z_max: number | null; spacing: number | null; tags: string[]; created: string; updated: string;
  project_name: string; project_status: string; my_role: string | null; description: string }
export interface Health { status: string; version: string; auth_enabled: boolean; guest_enabled: boolean; job_mode: string; queue: { pending: number; running: number }; load: { heavy_in_use: number; heavy_capacity: number; available_mb: number | null }; busy: boolean }
export interface CrsInfo { spec: string; name: string; is_local: boolean; epsg: number | null; proj4?: string | null; is_geographic?: boolean }
export interface ProjectSummary { points: number; lines: Record<string, number>; tin_runs: number; contour_sets: number; alignments: number; section_sets: number; bounds: number[] | null; z_range: number[] | null; designs?: Record<string, number> }
export interface Project { id: string; name: string; description: string; crs: string; crs_info: CrsInfo; created: string; updated: string; settings: Record<string, unknown>; summary: ProjectSummary; owner_id?: string | null; status?: string; my_role?: string | null }
export interface Job { id: string; project_id: string; kind: string; status: string; progress: number; message: string; params: Record<string, unknown>; result: Record<string, any> | null; error: string | null; created: string;
  queue_position?: number | null; queue_length?: number | null; eta_seconds?: number | null; priority?: number }
export interface TinRun { id: number; created: string; name: string; params: Record<string, unknown>; stats: Record<string, any>; n_nodes: number; n_triangles: number; bounds: (number | null)[]; z_range: (number | null)[]; issues_count: number; issues: { kind: string; x: number; y: number; message: string }[] }
export interface ContourStyle { major_color: string; minor_color: string; major_width: number; minor_width: number; ramp: string | null; opacity: number; label_format: string; label_prefix: string; label_suffix: string; label_every: number; label_major_only: boolean; text_height: number; show_labels: boolean }
export interface ContourSet { id: number; run_id: number; created: string; name: string; params: Record<string, any>; style: Partial<ContourStyle>; n_lines: number; levels: number[] }
export interface IPIn { x: number; y: number; radius: number; label: string }
export interface Alignment { id: number; name: string; start_chainage: number; end_chainage: number; length: number; valid: boolean; ips: IPIn[]; geometry: any[]; elements: any[]; key_points: any[]; issues: { ip_index: number; kind: string; message: string }[]; style: Record<string, unknown>; lock: Lock | null; version: number | null; created?: string; updated?: string }
export interface Lock { project_id: string; target_type: string; target_id: string; user_id: string; username: string; acquired: string; expires: string }
export interface ProfilePoint { chainage: number; x: number; y: number; z: number | null; source: string }
export interface Section { chainage: number; label: string; centre: number[]; direction: number; left: number; right: number; offset: number[]; z: (number | null)[]; xy: number[][]; source: string[] }
export interface SectionSet { id: number; alignment_id: number; run_id: number; created: string; name: string; params: Record<string, any>; summary: Record<string, any>; profile?: ProfilePoint[]; sections?: Section[] }
export interface Comment { id: string; project_id: string; user_id: string | null; username: string | null; target_type: string; target_id: string; parent_id: string | null; x: number | null; y: number | null; chainage: number | null; text: string; resolved: boolean; created: string; updated: string }
export interface Activity { id: number; username: string | null; action: string; target_type: string; target_id: string; detail: Record<string, any>; created: string }
export interface Member { user_id: string; username: string; role: string; organisation: string; added: string }
export type FeatureCollection = { type: "FeatureCollection"; features: any[] };
export interface ModuleInfo { id: string; label: string; icon: string; kind: string; status: string; maturity?: string; description: string; requires: string[]; seeds?: string[]; stages: { id: string; label: string; description: string }[] }
export interface Design { id: number; module: string; name: string; tin_run_id: number | null; alignment_id: number | null; settings: Record<string, any>; status: string; created: string; updated: string }
export interface PointDetail { fid: number; id: string; x: number; y: number; z: number; remark: string; layer: string; source: string }
export interface TileIndex { n: number; tile_triangles: number; n_triangles: number; n_nodes: number; bounds: number[]; z_range: number[]; tiles: { i: number; j: number; triangles: number; bounds: number[] }[] }

/** Lightweight notification channel so this module never imports the UI store. */
export function notify(text: string, kind = "info"): void {
  document.dispatchEvent(new CustomEvent("plm:toast", { detail: { text, kind } }));
}

async function request<T>(method: string, url: string, body?: unknown, init: RequestInit = {}, attempt = 0): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string> | undefined) };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(url, { method, body: payload, credentials: "same-origin", ...init, headers });
  if (res.status === 503 && attempt < 2) {
    // the server is busy: honour Retry-After and try again instead of failing the user's action
    const wait = Math.min(Math.max(Number(res.headers.get("retry-after") || 10), 2), 30);
    notify(`Server busy - retrying in ${wait} s`, "info");
    await new Promise((r) => setTimeout(r, wait * 1000));
    return request<T>(method, url, body, init, attempt + 1);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch { /* ignore */ }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("json")) return (await res.json()) as T;
  if (ct.includes("octet-stream")) return (await res.arrayBuffer()) as T;
  return (await res.text()) as T;
}

const q = (params: Record<string, unknown>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  const t = s.toString();
  return t ? `?${t}` : "";
};

export const api = {
  auth: {
    status: () => request<AuthStatus>("GET", "/api/auth/status"),
    me: () => request<User>("GET", "/api/auth/me"),
    login: (username: string, password: string) => request<User>("POST", "/api/auth/login", { username, password }),
    register: (username: string, password: string, organisation: string) => request<User>("POST", "/api/auth/register", { username, password, organisation }),
    logout: () => request<{ ok: boolean }>("POST", "/api/auth/logout"),
    users: () => request<AdminUser[]>("GET", "/api/auth/users"),
    createUser: (body: Record<string, unknown>) => request<AdminUser>("POST", "/api/auth/users", body),
    patchUser: (id: string, body: Record<string, unknown>) => request<AdminUser>("PATCH", `/api/auth/users/${id}`, body),
  },
  health: () => request<Health>("GET", "/api/health"),
  catalogue: {
    list: (params: Record<string, unknown> = {}) => request<CatalogueItem[]>("GET", `/api/catalogue${q(params)}`),
    clone: (id: string, name?: string) => request<Project>("POST", `/api/catalogue/${id}/clone`, { name }),
  },
  crs: {
    presets: () => request<{ key: string; name: string; definition: string | null; description: string }[]>("GET", "/api/crs/presets"),
    describe: (spec: string) => request<CrsInfo>("GET", `/api/crs/describe${q({ spec })}`),
  },
  projects: {
    list: (includeArchived = true) => request<Project[]>("GET", `/api/projects${q({ include_archived: includeArchived })}`),
    archive: (id: string) => request<Project>("POST", `/api/projects/${id}/archive`),
    restore: (id: string) => request<Project>("POST", `/api/projects/${id}/restore`),
    archiveUrl: (id: string) => `/api/projects/${id}/archive.zip`,
    create: (body: { name: string; crs: string; description?: string }) => request<Project>("POST", "/api/projects", body),
    get: (id: string) => request<Project>("GET", `/api/projects/${id}`),
    update: (id: string, body: Record<string, unknown>) => request<Project>("PATCH", `/api/projects/${id}`, body),
    delete: (id: string) => request<void>("DELETE", `/api/projects/${id}`),
  },
  data: {
    import: (pid: string, file: File, form: Record<string, string | boolean | undefined>) => {
      const fd = new FormData();
      fd.append("file", file, file.name);
      for (const [k, v] of Object.entries(form)) if (v !== undefined && v !== "") fd.append(k, String(v));
      return request<{ points_added: number; lines_added: Record<string, number>; warnings: string[]; summary: ProjectSummary }>("POST", `/api/projects/${pid}/import`, fd);
    },
    points: (pid: string, layer?: string, limit?: number) => request<FeatureCollection>("GET", `/api/projects/${pid}/points.geojson${q({ layer, limit })}`),
    pointsBin: (pid: string, layer?: string) => request<ArrayBuffer>("GET", `/api/projects/${pid}/points.bin${q({ layer })}`),
    point: (pid: string, fid: number) => request<PointDetail>("GET", `/api/projects/${pid}/points/${fid}`),
    nearest: (pid: string, x: number, y: number, radius = 5) => request<PointDetail & { distance: number }>("GET", `/api/projects/${pid}/points/nearest${q({ x, y, radius })}`),
    pointLayers: (pid: string) => request<{ layer: string; n: number; zmin: number; zmax: number }[]>("GET", `/api/projects/${pid}/points/layers`),
    deletePoints: (pid: string, params: Record<string, unknown>) => request<{ deleted: number }>("DELETE", `/api/projects/${pid}/points${q(params)}`),
    lines: (pid: string, kind?: string) => request<FeatureCollection>("GET", `/api/projects/${pid}/lines.geojson${q({ kind })}`),
    lineSummary: (pid: string) => request<{ kind: string; layer: string; n: number; vertices: number }[]>("GET", `/api/projects/${pid}/lines/summary`),
    addLine: (pid: string, body: { kind: string; layer?: string; name?: string; coords: number[][] }) => request<{ added: number }>("POST", `/api/projects/${pid}/lines`, body),
    deleteLines: (pid: string, params: Record<string, unknown>) => request<{ deleted: number }>("DELETE", `/api/projects/${pid}/lines${q(params)}`),
    updateLine: (pid: string, fid: number, params: Record<string, unknown>) => request<{ ok: boolean }>("PATCH", `/api/projects/${pid}/lines/${fid}${q(params)}`),
    pointsCsvUrl: (pid: string) => `/api/projects/${pid}/points.csv`,
  },
  tin: {
    create: (pid: string, body: Record<string, unknown>) => request<Job>("POST", `/api/projects/${pid}/tin`, body),
    list: (pid: string) => request<TinRun[]>("GET", `/api/projects/${pid}/tin`),
    get: (pid: string, run: number) => request<TinRun>("GET", `/api/projects/${pid}/tin/${run}`),
    delete: (pid: string, run: number) => request<void>("DELETE", `/api/projects/${pid}/tin/${run}`),
    mesh: (pid: string, run: number) => request<ArrayBuffer>("GET", `/api/projects/${pid}/tin/${run}/mesh.bin`),
    hull: (pid: string, run: number) => request<FeatureCollection>("GET", `/api/projects/${pid}/tin/${run}/hull.geojson`),
    issues: (pid: string, run: number) => request<FeatureCollection>("GET", `/api/projects/${pid}/tin/${run}/issues.geojson`),
    elevation: (pid: string, run: number, x: number, y: number) => request<{ z: number | null; inside: boolean }>("GET", `/api/projects/${pid}/tin/${run}/elevation${q({ x, y })}`),
    profile: (pid: string, run: number, coords: number[][]) => request<{ distance: number[]; z: (number | null)[]; xy: number[][]; length: number }>("POST", `/api/projects/${pid}/tin/${run}/profile`, { coords }),
    rejected: (pid: string, run: number) => request<FeatureCollection & { total: number; counts: Record<string, number> }>("GET", `/api/projects/${pid}/tin/${run}/rejected.geojson`),
    tiles: (pid: string, run: number) => request<TileIndex>("GET", `/api/projects/${pid}/tin/${run}/tiles`),
    tileMesh: (pid: string, run: number, i: number, j: number) => request<ArrayBuffer>("GET", `/api/projects/${pid}/tin/${run}/tiles/${i}/${j}.bin`),
  },
  constraints: {
    detect: (pid: string, body: Record<string, unknown> = {}) => request<FeatureCollection & { stats: Record<string, any> }>("POST", `/api/projects/${pid}/constraints/detect`, body),
    accept: (pid: string, body: { features: { kind: string; coords: number[][]; name?: string }[]; source?: string; replace_auto?: boolean }) => request<{ added: number }>("POST", `/api/projects/${pid}/constraints/accept`, body),
    list: (pid: string, source?: string) => request<FeatureCollection>("GET", `/api/projects/${pid}/constraints${q({ source })}`),
    deleteAuto: (pid: string) => request<{ deleted: number }>("DELETE", `/api/projects/${pid}/constraints/auto`),
  },
  contours: {
    create: (pid: string, body: Record<string, unknown>) => request<Job>("POST", `/api/projects/${pid}/contours`, body),
    list: (pid: string) => request<ContourSet[]>("GET", `/api/projects/${pid}/contours`),
    get: (pid: string, id: number) => request<ContourSet>("GET", `/api/projects/${pid}/contours/${id}`),
    patch: (pid: string, id: number, body: Record<string, unknown>) => request<ContourSet>("PATCH", `/api/projects/${pid}/contours/${id}`, body),
    delete: (pid: string, id: number) => request<void>("DELETE", `/api/projects/${pid}/contours/${id}`),
    geojson: (pid: string, id: number) => request<FeatureCollection>("GET", `/api/projects/${pid}/contours/${id}.geojson${q({ ndigits: 3 })}`),
    labels: (pid: string, id: number, every?: number) => request<FeatureCollection>("GET", `/api/projects/${pid}/contours/${id}/labels.geojson${q({ every })}`),
  },
  alignments: {
    list: (pid: string) => request<Alignment[]>("GET", `/api/projects/${pid}/alignments`),
    create: (pid: string, body: Record<string, unknown>) => request<Alignment>("POST", `/api/projects/${pid}/alignments`, body),
    preview: (pid: string, body: Record<string, unknown>) => request<Alignment>("POST", `/api/projects/${pid}/alignments/preview`, body),
    get: (pid: string, id: number) => request<Alignment>("GET", `/api/projects/${pid}/alignments/${id}`),
    update: (pid: string, id: number, body: Record<string, unknown>, note = "") => request<Alignment>("PUT", `/api/projects/${pid}/alignments/${id}${q({ note })}`, body),
    delete: (pid: string, id: number) => request<void>("DELETE", `/api/projects/${pid}/alignments/${id}`),
    geometry: (pid: string, id: number, chainage_interval = 20) => request<FeatureCollection>("GET", `/api/projects/${pid}/alignments/${id}/geometry.geojson${q({ chainage_interval })}`),
    importFile: (pid: string, file: File, form: Record<string, string>) => {
      const fd = new FormData();
      fd.append("file", file, file.name);
      for (const [k, v] of Object.entries(form)) if (v) fd.append(k, v);
      return request<Alignment>("POST", `/api/projects/${pid}/alignments/import`, fd);
    },
    csvUrl: (pid: string, id: number) => `/api/projects/${pid}/alignments/${id}.csv`,
    lock: (pid: string, id: number) => request<Lock>("POST", `/api/projects/${pid}/alignments/${id}/lock`),
    unlock: (pid: string, id: number, force = false) => request<{ released: boolean }>("DELETE", `/api/projects/${pid}/alignments/${id}/lock${q({ force })}`),
    versions: (pid: string, id: number) => request<{ version: number; username: string | null; note: string; created: string }[]>("GET", `/api/projects/${pid}/alignments/${id}/versions`),
    restore: (pid: string, id: number, version: number) => request<Alignment>("POST", `/api/projects/${pid}/alignments/${id}/versions/${version}/restore`, { note: "" }),
  },
  sections: {
    create: (pid: string, body: Record<string, unknown>) => request<Job>("POST", `/api/projects/${pid}/sections`, body),
    list: (pid: string, alignment_id?: number) => request<SectionSet[]>("GET", `/api/projects/${pid}/sections${q({ alignment_id })}`),
    get: (pid: string, id: number) => request<SectionSet>("GET", `/api/projects/${pid}/sections/${id}`),
    delete: (pid: string, id: number) => request<void>("DELETE", `/api/projects/${pid}/sections/${id}`),
    lines: (pid: string, id: number) => request<FeatureCollection>("GET", `/api/projects/${pid}/sections/${id}/lines.geojson`),
    profileCsvUrl: (pid: string, id: number) => `/api/projects/${pid}/sections/${id}/profile.csv`,
    crossCsvUrl: (pid: string, id: number) => `/api/projects/${pid}/sections/${id}/cross.csv`,
  },
  exportUrl: {
    dxf: (pid: string, params: Record<string, unknown>) => `/api/projects/${pid}/export.dxf${q(params)}`,
    gpkg: (pid: string) => `/api/projects/${pid}/export.gpkg`,
    geojson: (pid: string) => `/api/projects/${pid}/export.geojson`,
  },
  modules: {
    list: () => request<ModuleInfo[]>("GET", "/api/modules"),
  },
  designs: {
    list: (pid: string) => request<Design[]>("GET", `/api/projects/${pid}/designs`),
    create: (pid: string, body: { module: string; name?: string; tin_run_id?: number | null; alignment_id?: number | null; settings?: Record<string, unknown> }) => request<Design>("POST", `/api/projects/${pid}/designs`, body),
    get: (pid: string, id: number) => request<Design>("GET", `/api/projects/${pid}/designs/${id}`),
    patch: (pid: string, id: number, body: Record<string, unknown>) => request<Design>("PATCH", `/api/projects/${pid}/designs/${id}`, body),
    delete: (pid: string, id: number) => request<void>("DELETE", `/api/projects/${pid}/designs/${id}`),
  },
  jobs: {
    get: (id: string) => request<Job>("GET", `/api/jobs/${id}`),
    list: (pid: string) => request<Job[]>("GET", `/api/projects/${pid}/jobs`),
  },
  collab: {
    members: (pid: string) => request<Member[]>("GET", `/api/projects/${pid}/members`),
    addMember: (pid: string, username: string, role = "editor") => request<Member[]>("POST", `/api/projects/${pid}/members`, { username, role }),
    removeMember: (pid: string, uid: string) => request<Member[]>("DELETE", `/api/projects/${pid}/members/${uid}`),
    setRole: (pid: string, uid: string, role: string) => request<Member[]>("PATCH", `/api/projects/${pid}/members/${uid}`, { role }),
    transfer: (pid: string, username: string) => request<Member[]>("POST", `/api/projects/${pid}/transfer`, { username }),
    comments: (pid: string, params: Record<string, unknown> = {}) => request<Comment[]>("GET", `/api/projects/${pid}/comments${q(params)}`),
    addComment: (pid: string, body: Record<string, unknown>) => request<Comment>("POST", `/api/projects/${pid}/comments`, body),
    patchComment: (pid: string, id: string, body: Record<string, unknown>) => request<Comment>("PATCH", `/api/projects/${pid}/comments/${id}`, body),
    deleteComment: (pid: string, id: string) => request<void>("DELETE", `/api/projects/${pid}/comments/${id}`),
    activity: (pid: string, since = 0) => request<{ activity: Activity[]; locks: Lock[] }>("GET", `/api/projects/${pid}/activity${q({ since })}`),
  },
};

/** Human text for a job's state while waiting: queue position and estimate, or progress. */
export function jobStatusText(j: Job, verb: string): string {
  if (j.status === "pending") {
    const pos = j.queue_position ? `${j.queue_position}${j.queue_length ? ` of ${j.queue_length}` : ""}` : "";
    const eta = j.eta_seconds ? `, about ${j.eta_seconds >= 90 ? `${Math.round(j.eta_seconds / 60)} min` : `${j.eta_seconds} s`}` : "";
    return pos ? `Waiting in queue: ${pos}${eta}` : "Waiting in queue";
  }
  return `${verb} ${Math.round((j.progress || 0) * 100)}%`;
}

/** Poll a job until it finishes (for background jobs). */
export async function waitForJob(job: Job, onProgress?: (j: Job) => void, intervalMs = 1500): Promise<Job> {
  let j = job;
  while (j.status === "pending" || j.status === "running") {
    await new Promise((r) => setTimeout(r, intervalMs));
    j = await api.jobs.get(j.id);
    onProgress?.(j);
  }
  if (j.status === "error") throw new ApiError(400, j.error || "job failed");
  return j;
}
