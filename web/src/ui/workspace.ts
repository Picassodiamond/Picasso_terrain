/** Project workspace: top bar, side panels, Cesium map, charts pane, polling. */
import * as Cesium from "cesium";
import { api, ApiError, type Alignment, type Comment, type Project, type SectionSet } from "../api";
import { renderProfile } from "../charts/profile";
import { renderSection } from "../charts/section";
import { alignmentGeoJSON } from "../geom/alignment";
import { Frame } from "../map/coords";
import { Interaction } from "../map/draw";
import { MapLayers, type PickId } from "../map/layers";
import { MapViewer, type BaseMap } from "../map/viewer";
import { store, toast, type AppState } from "../state";
import { button, el, fmt, fmtChainage, select } from "./dom";
import { renderAlignmentPanel } from "./panels/alignment";
import { renderContoursPanel } from "./panels/contours";
import { renderDataPanel } from "./panels/data";
import { renderExportPanel } from "./panels/export";
import { renderSectionsPanel } from "./panels/sections";
import { renderSettingsPanel } from "./panels/settings";
import { renderTeamPanel } from "./panels/team";
import { renderTinPanel } from "./panels/tin";
import { LayerTree } from "./layerTree";
import { renderModuleSwitcher } from "./moduleSwitcher";
import { MODULES, designHash } from "../modules/registry";

type Tab = "data" | "tin" | "contours" | "alignment" | "sections" | "export" | "team" | "settings";
export type HeightMode = "ground" | "true";
const TABS: { key: Tab; label: string }[] = [
  { key: "data", label: "Data" }, { key: "tin", label: "TIN" }, { key: "contours", label: "Contours" }, { key: "alignment", label: "Alignment" },
  { key: "sections", label: "Sections" }, { key: "export", label: "Export" }, { key: "team", label: "Team" }, { key: "settings", label: "Settings" },
];

export class Workspace {
  root: HTMLElement;
  project: Project;
  frame: Frame;
  mv!: MapViewer;
  layers!: MapLayers;
  interaction!: Interaction;
  private onClose: () => void;
  private panelHost!: HTMLElement;
  private tabButtons = new Map<Tab, HTMLButtonElement>();
  private activeTab: Tab = "data";
  private statusCoords!: HTMLElement;
  private statusInfo!: HTMLElement;
  private switcherHost!: HTMLElement;
  private loadBanner!: HTMLElement;
  private healthTimer: number | null = null;
  private toolHint!: HTMLElement;
  private busyEl!: HTMLElement;
  private chartsEl!: HTMLElement;
  private profileEl!: HTMLElement;
  private sectionEl!: HTMLElement;
  private stationLabel!: HTMLElement;
  private vScale = 5;
  private unsub: (() => void)[] = [];
  private pollTimer: number | null = null;
  private lastActivityId = 0;
  private profileCursor: ((ch: number | null) => void) | null = null;
  /** nearest-vertex ground lookup: origin-relative float32 positions + a counting-sort grid (typed arrays) */
  private tinVertices: { ox: number; oy: number; oz: number; xyz: Float32Array; x0: number; y0: number; cell: number; nx: number; ny: number; offsets: Int32Array; ids: Int32Array } | null = null;
  private tinIndexParts: { origin: [number, number, number]; f: Float32Array }[] = [];
  /** meshes bigger than this are fetched as tiles and drawn progressively */
  static TILE_THRESHOLD = 300_000;
  layerTree!: LayerTree;
  comments: Comment[] = [];
  currentSectionSetData: SectionSet | null = null;
  /** map-click hooks registered by panels (tools) */
  toolHandlers: { onClick?: (p: { x: number; y: number; z: number }, picked: PickId | null) => void; onDouble?: (p: { x: number; y: number; z: number } | null) => void; onMove?: (p: { x: number; y: number; z: number } | null) => void; onDrag?: (i: number, p: { x: number; y: number; z: number }, phase: "start" | "move" | "end") => void; hint?: string } = {};

  constructor(root: HTMLElement, project: Project, onClose: () => void) {
    this.root = root;
    this.project = project;
    this.onClose = onClose;
    const b = project.summary?.bounds;
    const anchor = (project.settings as any)?.anchor ?? null;
    this.frame = new Frame(project.crs_info, b ? [b[0], b[1]] : undefined, anchor && Number.isFinite(anchor.lon) ? anchor : null);
    this.heightMode = ((project.settings as any)?.height_mode as HeightMode) || "ground";
    this.applyHeightMode();
    store.set("project", project);
  }

  // ------------------------------------------------------------------ height mode
  /** "ground": the lowest survey point rests on the base map (display only, RLs are unchanged);
   *  "true": real heights above the ellipsoid - the terrain floats above the flat imagery. */
  heightMode: HeightMode = "ground";

  /** Recompute the frame's display offset from the current mode and data. Returns true when it changed. */
  applyHeightMode(): boolean {
    const zmin = this.project.summary?.z_range?.[0];
    const want = this.heightMode === "ground" && this.frame.georeferenced && zmin != null && Number.isFinite(zmin) ? -zmin : 0;
    if (want === this.frame.heightOffset) return false;
    this.frame.heightOffset = want;
    return true;
  }

  async setHeightMode(mode: HeightMode, persist = true): Promise<void> {
    this.heightMode = mode;
    if (this.applyHeightMode()) await this.redrawAll();
    if (persist) api.projects.update(this.project.id, { settings: { height_mode: mode } }).catch(() => { /* viewers without edit rights keep the choice for this session only */ });
  }

  /** Re-place every layer after the frame changed (heights/offset), then re-frame the camera. */
  async redrawAll(): Promise<void> {
    if (!this.layers) return;
    const buf = await api.data.pointsBin(this.project.id);
    const n = this.project.summary?.points ?? 0;
    const labels = store.get("layers").pointLabels && n <= 20_000 ? await api.data.points(this.project.id, undefined, 20_000) : null;
    this.layers.setPointsBinary(buf, labels);
    await this.selectRun(store.get("currentRun")); // TIN + lines, alignment, comments
    await this.redrawContours();
    await this.loadSectionSet();
    this.layers.setVisibility(store.get("layers"));
    this.zoomToData();
  }

  // ------------------------------------------------------------------ layout
  async init(): Promise<void> {
    this.root.innerHTML = "";
    const user = store.get("user");
    const geo = this.frame.georeferenced;
    const baseSel = select(
      [{ value: "osm", label: "OpenStreetMap" }, { value: "opentopo", label: "OpenTopoMap" }, { value: "carto", label: "Carto dark" }, { value: "esri", label: "Esri imagery" }, { value: "none", label: "No base map" }],
      geo ? "osm" : "none", { title: geo ? "Base map" : "Base maps need a coordinate system or a placed local grid (Settings tab)" });
    if (!geo) baseSel.disabled = true;
    baseSel.addEventListener("change", () => this.mv.setBaseMap(baseSel.value as BaseMap));
    const baseHint = geo ? null : el("button", { class: "btn small", title: "Why is the base map disabled?", onClick: () => {
      toast("This project uses a local grid (plain metres), so it cannot be placed on the Earth. In Settings either assign the real coordinate system (MUTM / UTM) or type the longitude/latitude of one known point to place the grid on the map.", "info");
      this.showTab("settings");
    } }, "Base map?");
    const modeSel = select([{ value: "3D", label: "3D" }, { value: "2D", label: "2D" }, { value: "2.5D", label: "2.5D" }], "3D");
    modeSel.addEventListener("change", () => this.mv.setSceneMode(modeSel.value as any));
    const heightSel = select(
      [{ value: "ground", label: "On base map" }, { value: "true", label: "True elevation" }],
      geo ? this.heightMode : "true",
      { title: geo
        ? "On base map: the lowest survey point is drawn at the base map level so the terrain sits on the imagery (display only; RLs, contours and sections keep their real values). True elevation: real heights above the ellipsoid - the terrain floats above the flat map."
        : "Height placement applies to georeferenced projects" });
    if (!geo) heightSel.disabled = true;
    heightSel.addEventListener("change", () => void this.setHeightMode(heightSel.value as HeightMode));
    const chartsBtn = button("Charts", () => this.toggleCharts(), "btn small");
    const top = el("header", { class: "topbar" },
      el("div", { class: "brand", onClick: () => this.onClose(), style: "cursor:pointer", title: "All projects" }, el("span", { style: "color:var(--accent)" }, "▲"), "Picasso LandMesh"),
      el("span", { class: "project-name" }, "project ", el("b", {}, this.project.name), el("span", { class: "badge" }, this.project.crs_info?.is_local ? "local grid" : this.project.crs_info?.name || this.project.crs)),
      (this.switcherHost = el("span", { class: "switcher-host" })),
      el("span", { class: "spacer" }),
      el("span", { class: "muted" }, "Base map"), baseSel, baseHint,
      el("span", { class: "muted" }, "View"), modeSel,
      el("span", { class: "muted" }, "Height"), heightSel,
      button("Zoom to data", () => this.zoomToData(), "btn small"),
      button("Top", () => this.mv.lookDown(), "btn small"),
      chartsBtn,
      user?.authenticated ? el("span", { class: "muted" }, user.username) : null,
      user?.authenticated ? button("Sign out", async () => { await api.auth.logout(); location.hash = ""; location.reload(); }, "btn small") : null,
    );

    const tabs = el("div", { class: "tabs" });
    for (const t of TABS) {
      const b = el("button", { onClick: () => this.showTab(t.key) }, t.label);
      this.tabButtons.set(t.key, b);
      tabs.appendChild(b);
    }
    this.panelHost = el("div", { class: "panel" });
    const sidebar = el("aside", { class: "sidebar" }, tabs, this.panelHost);

    const cesiumDiv = el("div", { id: "cesium" });
    this.toolHint = el("div", { class: "tool-hint", style: "display:none" });
    this.busyEl = el("div", { class: "busy", style: "display:none" }, el("span", { class: "spinner" }), el("span", { class: "busy-text" }));
    this.statusCoords = el("span", { class: "mono" });
    this.statusInfo = el("span");
    const statusbar = el("div", { class: "statusbar" }, this.statusCoords, this.statusInfo);
    const mapWrap = el("div", { class: "map-wrap" }, cesiumDiv, this.toolHint, this.busyEl, statusbar);
    // access / state banners
    if (this.project.status === "archived") mapWrap.appendChild(el("div", { class: "ws-banner warn" }, "Archived project - read-only. ", this.project.my_role === "owner" ? "Restore it under Settings to make changes." : "The owner can restore it."));
    else if (this.project.my_role === "viewer") mapWrap.appendChild(el("div", { class: "ws-banner" }, "View-only access: you can browse and comment; ask the owner for the editor role to make changes."));
    else if (store.get("user")?.guest) mapWrap.appendChild(el("div", { class: "ws-banner" }, "Guest sandbox - limited points, no exports, deleted after a few days. ", button("Sign in", () => store.emit("auth:login"), "btn small")));
    this.loadBanner = el("div", { class: "ws-banner load", style: "display:none" });
    mapWrap.appendChild(this.loadBanner);

    this.profileEl = el("div");
    this.sectionEl = el("div");
    this.stationLabel = el("span", { class: "mono" });
    const vsel = select([1, 2, 5, 10, 20].map((v) => ({ value: String(v), label: `V ×${v}` })), String(this.vScale));
    vsel.addEventListener("change", () => { this.vScale = Number(vsel.value); this.renderCharts(); });
    const splitter = el("div", { class: "splitter" });
    this.chartsEl = el("section", { class: "charts collapsed" },
      splitter,
      el("div", { class: "chart-head" },
        el("b", {}, "Profile & cross-section"),
        button("◀", () => this.stepSection(-1), "btn small"), this.stationLabel, button("▶", () => this.stepSection(1), "btn small"),
        vsel,
        el("span", { class: "spacer" }),
        button("▾", () => this.toggleCharts(), "btn small"),
      ),
      el("div", { class: "chart-body" }, this.profileEl, this.sectionEl),
    );
    this.setupSplitter(splitter);
    const main = el("div", { class: "main" }, mapWrap, this.chartsEl);
    this.root.appendChild(el("div", { class: "app" }, top, el("div", { class: "workspace" }, sidebar, main)));
    void this.refreshDesigns();

    // map
    this.mv = new MapViewer(cesiumDiv, this.frame);
    this.mv.onTileError = (m) => toast(m, "error");
    this.layers = new MapLayers(this.mv.scene, this.frame);
    this.interaction = new Interaction(this.mv);
    this.interaction.callbacks = {
      onClick: (p, picked) => this.toolHandlers.onClick?.(p, picked) ?? this.defaultClick(p, picked),
      onDoubleClick: (p) => this.toolHandlers.onDouble?.(p),
      onMove: (p, picked) => { this.statusCoords.textContent = p ? `E ${fmt(p.x, 2)}  N ${fmt(p.y, 2)}` : ""; this.toolHandlers.onMove?.(p); void picked; },
      onDragIp: (i, p, phase) => this.toolHandlers.onDrag?.(i, p, phase),
      onRightClick: () => { if (store.get("tool") !== "none") this.setTool("none"); },
    };
    window.addEventListener("keydown", this.onKey);
    window.addEventListener("resize", this.onResize);

    this.layerTree = new LayerTree(this, mapWrap);
    this.unsub.push(store.on("layers", (v) => this.layers.setVisibility(v)));
    this.unsub.push(store.on("busy", (v) => { this.busyEl.style.display = v ? "" : "none"; this.busyEl.querySelector(".busy-text")!.textContent = v || ""; }));
    this.unsub.push(store.on("currentSectionSet", () => void this.loadSectionSet()));
    this.unsub.push(store.on("currentSectionIndex", () => this.renderCharts()));
    this.unsub.push(store.subscribe("hover:chainage", (p) => this.layers.setHoverMarker(p)));
    this.unsub.push(store.subscribe("map:flyTo", (b) => this.mv.flyToBounds(b)));

    await this.loadAll();
    this.showTab("data");
    this.zoomToData();
    this.startPolling();
    this.startHealthPolling();
    this.updateStatus();
  }

  /** Every 20 s: show a banner when the server queue is long or all heavy slots are taken. */
  private startHealthPolling(): void {
    const tick = async () => {
      try {
        const h = await api.health();
        if (h.busy) {
          const parts = [];
          if (h.queue.pending) parts.push(`${h.queue.pending} job${h.queue.pending > 1 ? "s" : ""} waiting`);
          if (h.queue.running) parts.push(`${h.queue.running} running`);
          if (h.load.heavy_in_use >= h.load.heavy_capacity) parts.push("all processing slots in use");
          this.loadBanner.textContent = `Server is busy (${parts.join(", ")}). New builds will queue; you will see your position.`;
          this.loadBanner.style.display = "";
        } else this.loadBanner.style.display = "none";
      } catch { /* offline: nothing to show */ }
    };
    void tick();
    this.healthTimer = window.setInterval(() => void tick(), 20_000);
  }

  private setupSplitter(splitter: HTMLElement): void {
    let startY = 0, startH = 0;
    const move = (e: MouseEvent) => { const h = Math.max(120, Math.min(window.innerHeight - 200, startH + (startY - e.clientY))); this.chartsEl.style.height = `${h}px`; };
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); this.renderCharts(); };
    splitter.addEventListener("mousedown", (e) => { if (this.chartsEl.classList.contains("collapsed")) return; startY = e.clientY; startH = this.chartsEl.clientHeight; window.addEventListener("mousemove", move); window.addEventListener("mouseup", up); e.preventDefault(); });
  }

  private onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") this.setTool("none");
    if ((e.target as HTMLElement)?.tagName === "INPUT" || (e.target as HTMLElement)?.tagName === "TEXTAREA") return;
    if (e.key === "ArrowRight") this.stepSection(1);
    if (e.key === "ArrowLeft") this.stepSection(-1);
  };
  private onResize = () => this.renderCharts();

  showTab(tab: Tab): void {
    this.activeTab = tab;
    for (const [k, b] of this.tabButtons) b.classList.toggle("active", k === tab);
    this.panelHost.innerHTML = "";
    const host = this.panelHost;
    switch (tab) {
      case "data": renderDataPanel(this, host); break;
      case "tin": renderTinPanel(this, host); break;
      case "contours": renderContoursPanel(this, host); break;
      case "alignment": renderAlignmentPanel(this, host); break;
      case "sections": renderSectionsPanel(this, host); break;
      case "export": renderExportPanel(this, host); break;
      case "team": renderTeamPanel(this, host); break;
      case "settings": renderSettingsPanel(this, host); break;
    }
  }

  rerenderPanel(): void {
    this.showTab(this.activeTab);
  }

  // ------------------------------------------------------------------ tools
  setTool(tool: AppState["tool"], hint = ""): void {
    store.set("tool", tool);
    if (tool === "none") { this.toolHandlers = {}; this.layers.setDraftLine(null); this.interaction.dragEnabled = false; }
    this.toolHint.style.display = tool === "none" ? "none" : "";
    this.toolHint.textContent = hint || this.toolHandlers.hint || "";
    this.mv.scene.canvas.style.cursor = tool === "none" ? "" : "crosshair";
  }

  private defaultClick(p: { x: number; y: number; z: number }, picked: PickId | null): void {
    const show = (d: { fid: number; id?: string; z: number; remark?: string }) => toast(`Point ${d.id || d.fid}: RL ${fmt(d.z)} ${d.remark ? "· " + d.remark : ""}`);
    if (picked?.type === "point") {
      const pk = picked;
      void api.data.point(this.project.id, pk.fid).then(show).catch(() => show(pk));
    } else if (picked?.type === "section") { store.set("currentSectionIndex", picked.index); this.showCharts(true); }
    else if (picked?.type === "comment") { this.showTab("team"); }
    else if (picked?.type === "ip") { this.showTab("alignment"); }
    else if (!picked && this.layers.pointsBig && store.get("layers").points) {
      // big point clouds are not pickable: ask the server for the closest point instead
      void api.data.nearest(this.project.id, p.x, p.y, 3).then(show).catch(() => { /* nothing close */ });
    }
  }

  // ------------------------------------------------------------------ data loading
  async loadAll(): Promise<void> {
    store.set("busy", "Loading project");
    try {
      // TIN first: label/marker heights of the other layers are taken from it
      await Promise.all([this.refreshPoints(), this.refreshLines(), this.refreshTinRuns()]);
      await Promise.all([this.refreshAlignments(), this.refreshComments()]);
      await this.refreshContours();
      await this.refreshSectionSets();
    } finally {
      store.set("busy", null);
    }
  }

  async refreshProject(): Promise<void> {
    this.project = await api.projects.get(this.project.id);
    store.set("project", this.project);
    const b = this.project.summary?.bounds;
    if (b && this.frame.kind === "local" && this.frame.originX === 0 && this.frame.originY === 0) {
      this.frame.setOrigin(b[0], b[1]);
    }
    // the lowest point may have changed (first import, deletions): keep the terrain on the base map
    if (this.applyHeightMode()) void this.redrawAll();
    this.updateStatus();
  }

  async refreshPoints(): Promise<void> {
    await this.refreshProject();
    const buf = await api.data.pointsBin(this.project.id);
    // labels need ids / remarks: fetch them as GeoJSON, but only for modest point counts
    const n = this.project.summary?.points ?? 0;
    const labels = store.get("layers").pointLabels && n <= 20_000 ? await api.data.points(this.project.id, undefined, 20_000) : null;
    this.layers.setPointsBinary(buf, labels);
    this.layers.setVisibility(store.get("layers"));
  }

  private linesFc: import("../api").FeatureCollection | null = null;

  async refreshLines(): Promise<void> {
    this.linesFc = await api.data.lines(this.project.id);
    this.layers.setLines(this.linesFc, this.zAt);
    this.layers.setVisibility(store.get("layers"));
  }

  async refreshTinRuns(): Promise<void> {
    const runs = await api.tin.list(this.project.id);
    store.set("tinRuns", runs);
    const cur = store.get("currentRun");
    const target = cur && runs.some((r) => r.id === cur) ? cur : runs.length ? runs[runs.length - 1].id : null;
    await this.selectRun(target);
  }

  async selectRun(run: number | null): Promise<void> {
    store.set("currentRun", run);
    if (run === null) { this.layers.setTin(null, store.get("tinStyle")); this.tinVertices = null; return; }
    store.set("busy", "Loading TIN");
    try {
      const r = store.get("tinRuns").find((x) => x.id === run);
      const zr = r?.z_range ?? [0, 0];
      const zro = zr[0] != null && zr[1] != null ? ([zr[0], zr[1]] as [number, number]) : undefined;
      await this.loadTinMesh(run, r?.n_triangles ?? 0, zro);
      const b = r?.bounds;
      this.mv.setPickTarget(this.layers.tinParts, ((zr[0] ?? 0) + (zr[1] ?? 0)) / 2, b && b[0] != null ? [((b[0] ?? 0) + (b[2] ?? 0)) / 2, ((b[1] ?? 0) + (b[3] ?? 0)) / 2] : null);
    } finally {
      store.set("busy", null);
    }
    // re-place layers whose heights depend on the terrain
    const al = store.get("alignments").find((a) => a.id === store.get("currentAlignment")) ?? null;
    if (al) this.drawAlignment(al);
    if (this.linesFc) this.layers.setLines(this.linesFc, this.zAt);
    this.layers.setComments(this.comments, this.zAt);
    this.layerTree?.invalidateTin();
    this.layers.setVisibility(store.get("layers"));
    this.updateStatus();
  }

  restyleTin(): void {
    const run = store.get("currentRun");
    if (run !== null) void this.selectRun(run);
  }

  /** Fetch and draw the surface: one mesh.bin for ordinary sizes, tiles (3 in flight, nearest the
   *  centre first) above TILE_THRESHOLD triangles so a big mesh appears progressively and Cesium
   *  can cull tiles outside the view. */
  private async loadTinMesh(run: number, nTriangles: number, zRange?: [number, number]): Promise<void> {
    const style = store.get("tinStyle");
    this.layers.clearTin();
    this.tinIndexParts = [];
    this.tinVertices = null;
    if (nTriangles <= Workspace.TILE_THRESHOLD) {
      const buf = await api.tin.mesh(this.project.id, run);
      this.layers.addTinPart(buf, style, zRange);
      this.addTinIndexPart(buf);
    } else {
      const idx = await api.tin.tiles(this.project.id, run);
      const cx = (idx.bounds[0] + idx.bounds[2]) / 2, cy = (idx.bounds[1] + idx.bounds[3]) / 2;
      const dist = (t: { bounds: number[] }) => ((t.bounds[0] + t.bounds[2]) / 2 - cx) ** 2 + ((t.bounds[1] + t.bounds[3]) / 2 - cy) ** 2;
      const queue = [...idx.tiles].sort((a, b) => dist(a) - dist(b));
      const total = queue.length;
      const zr = zRange ?? (idx.z_range as [number, number]);
      let done = 0;
      const worker = async () => {
        for (let t = queue.shift(); t; t = queue.shift()) {
          const buf = await api.tin.tileMesh(this.project.id, run, t.i, t.j);
          if (store.get("currentRun") !== run) return; // user switched runs meanwhile
          this.layers.addTinPart(buf, style, zr);
          this.addTinIndexPart(buf);
          store.set("busy", `Loading TIN ${++done}/${total} tiles`);
        }
      };
      await Promise.all([worker(), worker(), worker()]);
    }
    this.finishTinIndex();
  }

  private addTinIndexPart(buf: ArrayBuffer): void {
    const dv = new DataView(buf);
    const n = dv.getUint32(8, true), dtype = dv.getUint32(16, true);
    const origin: [number, number, number] = [dv.getFloat64(20, true), dv.getFloat64(28, true), dv.getFloat64(36, true)];
    if (dtype === 0) this.tinIndexParts.push({ origin, f: new Float32Array(buf.slice(44, 44 + n * 12)) });
    else {
      const d = new Float64Array(buf.slice(44, 44 + n * 24));
      const o: [number, number, number] = [d[0], d[1], d[2]];
      const f = new Float32Array(n * 3);
      for (let i = 0; i < n * 3; i++) f[i] = d[i] - o[i % 3];
      this.tinIndexParts.push({ origin: o, f });
    }
  }

  /** Build the nearest-vertex grid over all loaded parts (typed arrays only: ~20 bytes per vertex). */
  private finishTinIndex(): void {
    const parts = this.tinIndexParts;
    const n = parts.reduce((s, p) => s + p.f.length / 3, 0);
    if (!n) { this.tinVertices = null; return; }
    const ox = Math.min(...parts.map((p) => p.origin[0])), oy = Math.min(...parts.map((p) => p.origin[1])), oz = Math.min(...parts.map((p) => p.origin[2]));
    const xyz = new Float32Array(n * 3);
    let k = 0;
    let minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity;
    for (const p of parts) {
      const dx = p.origin[0] - ox, dy = p.origin[1] - oy, dz = p.origin[2] - oz;
      for (let i = 0; i < p.f.length; i += 3) {
        const x = p.f[i] + dx, y = p.f[i + 1] + dy;
        xyz[k] = x; xyz[k + 1] = y; xyz[k + 2] = p.f[i + 2] + dz; k += 3;
        if (x < minx) minx = x; if (x > maxx) maxx = x; if (y < miny) miny = y; if (y > maxy) maxy = y;
      }
    }
    let cell = Math.max(1e-3, Math.sqrt(((maxx - minx) * (maxy - miny)) / n) * 3);
    let nx = Math.floor((maxx - minx) / cell) + 1, ny = Math.floor((maxy - miny) / cell) + 1;
    while (nx * ny > 4 * n + 16) { cell *= 1.5; nx = Math.floor((maxx - minx) / cell) + 1; ny = Math.floor((maxy - miny) / cell) + 1; }
    const cellOf = new Int32Array(n);
    const counts = new Int32Array(nx * ny + 1);
    for (let i = 0; i < n; i++) {
      const c = Math.floor((xyz[i * 3 + 1] - miny) / cell) * nx + Math.floor((xyz[i * 3] - minx) / cell);
      cellOf[i] = c; counts[c + 1]++;
    }
    for (let c = 0; c < nx * ny; c++) counts[c + 1] += counts[c];
    const offsets = counts;
    const fill = new Int32Array(nx * ny);
    const ids = new Int32Array(n);
    for (let i = 0; i < n; i++) { const c = cellOf[i]; ids[offsets[c] + fill[c]++] = i; }
    this.tinVertices = { ox, oy, oz, xyz, x0: minx, y0: miny, cell, nx, ny, offsets, ids };
    this.tinIndexParts = [];
  }

  /** Approximate ground elevation (nearest TIN vertex) for placing labels/markers. */
  zAt = (x: number, y: number): number => {
    const t = this.tinVertices;
    if (!t) return 0;
    const rx = x - t.ox, ry = y - t.oy;
    const cx = Math.floor((rx - t.x0) / t.cell), cy = Math.floor((ry - t.y0) / t.cell);
    let best = Infinity, bz = 0;
    const scan = (ci: number, cj: number) => {
      if (ci < 0 || cj < 0 || ci >= t.nx || cj >= t.ny) return;
      const c = cj * t.nx + ci;
      for (let k = t.offsets[c]; k < t.offsets[c + 1]; k++) {
        const i = t.ids[k];
        const d = (t.xyz[i * 3] - rx) ** 2 + (t.xyz[i * 3 + 1] - ry) ** 2;
        if (d < best) { best = d; bz = t.xyz[i * 3 + 2]; }
      }
    };
    // grow rings outwards until something is found (points far outside the TIN take a few rings)
    for (let r = 0; r <= Math.max(t.nx, t.ny) && best === Infinity; r++) {
      for (let dx = -r; dx <= r; dx++) { scan(cx + dx, cy - r); if (r) scan(cx + dx, cy + r); }
      for (let dy = -r + 1; dy < r; dy++) { scan(cx - r, cy + dy); scan(cx + r, cy + dy); }
      if (best < Infinity && r === 0) { scan(cx - 1, cy - 1); scan(cx, cy - 1); scan(cx + 1, cy - 1); scan(cx - 1, cy); scan(cx + 1, cy); scan(cx - 1, cy + 1); scan(cx, cy + 1); scan(cx + 1, cy + 1); }
    }
    return best === Infinity ? 0 : t.oz + bz;
  };

  async refreshContours(): Promise<void> {
    const sets = await api.contours.list(this.project.id);
    store.set("contourSets", sets);
    let visible = store.get("visibleContourSets").filter((id) => sets.some((s) => s.id === id));
    if (!visible.length && sets.length) visible = [sets[sets.length - 1].id];
    store.set("visibleContourSets", visible);
    await this.redrawContours();
  }

  async redrawContours(): Promise<void> {
    const sets = store.get("contourSets");
    const visible = store.get("visibleContourSets");
    const zr = store.get("tinRuns").find((r) => r.id === store.get("currentRun"))?.z_range as [number, number] | undefined;
    const data = await Promise.all(visible.map(async (id) => {
      const s = sets.find((x) => x.id === id)!;
      const [fc, labels] = await Promise.all([api.contours.geojson(this.project.id, id), s.style?.show_labels === false ? Promise.resolve(null) : api.contours.labels(this.project.id, id)]);
      return { fc, style: s.style || {}, labels, zRange: zr && zr[0] != null ? zr : undefined };
    }));
    this.layers.setContours(data, store.get("layers").contourLabels);
    this.layers.setVisibility(store.get("layers"));
  }

  async refreshAlignments(): Promise<void> {
    const als = await api.alignments.list(this.project.id);
    store.set("alignments", als);
    const cur = store.get("currentAlignment");
    const target = cur && als.some((a) => a.id === cur) ? cur : als.length ? als[als.length - 1].id : null;
    store.set("currentAlignment", target);
    this.drawAlignment(als.find((a) => a.id === target) ?? null);
  }

  drawAlignment(al: Alignment | null, opts: { editable?: boolean; draftIps?: Alignment["ips"]; chainageInterval?: number } = {}): void {
    const geom = al && al.elements?.length ? alignmentGeoJSON(al, opts.chainageInterval ?? Number((al.style as any)?.chainage_interval ?? 20)) : null;
    this.layers.setAlignment(geom, al, { editable: !!opts.editable, draftIps: opts.draftIps, zAt: this.zAt });
    this.layers.setVisibility(store.get("layers"));
  }

  async refreshSectionSets(): Promise<void> {
    const sets = await api.sections.list(this.project.id);
    store.set("sectionSets", sets);
    const cur = store.get("currentSectionSet");
    const target = cur && sets.some((s) => s.id === cur) ? cur : sets.length ? sets[sets.length - 1].id : null;
    if (target !== cur) store.set("currentSectionSet", target);
    else await this.loadSectionSet();
  }

  async loadSectionSet(): Promise<void> {
    const id = store.get("currentSectionSet");
    if (id === null) { this.currentSectionSetData = null; this.layers.setSections(null, 0); this.renderCharts(); return; }
    const [s, lines] = await Promise.all([api.sections.get(this.project.id, id), api.sections.lines(this.project.id, id)]);
    this.currentSectionSetData = s;
    if (store.get("currentSectionIndex") >= (s.sections?.length ?? 0)) store.set("currentSectionIndex", 0);
    this.layers.setSections(lines, store.get("currentSectionIndex"));
    this.layers.setVisibility(store.get("layers"));
    this.renderCharts();
  }

  async refreshComments(): Promise<void> {
    this.comments = await api.collab.comments(this.project.id);
    this.layers.setComments(this.comments, this.zAt);
    store.emit("refresh:comments");
  }

  // ------------------------------------------------------------------ charts
  toggleCharts(): void { this.showCharts(this.chartsEl.classList.contains("collapsed")); }

  showCharts(show: boolean): void {
    this.chartsEl.classList.toggle("collapsed", !show);
    if (show && this.chartsEl.style.height === "") this.chartsEl.style.height = "300px";
    setTimeout(() => this.renderCharts(), 30);
  }

  stepSection(delta: number): void {
    const s = this.currentSectionSetData;
    if (!s?.sections?.length) return;
    const i = (store.get("currentSectionIndex") + delta + s.sections.length) % s.sections.length;
    store.set("currentSectionIndex", i);
    this.layers.highlightSection(i);
  }

  renderCharts(): void {
    if (this.chartsEl.classList.contains("collapsed")) return;
    const s = this.currentSectionSetData;
    const idx = store.get("currentSectionIndex");
    if (!s || !s.profile) {
      this.profileEl.innerHTML = '<p class="muted" style="padding:20px">Generate profile & cross-sections in the Sections tab.</p>';
      this.sectionEl.innerHTML = "";
      this.stationLabel.textContent = "";
      return;
    }
    const al = store.get("alignments").find((a) => a.id === s.alignment_id);
    const markers = (al?.key_points || []).filter((k: any) => k.kind === "BC" || k.kind === "EC").map((k: any) => ({ chainage: k.chainage, label: `${k.kind} ${k.index}` }));
    const sec = s.sections?.[idx];
    if (sec) markers.push({ chainage: sec.chainage, label: "section" });
    const res = renderProfile(this.profileEl, s.profile, {
      vScale: this.vScale, markers,
      onHover: (p) => store.emit("hover:chainage", p ? { x: p.x, y: p.y, z: p.z ?? 0 } : null),
    });
    this.profileCursor = res.setCursor;
    if (sec) {
      this.stationLabel.textContent = `CH ${fmtChainage(sec.chainage)}  (${idx + 1}/${s.sections!.length})`;
      renderSection(this.sectionEl, sec, { vScale: this.vScale, onHover: (p) => store.emit("hover:chainage", p ? { x: p.x, y: p.y, z: p.z } : null) });
      this.profileCursor?.(sec.chainage);
    }
  }

  // ------------------------------------------------------------------ design modules (hand-off)
  async refreshDesigns(): Promise<void> {
    try {
      const designs = await api.designs.list(this.project.id);
      this.switcherHost.replaceChildren(renderModuleSwitcher(this.project.id, designs, "terrain", (mid) => void this.createDesign(mid)));
    } catch { /* older server without designs */ }
  }

  /** Create a design workspace pinned to the current TIN run (optionally seeded with an alignment) and switch to it. */
  async createDesign(moduleId: string, alignmentId: number | null = store.get("currentAlignment"), suggestedName?: string): Promise<void> {
    const run = store.get("currentRun");
    if (run === null) { toast("Build a TIN first - a design is pinned to a terrain snapshot", "error"); this.showTab("tin"); return; }
    const m = MODULES.find((x) => x.id === moduleId);
    const al = store.get("alignments").find((a) => a.id === alignmentId);
    const name = prompt(`Name for the new ${m?.label ?? moduleId}`, suggestedName ?? (al ? `${al.name} design` : `${m?.label ?? moduleId} 1`));
    if (name === null) return;
    try {
      const d = await api.designs.create(this.project.id, { module: moduleId, name, tin_run_id: run, alignment_id: alignmentId ?? null });
      toast(`${m?.label ?? moduleId} "${d.name}" created on TIN run ${run}`, "ok");
      location.hash = designHash(this.project.id, d);
    } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
  }

  openInRoadDesign(alignmentId: number): void {
    const al = store.get("alignments").find((a) => a.id === alignmentId);
    void this.createDesign("road", alignmentId, al ? `${al.name} road design` : undefined);
  }

  // ------------------------------------------------------------------ misc
  zoomToData(): void {
    const b = this.project.summary?.bounds;
    if (b) this.mv.flyToBounds(b, this.project.summary?.z_range);
  }

  updateStatus(): void {
    const s = this.project.summary;
    const run = store.get("currentRun");
    this.statusInfo.textContent = s ? `${s.points} points · ${Object.values(s.lines || {}).reduce((a, b) => a + b, 0)} lines · TIN ${run ?? "–"} · ${s.contour_sets} contour sets · ${s.alignments} alignments` : "";
  }

  private startPolling(): void {
    const tick = async () => {
      try {
        const r = await api.collab.activity(this.project.id, this.lastActivityId);
        if (r.activity.length) {
          const mine = store.get("user")?.username;
          for (const a of r.activity) if (a.username && a.username !== mine && this.lastActivityId > 0) toast(`${a.username}: ${a.action.replace(/_/g, " ")}`);
          this.lastActivityId = Math.max(this.lastActivityId, ...r.activity.map((a) => a.id));
          const others = r.activity.filter((a) => a.username !== mine);
          if (others.some((a) => a.action.startsWith("alignment") || a.action.startsWith("lock"))) await this.refreshAlignments().then(() => this.activeTab === "alignment" && this.rerenderPanel());
          if (others.some((a) => a.action === "comment" || a.action.startsWith("comment_"))) await this.refreshComments();
          if (others.some((a) => a.action === "tin_done")) await this.refreshTinRuns();
          if (others.some((a) => a.action === "contours_done")) await this.refreshContours();
          if (others.some((a) => a.action === "import")) { await this.refreshPoints(); await this.refreshLines(); }
        }
        store.emit("activity", r);
      } catch { /* offline */ }
    };
    void tick();
    this.pollTimer = window.setInterval(tick, 6000);
  }

  destroy(): void {
    if (this.healthTimer) { window.clearInterval(this.healthTimer); this.healthTimer = null; }
    if (this.pollTimer) window.clearInterval(this.pollTimer);
    this.unsub.forEach((u) => u());
    window.removeEventListener("keydown", this.onKey);
    window.removeEventListener("resize", this.onResize);
    this.interaction?.destroy();
    this.mv?.destroy();
    this.root.innerHTML = "";
  }
}

export type { Cesium };
