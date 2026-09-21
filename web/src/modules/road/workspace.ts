/** Road design workspace.
 *
 * Layout: stage panel (left) | 2-D plan view + status bar (centre) | cross-section (right), with the
 * longitudinal profile docked below. The design (horizontal IPs, vertical PVIs, templates, corridor,
 * structures) is served by the road module API; the terrain snapshot (one TIN run) is read-only.
 * IPs are dragged on the plan, PVIs in the profile; tables in the stage panel edit the same buffers.
 */
import { api, ApiError, type Design, type ModuleInfo, type Project, type ProfilePoint, type Section, type TinRun } from "../../api";
import { renderProfile } from "../../charts/profile";
import { renderSection } from "../../charts/section";
import { helpBinding, registerShortcuts, showShortcutHelp } from "../../ui/keys";
import { clearSelection, mountInspector, ro, selectElement } from "../../ui/selection";
import { store, toast } from "../../state";
import { button, el, fmt, fmtChainage, parseChainage, select } from "../../ui/dom";
import { renderModuleSwitcher } from "../../ui/moduleSwitcher";
import type { ModuleContext, ModuleInstance } from "../registry";
import { designHash, terrainHash } from "../registry";
import { PLAN_TOOLS, PlanView, type PlanLayer, type PlanTool } from "./plan";
import { renderAlignmentStage, renderEarthworksStage, renderOutputStage, renderProfileStage, renderStructuresStage, renderTemplatesStage } from "./stages";

export async function openRoadWorkspace(ctx: ModuleContext): Promise<ModuleInstance> {
  const ws = new RoadWorkspace(ctx);
  await ws.init();
  return ws;
}

/** Densified centreline with cumulative distance, for chainage / offset lookups in the browser. */
interface Centreline { xy: number[][]; ch: number[]; start: number; end: number }

const err = (e: unknown) => toast(e instanceof ApiError ? e.detail : String(e), "error");

export class RoadWorkspace implements ModuleInstance {
  private root: HTMLElement;
  project: Project;
  design: Design;
  pid: string;
  did: number;
  runs: TinRun[] = [];
  run: TinRun | undefined;
  data: { overview?: any; horizontal?: any; vertical?: any; ground?: any; templates?: any; corridor?: any; structures?: any; standards?: any; sheets?: any } = {};
  /** unsaved edit buffers (tables and drag handles write here) */
  editIps: any[] = [];
  editStart = 0;
  editPvis: any[] = [];
  editTemplates: any = null;
  editStructures: any[] = [];
  previewH: any = null;
  /** ground profile under the alignment being dragged (live preview) */
  previewGround: any[] | null = null;
  dirty = new Set<string>();
  station = 0;
  private module: ModuleInfo | null = null;
  private plan!: PlanView;
  private stageHost!: HTMLElement;
  private profileEl!: HTMLElement;
  private sectionEl!: HTMLElement;
  private sectionHead!: HTMLElement;
  private statusEl!: HTMLElement;
  private switcherHost!: HTMLElement;
  private dirtyEl!: HTMLElement;
  private toolbar!: HTMLElement;
  private activeStage = "alignment";
  private sectionInterval = 20;
  private unregisterKeys: (() => void) | null = null;
  private unmountInspector: (() => void) | null = null;
  private sectionTimer: number | null = null;
  /** only the newest showSection() may draw: a slow reply must not overwrite a newer one */
  private sectionToken = 0;
  private halfWidth = 15;
  private centre: Centreline | null = null;
  private cursor: { x: number; y: number; dir: number } | null = null;
  private dragPreview: number[][] | null = null;
  private profileCursor: ((ch: number | null) => void) | null = null;
  private rlTimer: number | null = null;
  private previewTimer: number | null = null;
  private terrainLayers: PlanLayer[] = [];
  private destroyed = false;

  constructor(ctx: ModuleContext) {
    this.root = ctx.root;
    this.project = ctx.project;
    this.design = ctx.design;
    this.pid = ctx.project.id;
    this.did = ctx.design.id;
  }

  destroy(): void {
    this.destroyed = true;
    this.unregisterKeys?.();
    this.unregisterKeys = null;
    this.unmountInspector?.();
    this.unmountInspector = null;
    clearSelection();
    this.plan?.destroy();
    this.root.innerHTML = "";
  }

  // ================================================================== boot and layout
  async init(): Promise<void> {
    this.root.innerHTML = "";
    const user = store.get("user");
    this.switcherHost = el("span", { class: "switcher-host" });
    this.dirtyEl = el("span", { class: "badge warn", style: "display:none" }, "unsaved changes");
    const top = el("header", { class: "topbar" },
      el("div", { class: "brand", onClick: () => { location.hash = ""; }, style: "cursor:pointer", title: "All projects" }, el("span", { style: "color:var(--accent)" }, "▲"), "Picasso LandMesh"),
      el("span", { class: "project-name" }, "project ", el("b", {}, this.project.name), el("span", { class: "badge" }, "Road design"), el("b", { style: "margin-left:6px" }, this.design.name), this.dirtyEl),
      this.switcherHost,
      el("span", { class: "spacer" }),
      el("span", { class: "muted" }, this.project.crs_info?.is_local ? "local grid" : this.project.crs_info?.name || this.project.crs),
      user?.authenticated ? el("span", { class: "muted" }, user.username) : null,
      button("⌨", () => showShortcutHelp(), "btn small"),
      button("Help", () => window.open("/help/road.html", "_blank"), "btn small"),
    );
    this.stageHost = el("aside", { class: "road-side" });
    const planHost = el("div", { class: "plan-wrap" });
    this.statusEl = el("div", { class: "road-status mono" }, "move over the plan");
    this.toolbar = el("div", { class: "plan-toolbar" });
    planHost.append(this.toolbar, this.statusEl);
    this.profileEl = el("div", { class: "road-profile-body" });
    const vsel = select([2, 5, 10, 20].map((v) => ({ value: String(v), label: `V ×${v}` })), String(this.vScale));
    vsel.addEventListener("change", () => { this.vScale = Number(vsel.value); this.renderProfile(); });
    const profile = el("section", { class: "road-profile" }, el("div", { class: "chart-head" }, el("b", {}, "Longitudinal profile"), el("span", { class: "muted" }, "ground from the terrain snapshot · design grade with draggable PVIs"), el("span", { class: "spacer" }), vsel), this.profileEl);
    this.sectionEl = el("div", { class: "road-section-body" });
    this.sectionHead = el("div", { class: "chart-head" });
    const right = el("aside", { class: "road-right" }, this.sectionHead, this.sectionEl);
    const centre = el("div", { class: "road-centre" }, planHost, profile);
    this.root.appendChild(el("div", { class: "app" }, top, el("div", { class: "road-workspace" }, this.stageHost, centre, right)));

    this.unmountInspector = mountInspector(planHost);
    this.plan = new PlanView(planHost);
    this.plan.onMove = (x, y) => this.updateStatus(x, y);
    this.plan.onLeave = () => this.updateStatus();
    this.plan.onClick = (x, y) => {
      const hit = this.plan.hitHandle(...this.plan.toScreen(x, y));
      if (hit && this.activeStage === "alignment") { this.selectIp(Number(hit.id.replace("ip", ""))); return; }
      const s = this.stationAt(x, y);
      if (s) void this.showSection(s.chainage);
    };
    this.plan.onDrag = (id, x, y, phase) => this.onHandleDrag(id, x, y, phase);
    this.plan.onDeleteHandle = (id) => this.deleteIp(id);
    this.plan.onToolChange = () => { this.renderToolbar(); this.updateStatus(); };
    window.addEventListener("beforeunload", this.beforeUnload);
    this.unregisterKeys = registerShortcuts("Road design", this.shortcuts());

    const [runs, designs, modules] = await Promise.all([api.tin.list(this.pid), api.designs.list(this.pid), api.modules.list().catch(() => [] as ModuleInfo[])]);
    if (this.destroyed) return;
    this.runs = runs;
    this.run = runs.find((r) => r.id === this.design.tin_run_id) ?? runs[runs.length - 1];
    this.module = modules.find((m) => m.id === "road") ?? null;
    this.switcherHost.replaceChildren(renderModuleSwitcher(this.pid, designs, this.design.id, (mid) => void this.newDesign(mid)));
    await this.loadTerrainLayers();
    await this.loadAll();
  }

  private beforeUnload = (e: BeforeUnloadEvent) => { if (this.dirty.size) { e.preventDefault(); e.returnValue = ""; } };
  vScale = 5;

  // ================================================================== data
  async loadAll(): Promise<void> {
    const r = api.road;
    const overview = await r.overview(this.pid, this.did);
    const [horizontal, vertical, templates, structures, standards, corridor] = await Promise.all([
      r.horizontal(this.pid, this.did), r.vertical(this.pid, this.did), r.templates(this.pid, this.did),
      r.structures(this.pid, this.did), r.standards(this.pid, this.did),
      overview.has_corridor ? r.corridor(this.pid, this.did).catch(() => null) : Promise.resolve(null),
    ]);
    if (this.destroyed) return;
    Object.assign(this.data, { overview, horizontal, vertical, templates, structures, standards, corridor });
    this.data.ground = horizontal?.ips?.length ? await r.ground(this.pid, this.did, 10).catch(() => null) : null;
    this.resetEdits();
    this.setCentre(horizontal);
    this.station = Math.max(this.station, this.centre?.start ?? 0);
    this.buildPlan();
    this.renderStage();
    this.renderProfile();
    void this.showSection(this.station);
  }

  private resetEdits(): void {
    this.editIps = JSON.parse(JSON.stringify(this.data.horizontal?.ips || []));
    this.editStart = this.data.horizontal?.start_chainage ?? 0;
    this.editPvis = JSON.parse(JSON.stringify(this.data.vertical?.pvis || []));
    this.editTemplates = this.data.templates ? { templates: JSON.parse(JSON.stringify(this.data.templates.templates)), assignments: JSON.parse(JSON.stringify(this.data.templates.assignments)), superelevation: { ...(this.data.templates.superelevation || {}) } } : null;
    this.editStructures = JSON.parse(JSON.stringify(this.data.structures?.structures || []));
    this.previewH = null;
    this.dirty.clear();
    this.dirtyEl.style.display = "none";
  }

  private setCentre(h: any): void {
    const g = h?.geometry;
    this.centre = g?.centreline?.length ? { xy: g.centreline, ch: g.chainages, start: h.start_chainage, end: h.end_chainage } : null;
  }

  markDirty(key: string): void {
    this.dirty.add(key);
    this.dirtyEl.style.display = "";
    this.dirtyEl.textContent = `unsaved: ${[...this.dirty].join(", ")}`;
  }

  private clearDirty(key: string): void {
    this.dirty.delete(key);
    this.dirtyEl.style.display = this.dirty.size ? "" : "none";
    this.dirtyEl.textContent = `unsaved: ${[...this.dirty].join(", ")}`;
  }

  // ================================================================== actions: settings, horizontal
  /** What happens to the grade line when the alignment is saved: stretch | refit | trim | keep. */
  get followMode(): string { return String((this.design.settings as any)?.follow?.vertical ?? "stretch"); }
  /** Rebuild the corridor (and so the cross-sections) right after an alignment or profile change. */
  get autoCorridor(): boolean { return !!(this.design.settings as any)?.follow?.corridor; }

  async setFollow(patch: { vertical?: string; corridor?: boolean }): Promise<void> {
    try {
      const follow = { ...((this.design.settings as any)?.follow || {}), ...patch };
      this.design = await api.designs.patch(this.pid, this.did, { settings: { follow } });
      toast(patch.vertical ? `On alignment change: ${patch.vertical === "keep" ? "keep the grade line" : patch.vertical === "refit" ? "re-fit the grade line to the ground" : patch.vertical === "trim" ? "trim / extend the grade line" : "stretch the grade line"}` : `Automatic corridor rebuild ${patch.corridor ? "on" : "off"}`, "ok");
    } catch (e) { err(e); }
  }

  lastCorridorParams(): Record<string, unknown> {
    const p = this.data.corridor?.summary?.params || {};
    return { interval: p.interval ?? 20, prismoidal: !!p.prismoidal, cut_factor: p.cut_factor ?? 1, fill_factor: p.fill_factor ?? 1, ...(p.swath ? { swath: p.swath } : {}) };
  }

  /** After a geometry change: rebuild the corridor when the design asks for it, otherwise say it is stale. */
  private async afterGeometryChange(): Promise<void> {
    if (this.autoCorridor && this.data.vertical?.pvis?.length >= 2 && (this.data.corridor || this.data.overview?.has_corridor)) await this.buildCorridor(this.lastCorridorParams());
    else if (this.data.corridor) toast("The corridor was built for the previous geometry: rebuild it under Earthworks", "info");
  }

  async followVertical(mode: string): Promise<void> {
    try {
      store.set("busy", "Updating the grade line");
      this.data.vertical = await api.road.followVertical(this.pid, this.did, { mode });
      this.editPvis = JSON.parse(JSON.stringify(this.data.vertical.pvis));
      this.clearDirty("vertical");
      this.data.sheets = undefined;
      this.data.overview = await api.road.overview(this.pid, this.did);
      toast(mode === "refit" ? `Grade line re-fitted: ${this.data.vertical.pvis.length} PVIs` : mode === "trim" ? "Grade line trimmed / extended to the alignment" : "Grade line stretched to the alignment, cut / fill depths kept", "ok");
      this.renderStage();
      this.renderProfile();
      void this.showSection(this.station);
    } catch (e) { err(e); } finally { store.set("busy", null); }
    await this.afterGeometryChange();
  }

  async saveSettings(patch: Record<string, unknown>): Promise<void> {
    try {
      this.design = await api.designs.patch(this.pid, this.did, { settings: patch });
      toast("Design parameters saved", "ok");
      await this.loadAll();
    } catch (e) { err(e); }
  }

  async rebase(runId: number): Promise<void> {
    try {
      this.design = await api.designs.patch(this.pid, this.did, { tin_run_id: runId });
      toast(`Design rebased onto TIN run ${runId}`, "ok");
      await this.init();
    } catch (e) { err(e); }
  }

  previewHorizontal(): void {
    if (this.previewTimer) window.clearTimeout(this.previewTimer);
    this.previewTimer = window.setTimeout(async () => {
      if (this.editIps.length < 2) return;
      try {
        this.previewH = await api.road.previewHorizontal(this.pid, this.did, { ips: this.editIps, start_chainage: this.editStart }, !!this.run);
        this.previewGround = Array.isArray(this.previewH?.ground) && this.previewH.ground.length ? this.previewH.ground : null;
        this.setCentre(this.previewH);
        this.dragPreview = null;
        this.buildPlan();
        this.renderProfile();
        // the centre line moved, so the cross-section at this station is of somewhere else now
        this.refreshSection();
        if (this.activeStage === "alignment") this.renderStage();
      } catch (e) { err(e); }
    }, 120);
  }

  async saveHorizontal(): Promise<void> {
    if (this.editIps.length < 2) { toast("An alignment needs at least two IPs", "error"); return; }
    try {
      this.data.horizontal = await api.road.putHorizontal(this.pid, this.did, { ips: this.editIps, start_chainage: this.editStart, follow: this.followMode });
      this.previewH = null;
      this.previewGround = null;
      this.clearDirty("horizontal");
      this.data.sheets = undefined;
      this.data.ground = await api.road.ground(this.pid, this.did, 10).catch(() => null);
      this.data.vertical = await api.road.vertical(this.pid, this.did);
      this.editPvis = JSON.parse(JSON.stringify(this.data.vertical?.pvis || []));
      this.data.overview = await api.road.overview(this.pid, this.did);
      this.editIps = JSON.parse(JSON.stringify(this.data.horizontal.ips));
      this.setCentre(this.data.horizontal);
      toast(`Alignment saved: ${fmt(this.data.horizontal.length, 1)} m${this.data.horizontal.is_valid ? "" : " (geometry issues, see checks)"}`, this.data.horizontal.is_valid ? "ok" : "error");
      const vf = this.data.horizontal.vertical_follow;
      if (vf?.applied) toast(`Profile updated: ${vf.message}`, "ok");
      else if (vf?.message) toast(vf.message, "error");
      else if (this.data.vertical?.stale) toast("The grade line is out of date: see the Profile stage", "info");
      this.buildPlan();
      this.renderStage();
      this.renderProfile();
      void this.showSection(this.station);
    } catch (e) { err(e); return; }
    await this.afterGeometryChange();
  }

  revertHorizontal(): void {
    this.editIps = JSON.parse(JSON.stringify(this.data.horizontal?.ips || []));
    this.editStart = this.data.horizontal?.start_chainage ?? 0;
    this.previewH = null;
    this.previewGround = null;
    this.clearDirty("horizontal");
    this.setCentre(this.data.horizontal);
    this.buildPlan();
    this.renderStage();
  }

  /** An IP as a selection: the numbers an engineer sets on it. */
  selectIp(i: number): void {
    const p = this.editIps[i];
    if (!p) return;
    selectElement({
      kind: "IP", id: i, label: `IP ${p.label || i}`,
      subtitle: `intersection point ${i + 1} of ${this.editIps.length}`,
      fields: [
        { key: "x", label: "Easting", value: Number(Number(p.x).toFixed(3)), type: "number", unit: "m", step: 0.001 },
        { key: "y", label: "Northing", value: Number(Number(p.y).toFixed(3)), type: "number", unit: "m", step: 0.001 },
        { key: "radius", label: "Radius R", value: Number(p.radius || 0), type: "number", unit: "m", step: 5, min: 0,
          hint: "0 means no curve. The checks compare R with the minimum for the class and design speed." },
        { key: "spiral", label: "Transition Ls", value: Number(p.spiral || 0), type: "number", unit: "m", step: 5, min: 0 },
        { key: "label", label: "Label", value: String(p.label ?? ""), type: "text" },
      ],
      apply: (v) => {
        p.x = Number(v.x); p.y = Number(v.y);
        p.radius = Number(v.radius) || 0;
        p.spiral = Number(v.spiral) || 0;
        p.label = String(v.label ?? "");
        this.markDirty("horizontal");
        this.previewHorizontal();
        this.renderStage();
        this.selectIp(i);
        return `IP ${p.label || i} updated — save the alignment to keep it`;
      },
    });
  }

  private onHandleDrag(id: string, x: number, y: number, phase: "start" | "move" | "end"): void {
    const i = Number(id.replace("ip", ""));
    const p = this.editIps[i];
    if (!p) return;
    if (phase === "start") this.selectIp(i);
    if (phase === "move") {
      p.x = x; p.y = y;
      this.dragPreview = this.editIps.map((q) => [q.x, q.y]);
      this.plan.requestRender();
      return;
    }
    if (phase === "end") {
      p.x = x; p.y = y;
      this.markDirty("horizontal");
      this.previewHorizontal();
    }
  }

  /** The delete tool on the plan removes an IP (the alignment still needs two). */
  private deleteIp(id: string): void {
    if (this.activeStage !== "alignment") { toast("Node editing is on the Alignment stage", "info"); return; }
    const i = Number(id.replace("ip", ""));
    if (!Number.isFinite(i) || !this.editIps[i]) return;
    if (this.editIps.length <= 2) { toast("An alignment needs at least two IPs", "error"); return; }
    const p = this.editIps[i];
    if (!confirm(`Delete IP ${p.label || i}?  The alignment will be re-computed without it; Revert undoes it until you save.`)) return;
    this.editIps.splice(i, 1);
    toast(`IP ${p.label || i} deleted - save the alignment to keep it, or Revert to undo`, "ok");
    this.markDirty("horizontal");
    this.previewHorizontal();
    this.buildPlan();
    this.renderStage();
  }

  // ================================================================== actions: vertical
  async saveVertical(): Promise<void> {
    if (this.editPvis.length < 2) { toast("A vertical alignment needs at least two PVIs", "error"); return; }
    try {
      this.data.vertical = await api.road.putVertical(this.pid, this.did, { pvis: this.editPvis, source: "manual" });
      this.editPvis = JSON.parse(JSON.stringify(this.data.vertical.pvis));
      this.clearDirty("vertical");
      this.data.sheets = undefined;
      this.data.overview = await api.road.overview(this.pid, this.did);
      toast(this.data.vertical.is_valid ? "Profile saved" : "Profile saved with geometry issues (see checks)", this.data.vertical.is_valid ? "ok" : "error");
      this.renderStage();
      this.renderProfile();
      void this.showSection(this.station);
    } catch (e) { err(e); return; }
    await this.afterGeometryChange();
  }

  async autoVertical(spacing: number): Promise<void> {
    try {
      store.set("busy", "Fitting the grade line to the ground");
      this.data.vertical = await api.road.autoVertical(this.pid, this.did, { spacing });
      this.editPvis = JSON.parse(JSON.stringify(this.data.vertical.pvis));
      this.clearDirty("vertical");
      this.data.sheets = undefined;
      this.data.overview = await api.road.overview(this.pid, this.did);
      toast(`Grade line fitted: ${this.data.vertical.pvis.length} PVIs`, "ok");
      this.renderStage();
      this.renderProfile();
      void this.showSection(this.station);
    } catch (e) { err(e); store.set("busy", null); return; } finally { store.set("busy", null); }
    await this.afterGeometryChange();
  }

  revertVertical(): void {
    this.editPvis = JSON.parse(JSON.stringify(this.data.vertical?.pvis || []));
    this.clearDirty("vertical");
    this.renderStage();
    this.renderProfile();
  }

  // ================================================================== actions: templates, corridor, structures
  async saveTemplates(): Promise<void> {
    if (!this.editTemplates) return;
    try {
      this.data.sheets = undefined;
      this.data.templates = await api.road.putTemplates(this.pid, this.did, this.editTemplates);
      this.editTemplates = { templates: JSON.parse(JSON.stringify(this.data.templates.templates)), assignments: JSON.parse(JSON.stringify(this.data.templates.assignments)), superelevation: { ...(this.data.templates.superelevation || {}) } };
      this.clearDirty("templates");
      this.data.horizontal = await api.road.horizontal(this.pid, this.did);  // superelevation table depends on the template settings
      toast("Templates saved", "ok");
      this.renderStage();
    } catch (e) { err(e); }
  }

  revertTemplates(): void {
    this.editTemplates = this.data.templates ? { templates: JSON.parse(JSON.stringify(this.data.templates.templates)), assignments: JSON.parse(JSON.stringify(this.data.templates.assignments)), superelevation: { ...(this.data.templates.superelevation || {}) } } : null;
    this.clearDirty("templates");
    this.renderStage();
  }

  async buildCorridor(params: Record<string, unknown>): Promise<void> {
    if (this.dirty.size) toast(`Unsaved ${[...this.dirty].join(", ")} changes are not used by the corridor`, "info");
    try {
      store.set("busy", "Building the corridor");
      this.data.sheets = undefined;
      this.data.corridor = await api.road.buildCorridor(this.pid, this.did, params);
      this.data.overview = await api.road.overview(this.pid, this.did);
      const t = this.data.corridor.summary.totals;
      toast(`Corridor built: ${this.data.corridor.summary.sections} sections, cut ${fmt(t.cut, 0)} m³, fill ${fmt(t.fill, 0)} m³`, "ok");
      this.buildPlan();
      this.renderStage();
      void this.showSection(this.station);
    } catch (e) { err(e); } finally { store.set("busy", null); }
  }

  volumesCsvUrl(): string { return api.road.volumesCsvUrl(this.pid, this.did); }

  // ================================================================== drawing sheets (Output stage)
  sheetsLoading = false;
  async loadSheets(force = false): Promise<void> {
    if (this.sheetsLoading || (this.data.sheets && !force)) return;
    this.sheetsLoading = true;
    try {
      this.data.sheets = await api.road.sheets(this.pid, this.did);
    } catch (e) { err(e); this.data.sheets = { sheets: [], problems: [String(e)], settings: {} }; }
    finally { this.sheetsLoading = false; }
    if (!this.destroyed && this.activeStage === "output") this.renderStage();
  }

  async saveSheetSettings(body: Record<string, unknown>): Promise<void> {
    try {
      this.data.sheets = await api.road.putSheetSettings(this.pid, this.did, body);
      toast("Sheet settings saved", "ok");
    } catch (e) { err(e); }
    this.renderStage();
  }

  async saveStructures(): Promise<void> {
    try {
      this.data.sheets = undefined;
      this.data.structures = await api.road.putStructures(this.pid, this.did, this.editStructures);
      this.editStructures = JSON.parse(JSON.stringify(this.data.structures.structures));
      this.clearDirty("structures");
      this.data.overview = await api.road.overview(this.pid, this.did);
      toast(`${this.editStructures.length} structure(s) saved`, "ok");
      this.buildPlan();
      this.renderStage();
      this.renderProfile();
    } catch (e) { err(e); }
  }

  revertStructures(): void {
    this.editStructures = JSON.parse(JSON.stringify(this.data.structures?.structures || []));
    this.clearDirty("structures");
    this.renderStage();
  }

  async suggestStructures(params: Record<string, unknown>): Promise<any[]> {
    return (await api.road.suggestStructures(this.pid, this.did, params)).suggestions;
  }

  // ================================================================== plan
  private async loadTerrainLayers(): Promise<void> {
    const layers: PlanLayer[] = [];
    const run = this.run;
    if (!run) { this.terrainLayers = layers; return; }
    const [hull, sets, lines] = await Promise.all([
      api.tin.hull(this.pid, run.id).catch(() => null), api.contours.list(this.pid), api.data.lines(this.pid).catch(() => ({ type: "FeatureCollection", features: [] })),
    ]);
    if (hull && hull.features.length) {
      layers.push({ id: "hull", label: "Terrain extent", visible: true, draw: (ctx, v) => {
        for (const f of hull.features) {
          const rings: number[][][] = f.geometry.coordinates;
          ctx.fillStyle = "#111a2c"; ctx.strokeStyle = "#2a3a55"; ctx.lineWidth = 1;
          for (const ring of rings) { v.path(ctx, ring, true); ctx.fill(); ctx.stroke(); }
        }
      } });
    }
    const set = [...sets].reverse().find((s) => s.run_id === run.id) ?? sets[sets.length - 1];
    if (set) {
      const fc = await api.contours.geojson(this.pid, set.id);
      layers.push({ id: "contours", label: `Contours (${set.params.interval} m)`, visible: true, draw: (ctx, v) => {
        for (const f of fc.features) {
          const major = f.properties.major;
          ctx.strokeStyle = major ? "#5b6b85" : "#2f3d55"; ctx.lineWidth = major ? 1.4 : 0.8;
          v.path(ctx, f.geometry.coordinates); ctx.stroke();
        }
        if (v.scale > 0.5) {
          ctx.fillStyle = "#7c8ba5"; ctx.font = "10px system-ui";
          for (const f of fc.features) {
            if (!f.properties.major) continue;
            const c: number[][] = f.geometry.coordinates;
            const mid = c[Math.floor(c.length / 2)];
            const [sx, sy] = v.toScreen(mid[0], mid[1]);
            ctx.fillText(String(f.properties.label ?? f.properties.level), sx + 2, sy - 2);
          }
        }
      } });
    }
    const KIND_COLORS: Record<string, string> = { feature: "#1f6f8b", boundary: "#8b3a93", void: "#8b5a2b", contour: "#5c7a1f" };
    layers.push({ id: "constraints", label: "Constraint lines", visible: true, draw: (ctx, v) => {
      for (const f of lines.features) {
        ctx.strokeStyle = KIND_COLORS[f.properties.kind] || "#38bdf8"; ctx.lineWidth = f.properties.kind === "boundary" ? 1.6 : 1.1;
        ctx.setLineDash(f.properties.source === "auto" ? [6, 4] : []);
        v.path(ctx, f.geometry.coordinates); ctx.stroke();
      }
      ctx.setLineDash([]);
    } });
    this.terrainLayers = layers;
  }

  private buildPlan(): void {
    const prevVis = new Map(this.plan.layers.map((l) => [l.id, l.visible]));
    const layers: PlanLayer[] = [...this.terrainLayers];
    layers.push({ id: "corridor", label: "Corridor daylight", visible: true, draw: (ctx, v) => {
      const secs: any[] = this.data.corridor?.sections || [];
      if (!secs.length) return;
      for (const side of ["left", "right"] as const) {
        const pts: number[][] = [];
        for (const s of secs) {
          const o = s[side]?.catch_offset;
          if (o == null) continue;
          pts.push([s.x + o * Math.sin(s.direction), s.y - o * Math.cos(s.direction)]);
        }
        ctx.strokeStyle = "#38bdf8"; ctx.lineWidth = 1.2; ctx.setLineDash([5, 4]);
        v.path(ctx, pts); ctx.stroke(); ctx.setLineDash([]);
      }
      // flags: stations where the slope did not catch the ground
      ctx.fillStyle = "#ef4444";
      for (const s of secs) if (s.flags?.length) { const [sx, sy] = v.toScreen(s.x, s.y); ctx.beginPath(); ctx.arc(sx, sy, 4, 0, Math.PI * 2); ctx.fill(); }
    } });
    layers.push({ id: "structures", label: "Structures", visible: true, draw: (ctx, v) => {
      const c = this.centre;
      if (!c) return;
      for (const s of this.editStructures) {
        const sided = s.side === "left" || s.side === "right" || s.side === "both";
        const wall = s.kind.endsWith("wall") || s.kind === "side_drain";
        if (wall && sided) {
          for (const side of (s.side === "both" ? ["left", "right"] : [s.side])) {
            const sgn = side === "left" ? -1 : 1;
            const pts: number[][] = [];
            for (let ch = s.from; ch <= s.to + 1e-6; ch += Math.max((s.to - s.from) / 40, 2)) {
              const p = pointAtChainage(c, Math.min(ch, s.to)); if (!p) continue;
              const off = sgn * 5;
              pts.push([p.x + off * Math.sin(p.dir), p.y - off * Math.cos(p.dir)]);
            }
            ctx.strokeStyle = s.kind === "side_drain" ? "#22d3ee" : s.kind === "breast_wall" ? "#a78bfa" : "#f472b6"; ctx.lineWidth = 4;
            v.path(ctx, pts); ctx.stroke();
          }
        } else {
          const p = pointAtChainage(c, s.from); if (!p) continue;
          const nx = -Math.sin(p.dir), ny = Math.cos(p.dir), L = 12;
          ctx.strokeStyle = "#22d3ee"; ctx.lineWidth = 3;
          v.path(ctx, [[p.x - nx * L, p.y - ny * L], [p.x + nx * L, p.y + ny * L]]); ctx.stroke();
          const [sx, sy] = v.toScreen(p.x + nx * L, p.y + ny * L);
          ctx.fillStyle = "#a5f3fc"; ctx.font = "10px system-ui"; ctx.fillText(s.kind === "culvert" ? "culvert" : s.kind.replace(/_/g, " "), sx + 3, sy);
        }
      }
    } });
    layers.push({ id: "alignment", label: "Alignment", visible: true, draw: (ctx, v) => {
      const c = this.centre;
      const h = this.previewH ?? this.data.horizontal;
      if (this.dragPreview) { ctx.strokeStyle = "#fde68a"; ctx.lineWidth = 1; ctx.setLineDash([4, 4]); v.path(ctx, this.dragPreview); ctx.stroke(); ctx.setLineDash([]); }
      if (!c) return;
      ctx.strokeStyle = this.previewH ? "#fbbf24" : "#f59e0b"; ctx.lineWidth = 2.5; v.path(ctx, c.xy); ctx.stroke();
      ctx.strokeStyle = "#fde68a"; ctx.lineWidth = 1; ctx.fillStyle = "#fde68a"; ctx.font = "11px system-ui";
      const tick = v.scale > 0.15 ? 100 : v.scale > 0.03 ? 500 : 1000;
      for (let ch = Math.ceil(c.start / tick) * tick; ch <= c.end; ch += tick) {
        const s = pointAtChainage(c, ch); if (!s) continue;
        const nx = -Math.sin(s.dir), ny = Math.cos(s.dir), L = 6 / v.scale;
        v.path(ctx, [[s.x - nx * L, s.y - ny * L], [s.x + nx * L, s.y + ny * L]]); ctx.stroke();
        if (ch % (tick * 5) === 0 || tick >= 500) { const [sx, sy] = v.toScreen(s.x + nx * L * 1.5, s.y + ny * L * 1.5); ctx.fillText(fmtChainage(ch, 0), sx + 2, sy); }
      }
      // tangent lines between IPs and key points
      const ips = this.editIps;
      if (ips.length > 1) { ctx.strokeStyle = "rgba(251,113,133,.5)"; ctx.lineWidth = 1; ctx.setLineDash([3, 5]); v.path(ctx, ips.map((p) => [p.x, p.y])); ctx.stroke(); ctx.setLineDash([]); }
      for (const k of h?.geometry?.key_points || []) {
        if (k.kind === "IP") continue;
        const [sx, sy] = v.toScreen(k.x, k.y);
        ctx.fillStyle = k.kind === "SC" || k.kind === "CS" ? "#a7f3d0" : "#34d399"; ctx.beginPath(); ctx.arc(sx, sy, 3, 0, Math.PI * 2); ctx.fill();
        if (v.scale > 0.3) { ctx.fillStyle = "#6ee7b7"; ctx.font = "10px system-ui"; ctx.fillText(k.kind, sx + 4, sy + 10); }
      }
    } });
    layers.push({ id: "cursor", label: "Station", visible: true, draw: (ctx, v) => {
      const s = this.cursor; if (!s) return;
      const nx = -Math.sin(s.dir), ny = Math.cos(s.dir), hw = this.halfWidth;
      ctx.strokeStyle = "#38bdf8"; ctx.lineWidth = 1.5;
      v.path(ctx, [[s.x - nx * hw, s.y - ny * hw], [s.x + nx * hw, s.y + ny * hw]]); ctx.stroke();
      const [sx, sy] = v.toScreen(s.x, s.y); ctx.fillStyle = "#38bdf8"; ctx.beginPath(); ctx.arc(sx, sy, 4, 0, Math.PI * 2); ctx.fill();
    } });
    for (const l of layers) if (prevVis.has(l.id)) l.visible = prevVis.get(l.id)!;
    this.plan.layers = layers;
    // IP handles are draggable on the alignment stage
    this.plan.handles = this.activeStage === "alignment" ? this.editIps.map((p, i) => ({ id: `ip${i}`, x: p.x, y: p.y, label: p.label || String(i) })) : [];
    this.renderToolbar();
    this.updateStatus();
    if (!this.plan.layers.length || this.plan.scale === 1) this.fitAll();
    this.plan.requestRender();
  }

  /** Tool palette, layer chips and the fit buttons above the plan. */
  private renderToolbar(): void {
    this.toolbar.innerHTML = "";
    const tools = el("div", { class: "tool-palette" });
    for (const t of PLAN_TOOLS) {
      const active = this.plan.tool === t.id;
      const b = button(`${t.icon} ${t.label}`, () => this.plan.setTool(t.id), `tool-btn${active ? " active" : ""}`);
      b.title = `${t.hint}  (${t.key})`;
      if (t.id === "delete" && this.activeStage !== "alignment") { b.disabled = true; b.title = "Node editing is on the Alignment stage"; }
      tools.appendChild(b);
    }
    this.toolbar.appendChild(tools);
    for (const l of this.plan.layers.filter((x) => x.id !== "cursor")) {
      const cb = el("input", { type: "checkbox", checked: l.visible });
      cb.addEventListener("change", () => { l.visible = cb.checked; this.plan.requestRender(); });
      this.toolbar.appendChild(el("label", { class: "check chip" }, cb, l.label));
    }
    this.toolbar.appendChild(button("Fit", () => this.fitAll(), "btn small"));
    if (this.centre) this.toolbar.appendChild(button("Fit alignment", () => this.fitAlignment(), "btn small"));
  }

  private fitted = false;
  private fitAll(): void {
    const b = this.run && this.run.bounds.every((x) => x != null) ? (this.run.bounds as number[]) : this.project.summary?.bounds;
    if (b) { this.plan.fit(b); this.fitted = true; }
  }

  fitAlignment(): void { if (this.centre) this.plan.fit(polyBounds(this.centre.xy)); }

  // ================================================================== status / chainage
  private stationAt(x: number, y: number): { chainage: number; offset: number; x: number; y: number; dir: number } | null {
    const c = this.centre;
    if (!c) return null;
    let best = Infinity, bch = 0, bx = 0, by = 0, bdir = 0, boff = 0;
    for (let i = 0; i + 1 < c.xy.length; i++) {
      const [ax, ay] = c.xy[i], [bx2, by2] = c.xy[i + 1];
      const vx = bx2 - ax, vy = by2 - ay, L2 = vx * vx + vy * vy || 1e-12;
      const t = Math.min(1, Math.max(0, ((x - ax) * vx + (y - ay) * vy) / L2));
      const px = ax + t * vx, py = ay + t * vy;
      const d2 = (px - x) ** 2 + (py - y) ** 2;
      if (d2 < best) { best = d2; bch = c.ch[i] + t * (c.ch[i + 1] - c.ch[i]); bx = px; by = py; bdir = Math.atan2(vy, vx); boff = Math.sign(vx * (y - ay) - vy * (x - ax)) * Math.sqrt(d2); }
    }
    return { chainage: bch, offset: -boff, x: bx, y: by, dir: bdir };
  }

  /** Coordinates under the pointer, always prefixed by the tool in hand. Called with no position
   *  when only the tool changed, or when the pointer leaves the plan. */
  private updateStatus(x?: number, y?: number): void {
    const t = PLAN_TOOLS.find((k) => k.id === this.plan.tool);
    const tool = t ? `${t.icon} ${t.label}` : "";
    if (x === undefined || y === undefined) {
      this.statusEl.textContent = t ? `${tool}  \u2014  ${t.hint}` : "move over the plan";
      return;
    }
    const parts = [tool, `E ${fmt(x, 2)}`, `N ${fmt(y, 2)}`];
    const s = this.stationAt(x, y);
    if (s && Math.abs(s.offset) < 500) {
      parts.push(`CH ${fmtChainage(s.chainage)}`, `offset ${s.offset >= 0 ? "R" : "L"} ${fmt(Math.abs(s.offset), 2)}`);
      const v = this.data.vertical;
      if (v?.line?.length) { const dz = interpLine(v.line, s.chainage); if (dz != null) parts.push(`design RL ${fmt(dz, 3)}`); }
    }
    this.statusEl.textContent = parts.join("   ");
    if (this.run) {
      if (this.rlTimer) window.clearTimeout(this.rlTimer);
      this.rlTimer = window.setTimeout(async () => {
        try {
          const r = await api.tin.elevation(this.pid, this.run!.id, x, y);
          if (!this.destroyed && this.statusEl.textContent?.startsWith(`E ${fmt(x, 2)}`)) this.statusEl.textContent += `   ground RL ${r.inside ? fmt(r.z, 3) : "outside TIN"}`;
        } catch { /* ignore */ }
      }, 180);
    }
  }

  // ================================================================== profile
  renderProfile(): void {
    const ground: ProfilePoint[] = (this.previewGround ?? this.data.ground?.points ?? []).map((p: any) => ({ chainage: p.chainage, x: p.x, y: p.y, z: p.z, source: "edge" }));
    const v = this.data.vertical;
    if (!ground.length && !v?.line?.length) {
      this.profileEl.innerHTML = `<p class="muted" style="padding:20px">${!this.run ? "No TIN run: build a TIN in the terrain workspace first." : "No alignment yet: add IPs under Alignment (or seed the design from a project alignment)."}</p>`;
      return;
    }
    const info = new Map<number, any>((v?.table || []).map((r: any) => [r.index, r]));
    const pvis = this.editPvis.map((p, i) => ({ index: i, chainage: p.chainage, z: p.elevation, length: p.length || 0, K: info.get(i)?.K ?? null, kind: info.get(i)?.kind }));
    const markers = (this.data.horizontal?.geometry?.key_points || []).filter((k: any) => ["TS", "BC", "ST", "EC"].includes(k.kind)).map((k: any) => ({ chainage: k.chainage, label: k.kind }));
    for (const s of this.editStructures) if (!s.kind.endsWith("wall") && s.kind !== "side_drain") markers.push({ chainage: s.from, label: s.kind === "culvert" ? "culvert" : "drain" });
    const res = renderProfile(this.profileEl, ground, {
      vScale: this.vScale, markers, designLine: v?.line, pvis,
      domain: this.centre ? [this.centre.start, this.centre.end] : undefined,
      onHover: (p) => this.setCursor(p ? p.chainage : null),
      onPviDrag: (i, ch, z, phase) => {
        const p = this.editPvis[i]; if (!p) return;
        p.chainage = Math.round(ch * 100) / 100; p.elevation = Math.round(z * 1000) / 1000;
        if (phase === "end") { this.markDirty("vertical"); void this.saveVertical(); }
      },
    });
    this.profileCursor = res.setCursor;
  }

  private setCursor(chainage: number | null): void {
    if (!this.centre || chainage === null) { this.cursor = null; this.plan.requestRender(); return; }
    this.cursor = pointAtChainage(this.centre, chainage);
    this.plan.requestRender();
  }

  // ================================================================== section
  setStation(ch: number): void { void this.showSection(ch); }

  /** Redraw the current station shortly, collapsing a burst of edits into one request. */
  refreshSection(delay = 400): void {
    if (this.sectionTimer) window.clearTimeout(this.sectionTimer);
    this.sectionTimer = window.setTimeout(() => { this.sectionTimer = null; void this.showSection(this.station); }, delay);
  }

  async showSection(chainage: number): Promise<void> {
    const c = this.centre;
    // clamp first: the head below prints this.station, and printing it before the update showed
    // the previous station on every step
    if (c) this.station = Math.min(Math.max(chainage, c.start), c.end);
    this.sectionHead.replaceChildren(
      el("b", {}, "Cross-section"),
      button("◀", () => void this.showSection(this.station - this.sectionInterval), "btn small"),
      el("span", { class: "mono" }, c ? `CH ${fmtChainage(this.station)}` : "-"),
      button("▶", () => void this.showSection(this.station + this.sectionInterval), "btn small"),
      el("span", { class: "spacer" }),
      (() => { const i = select([10, 20, 25, 50].map((v) => ({ value: String(v), label: `every ${v} m` })), String(this.sectionInterval)); i.addEventListener("change", () => { this.sectionInterval = Number(i.value); }); return i; })(),
      (() => { const w = select([10, 15, 20, 30, 50].map((v) => ({ value: String(v), label: `±${v} m` })), String(this.halfWidth)); w.addEventListener("change", () => { this.halfWidth = Number(w.value); void this.showSection(this.station); }); return w; })(),
    );
    if (!c || !this.run) { this.sectionEl.innerHTML = '<p class="muted" style="padding:20px">Needs a TIN run and an alignment.</p>'; return; }
    const token = ++this.sectionToken;
    const superseded = () => this.destroyed || token !== this.sectionToken;
    const s = pointAtChainage(c, this.station);
    if (!s) return;
    this.cursor = s;
    this.plan.requestRender();
    this.profileCursor?.(this.station);
    try {
      // an unsaved alignment means the stored corridor belongs to the previous centre line, so fall
      // through to the live ground section rather than draw yesterday's geometry at today's chainage
      if (this.data.corridor && !this.dirty.has("horizontal")) {
        const sec = await api.road.section(this.pid, this.did, this.station);
        if (superseded()) return;
        const ground: number[][] = sec.ground;
        const hw = Math.max(this.halfWidth, ...sec.design.map((p: number[]) => Math.abs(p[0])));
        const section: Section = { chainage: sec.chainage, label: fmtChainage(sec.chainage), centre: [sec.x, sec.y], direction: sec.direction, left: hw, right: hw,
          offset: ground.map((p) => p[0]), z: ground.map((p) => p[1]), xy: ground.map((p) => [sec.x + p[0] * Math.sin(sec.direction), sec.y - p[0] * Math.cos(sec.direction)]),
          source: ground.map((p) => (Math.abs(p[0]) < 1e-6 ? "centre" : "edge")) };
        renderSection(this.sectionEl, section, { vScale: 2, design: sec.design.map((p: number[]) => ({ offset: p[0], z: p[1] })),
          structures: sec.structures || [],
          title: `CH ${fmtChainage(sec.chainage)} · ${sec.template_id} · cut ${fmt(sec.cut_area, 2)} m² · fill ${fmt(sec.fill_area, 2)} m²${sec.flags?.length ? " · " + sec.flags.join(", ") : ""}` });
        return;
      }
      const nx = -Math.sin(s.dir), ny = Math.cos(s.dir), hw = this.halfWidth;
      const line = [[s.x + nx * hw, s.y + ny * hw], [s.x, s.y], [s.x - nx * hw, s.y - ny * hw]];
      const r = await api.tin.profile(this.pid, this.run.id, line);
      if (superseded()) return;
      const sec: Section = { chainage: this.station, label: fmtChainage(this.station), centre: [s.x, s.y], direction: s.dir, left: hw, right: hw,
        offset: r.distance.map((d) => d - hw), z: r.z, xy: r.xy, source: r.distance.map((d) => (Math.abs(d - hw) < 1e-6 ? "centre" : "edge")) };
      const v = this.data.vertical;
      const dz = v?.line?.length ? interpLine(v.line, this.station) : null;
      const stale = this.dirty.has("horizontal") ? " · alignment unsaved: ground under the new centre line" : "";
      renderSection(this.sectionEl, sec, { vScale: 2, formationLevel: dz ?? undefined, title: `CH ${fmtChainage(this.station)} · ground${dz != null ? ` · design RL ${fmt(dz, 2)} (build the corridor for the full section)` : ""}${stale}` });
    } catch (e) {
      this.sectionEl.innerHTML = `<p class="error" style="padding:20px">${e instanceof ApiError ? e.detail : String(e)}</p>`;
    }
  }

  // ================================================================== keyboard
  /** What the keyboard does here. The help card (?) is built from these labels. */
  private shortcuts() {
    const V = [2, 5, 10, 20];
    const W = [10, 15, 20, 30, 50];
    const stageAt = (i: number) => {
      const st = this.module?.stages || [];
      if (i < st.length) { this.activeStage = st[i].id; this.renderStage(); this.buildPlan(); }
    };
    return [
      helpBinding(),
      { keys: ["arrowleft"], show: "←", label: "Previous station", group: "Cross-section", run: () => this.stepStation(-1) },
      { keys: ["arrowright"], show: "→", label: "Next station", group: "Cross-section", run: () => this.stepStation(1) },
      { keys: ["shift+arrowleft"], show: "Shift ←", label: "Back five stations", group: "Cross-section", run: () => this.stepStation(-5) },
      { keys: ["shift+arrowright"], show: "Shift →", label: "Forward five stations", group: "Cross-section", run: () => this.stepStation(5) },
      { keys: ["pageup"], show: "", label: "Back five stations", group: "Cross-section", run: () => this.stepStation(-5) },
      { keys: ["pagedown"], show: "", label: "Forward five stations", group: "Cross-section", run: () => this.stepStation(5) },
      { keys: ["home"], label: "Start of the alignment", group: "Cross-section", run: () => { if (this.centre) void this.showSection(this.centre.start); } },
      { keys: ["end"], label: "End of the alignment", group: "Cross-section", run: () => { if (this.centre) void this.showSection(this.centre.end); } },
      { keys: ["g"], label: "Go to chainage…", group: "Cross-section", run: () => this.promptChainage() },
      { keys: ["["], label: "Narrower section", group: "Cross-section", run: () => this.stepHalfWidth(W, -1) },
      { keys: ["]"], label: "Wider section", group: "Cross-section", run: () => this.stepHalfWidth(W, 1) },
      { keys: ["+", "="], show: "+", label: "More vertical exaggeration (profile)", group: "Cross-section", run: () => this.stepVScale(V, 1) },
      { keys: ["-"], show: "−", label: "Less vertical exaggeration (profile)", group: "Cross-section", run: () => this.stepVScale(V, -1) },
      ...[1, 2, 3, 4, 5, 6, 7].map((n) => ({
        keys: [String(n)], label: `Stage ${n}: ${(this.module?.stages || [])[n - 1]?.label ?? "-"}`, group: "Stages",
        run: () => stageAt(n - 1),
      })),
      ...PLAN_TOOLS.map((t) => ({
        keys: [t.key.toLowerCase()], label: `${t.label} tool \u2014 ${t.hint}`, group: "Plan tools",
        run: () => this.plan.setTool(t.id as PlanTool),
      })),
      { keys: ["mod+s"], label: "Save everything unsaved on this design", group: "Design", whileTyping: true, run: () => void this.saveDirty() },
      { keys: ["escape"], show: "Esc", label: "Back to the Select / move tool", group: "Plan tools", run: () => this.plan.setTool("select") },
    ];
  }

  /** Move by whole section intervals, stopping at the ends of the alignment. */
  private stepStation(steps: number): void {
    const c = this.centre;
    if (!c) return;
    const next = Math.min(Math.max(this.station + steps * this.sectionInterval, c.start), c.end);
    if (Math.abs(next - this.station) > 1e-9) void this.showSection(next);
  }

  private promptChainage(): void {
    const c = this.centre;
    if (!c) { toast("This design has no alignment yet", "error"); return; }
    const answer = prompt(`Go to chainage (${fmtChainage(c.start)} – ${fmtChainage(c.end)})`, "");
    if (answer === null || !answer.trim()) return;
    const ch = parseChainage(answer.trim());
    if (Number.isNaN(ch)) { toast(`Cannot read "${answer}" as a chainage`, "error"); return; }
    void this.showSection(ch);
  }

  private stepVScale(scales: number[], delta: number): void {
    const i = Math.min(Math.max(scales.indexOf(this.vScale) + delta, 0), scales.length - 1);
    this.vScale = scales[i];
    this.renderProfile();
  }

  private stepHalfWidth(widths: number[], delta: number): void {
    const i = Math.min(Math.max(widths.indexOf(this.halfWidth) + delta, 0), widths.length - 1);
    this.halfWidth = widths[i];
    void this.showSection(this.station);
  }

  /** Ctrl+S: save whatever the design has outstanding, in dependency order. */
  async saveDirty(): Promise<void> {
    if (!this.dirty.size) { toast("Nothing to save", "info"); return; }
    const order: [string, () => Promise<void>][] = [
      ["horizontal", () => this.saveHorizontal()],
      ["vertical", () => this.saveVertical()],
      ["templates", () => this.saveTemplates()],
      ["structures", () => this.saveStructures()],
    ];
    for (const [key, save] of order) if (this.dirty.has(key)) await save();
  }

  // ================================================================== stages
  renderStage(): void {
    const host = this.stageHost;
    host.innerHTML = "";
    const stages = this.module?.stages?.length ? this.module.stages : [{ id: "alignment", label: "Alignment", description: "" }];
    const status: Record<string, any> = this.data.overview?.stages || {};
    const list = el("div", { class: "stage-list" });
    stages.forEach((s, i) => {
      const st = status[s.id];
      const badge = this.dirty.has(s.id === "profile" ? "vertical" : s.id === "alignment" ? "horizontal" : s.id) ? el("span", { class: "badge warn" }, "unsaved")
        : st ? el("span", { class: `badge ${st.status === "done" ? "ok" : st.status === "stale" ? "warn" : ""}` }, st.status) : null;
      list.appendChild(el("div", { class: `stage${s.id === this.activeStage ? " active" : ""}`, title: st?.detail || s.description, onClick: () => { this.activeStage = s.id; this.renderStage(); this.buildPlan(); } },
        el("span", { class: "num" }, String(i + 1)), el("span", { class: "label" }, s.label), el("span", { class: "spacer" }), badge));
    });
    host.append(el("h3", {}, "Design stages"), list);
    const content = el("div");
    host.appendChild(content);
    switch (this.activeStage) {
      case "alignment": renderAlignmentStage(content, this); break;
      case "profile": renderProfileStage(content, this); break;
      case "templates": renderTemplatesStage(content, this); break;
      case "earthworks": renderEarthworksStage(content, this); break;
      case "structures": renderStructuresStage(content, this, false); break;
      case "drainage": renderStructuresStage(content, this, true); break;
      case "output": renderOutputStage(content, this); break;
      default: content.appendChild(el("p", { class: "muted" }, "Unknown stage"));
    }
    content.appendChild(el("div", { class: "btn-row", style: "margin-top:8px" }, button("Open terrain workspace", () => { location.hash = terrainHash(this.pid); }, "btn small")));
  }

  // ================================================================== new design from the switcher
  private async newDesign(moduleId: string): Promise<void> {
    if (!this.run) { toast("Build a TIN first", "error"); return; }
    const name = prompt("Name for the new design", `${moduleId} design`);
    if (name === null) return;
    try {
      const d = await api.designs.create(this.pid, { module: moduleId, name, tin_run_id: this.run.id, alignment_id: this.design.alignment_id });
      location.hash = designHash(this.pid, d);
    } catch (e) { err(e); }
  }
}

// ---------------------------------------------------------------------- geometry helpers
function pointAtChainage(c: Centreline, chainage: number): { x: number; y: number; dir: number } | null {
  if (c.xy.length < 2 || chainage < c.start - 1e-6 || chainage > c.end + 1e-6) return null;
  let i = 0;
  while (i + 2 < c.xy.length && c.ch[i + 1] < chainage) i++;
  const [ax, ay] = c.xy[i], [bx, by] = c.xy[i + 1];
  const L = c.ch[i + 1] - c.ch[i] || 1e-12;
  const f = Math.min(1, Math.max(0, (chainage - c.ch[i]) / L));
  return { x: ax + f * (bx - ax), y: ay + f * (by - ay), dir: Math.atan2(by - ay, bx - ax) };
}

function interpLine(line: { chainage: number; z: number }[], ch: number): number | null {
  if (!line.length || ch < line[0].chainage || ch > line[line.length - 1].chainage) return null;
  let lo = 0, hi = line.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (line[m].chainage <= ch) lo = m; else hi = m; }
  const a = line[lo], b = line[hi];
  const t = (ch - a.chainage) / ((b.chainage - a.chainage) || 1);
  return a.z + t * (b.z - a.z);
}

function polyBounds(xy: number[][]): number[] {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of xy) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
  return [x0, y0, x1, y1];
}
