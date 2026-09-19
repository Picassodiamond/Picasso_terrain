/** Project workspace: top bar, side panels, Cesium map, charts pane, polling. */
import * as Cesium from "cesium";
import { api, type Alignment, type Comment, type Project, type SectionSet } from "../api";
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

type Tab = "data" | "tin" | "contours" | "alignment" | "sections" | "export" | "team" | "settings";
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
  private tinVertices: { xyz: Float64Array; grid: Map<string, number[]>; cell: number } | null = null;
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
    store.set("project", project);
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
    const chartsBtn = button("Charts", () => this.toggleCharts(), "btn small");
    const top = el("header", { class: "topbar" },
      el("div", { class: "brand", onClick: () => this.onClose(), style: "cursor:pointer", title: "All projects" }, el("span", { style: "color:var(--accent)" }, "▲"), "Picasso LandMesh"),
      el("span", { class: "project-name" }, "project ", el("b", {}, this.project.name), el("span", { class: "badge" }, this.project.crs_info?.is_local ? "local grid" : this.project.crs_info?.name || this.project.crs)),
      el("span", { class: "spacer" }),
      el("span", { class: "muted" }, "Base map"), baseSel, baseHint,
      el("span", { class: "muted" }, "View"), modeSel,
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
    this.updateStatus();
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
    if (picked?.type === "point") toast(`Point ${picked.id || picked.fid}: RL ${fmt(picked.z)} ${picked.remark ? "· " + picked.remark : ""}`);
    else if (picked?.type === "section") { store.set("currentSectionIndex", picked.index); this.showCharts(true); }
    else if (picked?.type === "comment") { this.showTab("team"); }
    else if (picked?.type === "ip") { this.showTab("alignment"); }
    void p;
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
    this.updateStatus();
  }

  async refreshPoints(): Promise<void> {
    await this.refreshProject();
    const fc = await api.data.points(this.project.id);
    this.layers.setPoints(fc, store.get("layers").pointLabels);
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
      const buf = await api.tin.mesh(this.project.id, run);
      this.indexTin(buf);
      this.layers.setTin(buf, store.get("tinStyle"));
      const r = store.get("tinRuns").find((x) => x.id === run);
      const zr = r?.z_range ?? [0, 0];
      const b = r?.bounds;
      this.mv.setPickTarget(this.layers.tin, ((zr[0] ?? 0) + (zr[1] ?? 0)) / 2, b && b[0] != null ? [((b[0] ?? 0) + (b[2] ?? 0)) / 2, ((b[1] ?? 0) + (b[3] ?? 0)) / 2] : null);
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
    if (run === null) return;
    void api.tin.mesh(this.project.id, run).then((buf) => {
      this.layers.setTin(buf, store.get("tinStyle"));
      const r = store.get("tinRuns").find((x) => x.id === run);
      const zr = r?.z_range ?? [0, 0];
      this.mv.setPickTarget(this.layers.tin, ((zr[0] ?? 0) + (zr[1] ?? 0)) / 2, null);
    });
  }

  private indexTin(buf: ArrayBuffer): void {
    const dv = new DataView(buf);
    const n = dv.getUint32(8, true), dtype = dv.getUint32(16, true);
    const ox = dv.getFloat64(20, true), oy = dv.getFloat64(28, true), oz = dv.getFloat64(36, true);
    const xyz = new Float64Array(n * 3);
    if (dtype === 0) { const f = new Float32Array(buf, 44, n * 3); for (let i = 0; i < n; i++) { xyz[i * 3] = f[i * 3] + ox; xyz[i * 3 + 1] = f[i * 3 + 1] + oy; xyz[i * 3 + 2] = f[i * 3 + 2] + oz; } }
    else xyz.set(new Float64Array(buf.slice(44, 44 + n * 24)));
    let minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity;
    for (let i = 0; i < n; i++) { minx = Math.min(minx, xyz[i * 3]); maxx = Math.max(maxx, xyz[i * 3]); miny = Math.min(miny, xyz[i * 3 + 1]); maxy = Math.max(maxy, xyz[i * 3 + 1]); }
    const cell = Math.max(1, Math.sqrt(((maxx - minx) * (maxy - miny)) / Math.max(1, n)) * 3);
    const grid = new Map<string, number[]>();
    for (let i = 0; i < n; i++) {
      const k = `${Math.floor(xyz[i * 3] / cell)},${Math.floor(xyz[i * 3 + 1] / cell)}`;
      (grid.get(k) ?? grid.set(k, []).get(k)!).push(i);
    }
    this.tinVertices = { xyz, grid, cell };
  }

  /** Approximate ground elevation (nearest TIN vertex) for placing labels/markers. */
  zAt = (x: number, y: number): number => {
    const t = this.tinVertices;
    if (!t) return 0;
    const cx = Math.floor(x / t.cell), cy = Math.floor(y / t.cell);
    let best = Infinity, bz = 0;
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
      const ids = t.grid.get(`${cx + dx},${cy + dy}`);
      if (!ids) continue;
      for (const i of ids) { const d = (t.xyz[i * 3] - x) ** 2 + (t.xyz[i * 3 + 1] - y) ** 2; if (d < best) { best = d; bz = t.xyz[i * 3 + 2]; } }
    }
    if (best === Infinity) { // fall back to any vertex
      for (let i = 0; i < t.xyz.length / 3; i++) { const d = (t.xyz[i * 3] - x) ** 2 + (t.xyz[i * 3 + 1] - y) ** 2; if (d < best) { best = d; bz = t.xyz[i * 3 + 2]; } }
    }
    return bz;
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
