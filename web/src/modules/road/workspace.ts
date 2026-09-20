/** Road design workspace (shell).
 *
 * Layout: stage panel (left) | 2-D plan view + status bar (centre) | cross-section (right), with the
 * ground profile docked below. Everything shown is read from the terrain snapshot the design is
 * pinned to (one TIN run) through the existing terrain endpoints. The road engine (standards checks,
 * vertical alignment, templates, earthworks, structures, drainage) plugs into the stage panel later.
 */
import { api, ApiError, type Alignment, type Design, type ModuleInfo, type Project, type ProfilePoint, type Section, type TinRun } from "../../api";
import { renderProfile } from "../../charts/profile";
import { renderSection } from "../../charts/section";
import { densify } from "../../geom/alignment";
import { store, toast } from "../../state";
import { button, el, field, fmt, fmtChainage, select } from "../../ui/dom";
import { renderModuleSwitcher } from "../../ui/moduleSwitcher";
import type { ModuleContext, ModuleInstance } from "../registry";
import { designHash, terrainHash } from "../registry";
import { PlanView, type PlanLayer } from "./plan";

export async function openRoadWorkspace(ctx: ModuleContext): Promise<ModuleInstance> {
  const ws = new RoadWorkspace(ctx);
  await ws.init();
  return ws;
}

/** Densified centreline with cumulative distance, for chainage / offset lookups in the browser. */
interface Centreline { xy: number[][]; d: number[]; length: number; start: number }

const ROAD_CLASSES = [["national", "National Highway"], ["feeder", "Feeder Road"], ["district", "District Road"], ["village", "Village Road"]];
const TERRAINS = [["plain", "Plain"], ["rolling", "Rolling"], ["mountainous", "Mountainous"], ["steep", "Steep"]];
const SPEEDS = [100, 80, 60, 50, 40, 30, 25, 20];

class RoadWorkspace implements ModuleInstance {
  private root: HTMLElement;
  project: Project;
  design: Design;
  private runs: TinRun[] = [];
  private run: TinRun | undefined;
  private alignment: Alignment | null = null;
  private centre: Centreline | null = null;
  private module: ModuleInfo | null = null;
  private plan!: PlanView;
  private stageHost!: HTMLElement;
  private profileEl!: HTMLElement;
  private sectionEl!: HTMLElement;
  private sectionHead!: HTMLElement;
  private statusEl!: HTMLElement;
  private switcherHost!: HTMLElement;
  private activeStage = "alignment";
  private station = 0;
  private sectionInterval = 20;
  private halfWidth = 15;
  private cursor: { x: number; y: number; dir: number } | null = null;
  private profileCursor: ((ch: number | null) => void) | null = null;
  private rlTimer: number | null = null;
  private destroyed = false;

  constructor(ctx: ModuleContext) {
    this.root = ctx.root;
    this.project = ctx.project;
    this.design = ctx.design;
  }

  destroy(): void {
    this.destroyed = true;
    this.plan?.destroy();
    this.root.innerHTML = "";
  }

  // ------------------------------------------------------------------ boot
  async init(): Promise<void> {
    this.root.innerHTML = "";
    const user = store.get("user");
    const pid = this.project.id;
    this.switcherHost = el("span", { class: "switcher-host" });
    const top = el("header", { class: "topbar" },
      el("div", { class: "brand", onClick: () => { location.hash = ""; }, style: "cursor:pointer", title: "All projects" }, el("span", { style: "color:var(--accent)" }, "▲"), "Picasso LandMesh"),
      el("span", { class: "project-name" }, "project ", el("b", {}, this.project.name), el("span", { class: "badge" }, "Road design"), el("b", { style: "margin-left:6px" }, this.design.name)),
      this.switcherHost,
      el("span", { class: "spacer" }),
      el("span", { class: "muted" }, this.project.crs_info?.is_local ? "local grid" : this.project.crs_info?.name || this.project.crs),
      user?.authenticated ? el("span", { class: "muted" }, user.username) : null,
    );
    this.stageHost = el("aside", { class: "road-side" });
    const planHost = el("div", { class: "plan-wrap" });
    this.statusEl = el("div", { class: "road-status mono" }, "move over the plan");
    const toolbar = el("div", { class: "plan-toolbar" });
    planHost.append(toolbar, this.statusEl);
    this.profileEl = el("div", { class: "road-profile-body" });
    const profile = el("section", { class: "road-profile" }, el("div", { class: "chart-head" }, el("b", {}, "Longitudinal profile"), el("span", { class: "muted" }, "ground line from the terrain snapshot; design grade arrives with the profile stage")), this.profileEl);
    this.sectionEl = el("div", { class: "road-section-body" });
    this.sectionHead = el("div", { class: "chart-head" });
    const right = el("aside", { class: "road-right" }, this.sectionHead, this.sectionEl);
    const centre = el("div", { class: "road-centre" }, planHost, profile);
    this.root.appendChild(el("div", { class: "app" }, top, el("div", { class: "road-workspace" }, this.stageHost, centre, right)));

    this.plan = new PlanView(planHost);
    this.plan.onMove = (x, y) => this.updateStatus(x, y);
    this.plan.onLeave = () => { this.statusEl.textContent = "move over the plan"; };
    this.plan.onClick = (x, y) => { const s = this.stationAt(x, y); if (s) void this.showSection(s.chainage); };

    // data: terrain snapshot, seed alignment, modules, designs for the switcher
    const [runs, designs, modules, alignments] = await Promise.all([
      api.tin.list(pid), api.designs.list(pid), api.modules.list().catch(() => [] as ModuleInfo[]), api.alignments.list(pid),
    ]);
    if (this.destroyed) return;
    this.runs = runs;
    this.run = runs.find((r) => r.id === this.design.tin_run_id) ?? runs[runs.length - 1];
    this.module = modules.find((m) => m.id === "road") ?? null;
    this.alignment = alignments.find((a) => a.id === this.design.alignment_id) ?? null;
    this.centre = this.alignment ? centreline(this.alignment) : null;
    this.switcherHost.replaceChildren(renderModuleSwitcher(pid, designs, this.design.id, (mid) => void this.newDesign(mid)));
    this.station = this.alignment?.start_chainage ?? 0;

    await this.buildPlan(toolbar, alignments);
    this.renderStages(alignments);
    void this.loadProfile();
    void this.showSection(this.station);
  }

  // ------------------------------------------------------------------ plan layers
  private async buildPlan(toolbar: HTMLElement, alignments: Alignment[]): Promise<void> {
    const pid = this.project.id;
    const run = this.run;
    const layers: PlanLayer[] = [];
    let bounds: number[] | null = this.project.summary?.bounds ?? null;

    if (run) {
      const [hull, sets, lines] = await Promise.all([
        api.tin.hull(pid, run.id).catch(() => null),
        api.contours.list(pid),
        api.data.lines(pid).catch(() => ({ type: "FeatureCollection", features: [] })),
      ]);
      if (this.destroyed) return;
      if (hull && hull.features.length) {
        layers.push({ id: "hull", label: "Terrain extent", visible: true, draw: (ctx, v) => {
          for (const f of hull.features) {
            const rings: number[][][] = f.geometry.coordinates;
            ctx.fillStyle = "#111a2c"; ctx.strokeStyle = "#2a3a55"; ctx.lineWidth = 1;
            for (const ring of rings) { v.path(ctx, ring, true); ctx.fill(); ctx.stroke(); }
          }
        } });
        bounds = run.bounds.every((b) => b != null) ? (run.bounds as number[]) : bounds;
      }
      const set = [...sets].reverse().find((s) => s.run_id === run.id) ?? sets[sets.length - 1];
      if (set) {
        const fc = await api.contours.geojson(pid, set.id);
        if (this.destroyed) return;
        layers.push({ id: "contours", label: `Contours (set ${set.id}, ${set.params.interval} m)`, visible: true, draw: (ctx, v) => {
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
              const m = c[Math.floor(c.length / 2)];
              const [sx, sy] = v.toScreen(m[0], m[1]);
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
    }

    layers.push({ id: "alignment", label: "Alignment", visible: true, draw: (ctx, v) => {
      const al = this.alignment, c = this.centre;
      if (!al || !c) return;
      ctx.strokeStyle = "#f59e0b"; ctx.lineWidth = 2.5; v.path(ctx, c.xy); ctx.stroke();
      // chainage ticks every 100 m, labels every 500 m
      ctx.strokeStyle = "#fde68a"; ctx.lineWidth = 1; ctx.fillStyle = "#fde68a"; ctx.font = "11px system-ui";
      const tick = v.scale > 0.15 ? 100 : v.scale > 0.03 ? 500 : 1000;
      for (let ch = Math.ceil(c.start / tick) * tick; ch <= c.start + c.length; ch += tick) {
        const s = pointAtChainage(c, ch);
        if (!s) continue;
        const nx = -Math.sin(s.dir), ny = Math.cos(s.dir), L = 6 / v.scale;
        v.path(ctx, [[s.x - nx * L, s.y - ny * L], [s.x + nx * L, s.y + ny * L]]); ctx.stroke();
        if (ch % (tick * 5) === 0 || tick >= 500) { const [sx, sy] = v.toScreen(s.x + nx * L * 1.5, s.y + ny * L * 1.5); ctx.fillText(fmtChainage(ch, 0), sx + 2, sy); }
      }
      // IPs
      for (const ip of al.ips) {
        const [sx, sy] = v.toScreen(ip.x, ip.y);
        ctx.fillStyle = "#fb7185"; ctx.fillRect(sx - 4, sy - 4, 8, 8);
        ctx.fillStyle = "#fecdd3"; ctx.fillText(ip.label || "", sx + 6, sy - 6);
      }
      for (const k of al.key_points || []) {
        const [sx, sy] = v.toScreen(k.x, k.y);
        ctx.fillStyle = "#34d399"; ctx.beginPath(); ctx.arc(sx, sy, 3, 0, Math.PI * 2); ctx.fill();
      }
    } });

    layers.push({ id: "cursor", label: "Station", visible: true, draw: (ctx, v) => {
      const s = this.cursor;
      if (!s) return;
      const nx = -Math.sin(s.dir), ny = Math.cos(s.dir), hw = this.halfWidth;
      ctx.strokeStyle = "#38bdf8"; ctx.lineWidth = 1.5;
      v.path(ctx, [[s.x - nx * hw, s.y - ny * hw], [s.x + nx * hw, s.y + ny * hw]]); ctx.stroke();
      const [sx, sy] = v.toScreen(s.x, s.y);
      ctx.fillStyle = "#38bdf8"; ctx.beginPath(); ctx.arc(sx, sy, 4, 0, Math.PI * 2); ctx.fill();
    } });

    this.plan.layers = layers;
    for (const l of layers.filter((x) => x.id !== "cursor")) {
      const cb = el("input", { type: "checkbox", checked: l.visible });
      cb.addEventListener("change", () => { l.visible = cb.checked; this.plan.requestRender(); });
      toolbar.appendChild(el("label", { class: "check chip" }, cb, l.label));
    }
    toolbar.appendChild(button("Fit", () => this.fit(), "btn small"));
    if (this.alignment && this.centre) toolbar.appendChild(button("Fit alignment", () => this.plan.fit(polyBounds(this.centre!.xy)), "btn small"));
    if (!alignments.length) toolbar.appendChild(el("span", { class: "muted chip" }, "no alignment yet - draw one in the terrain workspace"));
    this.fit();
  }

  private fit(): void {
    const b = this.run && this.run.bounds.every((x) => x != null) ? (this.run.bounds as number[]) : this.project.summary?.bounds;
    if (b) this.plan.fit(b);
  }

  // ------------------------------------------------------------------ status / chainage
  private stationAt(x: number, y: number): { chainage: number; offset: number; x: number; y: number; dir: number } | null {
    const c = this.centre;
    if (!c) return null;
    let best = Infinity, bd = 0, bx = 0, by = 0, bdir = 0, boff = 0;
    for (let i = 0; i + 1 < c.xy.length; i++) {
      const [ax, ay] = c.xy[i], [bx2, by2] = c.xy[i + 1];
      const vx = bx2 - ax, vy = by2 - ay, L2 = vx * vx + vy * vy || 1e-12;
      const t = Math.min(1, Math.max(0, ((x - ax) * vx + (y - ay) * vy) / L2));
      const px = ax + t * vx, py = ay + t * vy;
      const d2 = (px - x) ** 2 + (py - y) ** 2;
      if (d2 < best) { best = d2; bd = c.d[i] + t * Math.sqrt(L2); bx = px; by = py; bdir = Math.atan2(vy, vx); boff = Math.sign(vx * (y - ay) - vy * (x - ax)) * Math.sqrt(d2); }
    }
    return { chainage: c.start + bd, offset: -boff, x: bx, y: by, dir: bdir }; // positive offset = right of travel
  }

  private updateStatus(x: number, y: number): void {
    const parts = [`E ${fmt(x, 2)}`, `N ${fmt(y, 2)}`];
    const s = this.stationAt(x, y);
    if (s && Math.abs(s.offset) < 500) parts.push(`CH ${fmtChainage(s.chainage)}`, `offset ${s.offset >= 0 ? "R" : "L"} ${fmt(Math.abs(s.offset), 2)}`);
    this.statusEl.textContent = parts.join("   ");
    if (this.run) {
      if (this.rlTimer) window.clearTimeout(this.rlTimer);
      this.rlTimer = window.setTimeout(async () => {
        try {
          const r = await api.tin.elevation(this.project.id, this.run!.id, x, y);
          if (!this.destroyed && this.statusEl.textContent?.startsWith(`E ${fmt(x, 2)}`)) this.statusEl.textContent += `   ground RL ${r.inside ? fmt(r.z, 3) : "outside TIN"}`;
        } catch { /* ignore */ }
      }, 180);
    }
  }

  // ------------------------------------------------------------------ profile & section
  private async loadProfile(): Promise<void> {
    const c = this.centre, run = this.run;
    if (!c || !run) {
      this.profileEl.innerHTML = `<p class="muted" style="padding:20px">${!run ? "No TIN run: build a TIN in the terrain workspace first." : "No seed alignment: pick or draw one in the terrain workspace, then choose it under Alignment."}</p>`;
      return;
    }
    const step = Math.max(1, Math.floor(c.xy.length / 2000));
    const coords = c.xy.filter((_, i) => i % step === 0 || i === c.xy.length - 1);
    try {
      const r = await api.tin.profile(this.project.id, run.id, coords);
      if (this.destroyed) return;
      const pts: ProfilePoint[] = r.distance.map((d, i) => ({ chainage: c.start + d, x: r.xy[i][0], y: r.xy[i][1], z: r.z[i], source: "edge" }));
      const markers = (this.alignment?.key_points || []).filter((k: any) => k.kind === "BC" || k.kind === "EC").map((k: any) => ({ chainage: k.chainage, label: `${k.kind} ${k.index}` }));
      const res = renderProfile(this.profileEl, pts, { vScale: 5, markers, onHover: (p) => this.setCursor(p ? p.chainage : null) });
      this.profileCursor = res.setCursor;
    } catch (e) {
      this.profileEl.innerHTML = `<p class="error" style="padding:20px">${e instanceof ApiError ? e.detail : String(e)}</p>`;
    }
  }

  private setCursor(chainage: number | null): void {
    if (!this.centre || chainage === null) { this.cursor = null; this.plan.requestRender(); return; }
    const s = pointAtChainage(this.centre, chainage);
    this.cursor = s;
    this.plan.requestRender();
  }

  private async showSection(chainage: number): Promise<void> {
    const c = this.centre, run = this.run;
    this.sectionHead.replaceChildren(
      el("b", {}, "Cross-section"),
      button("◀", () => void this.showSection(this.station - this.sectionInterval), "btn small"),
      el("span", { class: "mono" }, c ? `CH ${fmtChainage(this.station)}` : "-"),
      button("▶", () => void this.showSection(this.station + this.sectionInterval), "btn small"),
      el("span", { class: "spacer" }),
      (() => { const i = select([10, 20, 25, 50].map((v) => ({ value: String(v), label: `every ${v} m` })), String(this.sectionInterval)); i.addEventListener("change", () => { this.sectionInterval = Number(i.value); }); return i; })(),
      (() => { const w = select([10, 15, 20, 30, 50].map((v) => ({ value: String(v), label: `±${v} m` })), String(this.halfWidth)); w.addEventListener("change", () => { this.halfWidth = Number(w.value); void this.showSection(this.station); }); return w; })(),
    );
    if (!c || !run) { this.sectionEl.innerHTML = '<p class="muted" style="padding:20px">Needs a TIN run and a seed alignment.</p>'; return; }
    this.station = Math.min(Math.max(chainage, c.start), c.start + c.length);
    const s = pointAtChainage(c, this.station);
    if (!s) return;
    this.cursor = s;
    this.plan.requestRender();
    this.profileCursor?.(this.station);
    const nx = -Math.sin(s.dir), ny = Math.cos(s.dir), hw = this.halfWidth;
    const line = [[s.x + nx * hw, s.y + ny * hw], [s.x, s.y], [s.x - nx * hw, s.y - ny * hw]]; // left -> centre -> right
    try {
      const r = await api.tin.profile(this.project.id, run.id, line);
      if (this.destroyed) return;
      const sec: Section = { chainage: this.station, label: fmtChainage(this.station), centre: [s.x, s.y], direction: s.dir, left: hw, right: hw,
        offset: r.distance.map((d) => d - hw), z: r.z, xy: r.xy, source: r.distance.map((d) => (Math.abs(d - hw) < 1e-6 ? "centre" : "edge")) };
      renderSection(this.sectionEl, sec, { vScale: 2 });
    } catch (e) {
      this.sectionEl.innerHTML = `<p class="error" style="padding:20px">${e instanceof ApiError ? e.detail : String(e)}</p>`;
    }
  }

  // ------------------------------------------------------------------ stage panel
  private renderStages(alignments: Alignment[]): void {
    const host = this.stageHost;
    host.innerHTML = "";
    const stages = this.module?.stages?.length ? this.module.stages : [{ id: "alignment", label: "Alignment", description: "" }];
    const list = el("div", { class: "stage-list" });
    stages.forEach((s, i) => {
      const done = s.id === "alignment" && this.alignment;
      list.appendChild(el("div", { class: `stage${s.id === this.activeStage ? " active" : ""}`, onClick: () => { this.activeStage = s.id; this.renderStages(alignments); } },
        el("span", { class: "num" }, String(i + 1)), el("span", { class: "label" }, s.label),
        el("span", { class: "spacer" }),
        el("span", { class: `badge ${done ? "ok" : ""}` }, done ? "seeded" : s.id === "alignment" ? "no alignment" : "planned")));
    });
    host.append(el("h3", {}, "Design stages"), list);
    const content = el("div");
    host.appendChild(content);
    const stage = stages.find((s) => s.id === this.activeStage) ?? stages[0];
    if (stage.id === "alignment") this.renderAlignmentStage(content, alignments);
    else content.append(el("div", { class: "card" }, el("h4", {}, stage.label), el("p", { class: "muted" }, stage.description),
      el("p", { class: "hint" }, "This stage is part of the road engine, which is built after the legacy road software has been analysed. The workspace, terrain snapshot and stage flow are ready for it.")));
  }

  private renderAlignmentStage(host: HTMLElement, alignments: Alignment[]): void {
    const pid = this.project.id;
    const run = this.run;
    const newer = this.runs.filter((r) => run && r.id > run.id);
    const snap = el("div", { class: "card" }, el("h4", {}, "Terrain snapshot"),
      run ? el("dl", { class: "kv" },
        el("dt", {}, "TIN run"), el("dd", {}, `${run.id}${run.name ? " · " + run.name : ""}`),
        el("dt", {}, "triangles"), el("dd", {}, `${run.n_triangles} (${run.n_nodes} nodes)`),
        el("dt", {}, "RL"), el("dd", {}, `${fmt(run.z_range[0] ?? undefined, 2)} – ${fmt(run.z_range[1] ?? undefined, 2)}`),
        el("dt", {}, "created"), el("dd", {}, new Date(run.created).toLocaleString())) : el("p", { class: "muted" }, "No TIN run yet."),
      newer.length ? el("div", { class: "terrain-badge stale" }, `Newer terrain available: run ${newer[newer.length - 1].id}`, button("Rebase", async () => {
        try { this.design = await api.designs.patch(pid, this.design.id, { tin_run_id: newer[newer.length - 1].id }); toast(`Design rebased onto run ${this.design.tin_run_id}`, "ok"); await this.init(); }
        catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
      }, "btn small")) : el("div", { class: "terrain-badge" }, "Up to date with the latest terrain"),
      el("div", { class: "btn-row" }, button("Open terrain workspace", () => { location.hash = terrainHash(pid); }, "btn small")));

    const alSel = select([{ value: "", label: "— none —" }, ...alignments.map((a) => ({ value: String(a.id), label: `${a.name} (${(a.length / 1000).toFixed(2)} km)` }))], String(this.design.alignment_id ?? ""));
    alSel.addEventListener("change", async () => {
      try {
        this.design = await api.designs.patch(pid, this.design.id, { alignment_id: alSel.value ? Number(alSel.value) : null });
        await this.init();
      } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
    });
    const al = this.alignment;
    const ipTable = el("table", { class: "data" }, el("tr", {}, el("th", {}, "#"), el("th", {}, "X"), el("th", {}, "Y"), el("th", {}, "R")));
    if (al) for (const [i, ip] of al.ips.entries()) ipTable.appendChild(el("tr", {}, el("td", {}, ip.label || String(i)), el("td", {}, fmt(ip.x, 3)), el("td", {}, fmt(ip.y, 3)), el("td", {}, ip.radius ? fmt(ip.radius, 1) : "–")));
    const seed = el("div", { class: "card" }, el("h4", {}, "Horizontal alignment"),
      field("Seed alignment (from the terrain workspace)", alSel, "The design owns a copy from here on; IP editing with standards checks arrives with the road engine."),
      al ? el("dl", { class: "kv" }, el("dt", {}, "length"), el("dd", {}, `${fmt(al.length, 2)} m`), el("dt", {}, "chainage"), el("dd", {}, `${fmtChainage(al.start_chainage)} – ${fmtChainage(al.end_chainage)}`),
        el("dt", {}, "curves"), el("dd", {}, `${al.ips.filter((p) => p.radius > 0).length}`), el("dt", {}, "valid"), el("dd", {}, al.valid ? "yes" : `${al.issues.length} issues`)) : null,
      al ? ipTable : null);

    const st = this.design.settings || {};
    const cls = select(ROAD_CLASSES.map(([v, l]) => ({ value: v, label: l })), String(st.road_class ?? "feeder"));
    const ter = select(TERRAINS.map(([v, l]) => ({ value: v, label: l })), String(st.terrain ?? "rolling"));
    const spd = select(SPEEDS.map((v) => ({ value: String(v), label: `${v} km/h` })), String(st.design_speed ?? 40));
    const save = async () => {
      try { this.design = await api.designs.patch(pid, this.design.id, { settings: { road_class: cls.value, terrain: ter.value, design_speed: Number(spd.value) } }); toast("Design parameters saved", "ok"); }
      catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
    };
    for (const s of [cls, ter, spd]) s.addEventListener("change", () => void save());
    const params = el("div", { class: "card" }, el("h4", {}, "Design parameters"),
      field("Road class", cls), field("Terrain", ter), field("Design speed", spd),
      el("p", { class: "hint" }, "Stored with the design. The standards tables (Nepal Road Standard, DoR) that turn these into minimum radius, gradient and width checks load in the road engine."));
    host.append(snap, seed, params);
  }

  // ------------------------------------------------------------------ new design
  private async newDesign(moduleId: string): Promise<void> {
    if (!this.run) { toast("Build a TIN first", "error"); return; }
    const name = prompt("Name for the new design", `${moduleId} design`);
    if (name === null) return;
    try {
      const d = await api.designs.create(this.project.id, { module: moduleId, name, tin_run_id: this.run.id, alignment_id: this.design.alignment_id });
      location.hash = designHash(this.project.id, d);
    } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
  }
}

// ---------------------------------------------------------------------- geometry helpers
function centreline(al: Alignment): Centreline {
  const xy = densify(al, 2.0, 2);
  const d = [0];
  for (let i = 1; i < xy.length; i++) d.push(d[i - 1] + Math.hypot(xy[i][0] - xy[i - 1][0], xy[i][1] - xy[i - 1][1]));
  return { xy, d, length: d[d.length - 1], start: al.start_chainage };
}

function pointAtChainage(c: Centreline, chainage: number): { x: number; y: number; dir: number } | null {
  const t = chainage - c.start;
  if (t < -1e-6 || t > c.length + 1e-6 || c.xy.length < 2) return null;
  let i = 0;
  while (i + 2 < c.xy.length && c.d[i + 1] < t) i++;
  const [ax, ay] = c.xy[i], [bx, by] = c.xy[i + 1];
  const L = c.d[i + 1] - c.d[i] || 1e-12;
  const f = Math.min(1, Math.max(0, (t - c.d[i]) / L));
  return { x: ax + f * (bx - ax), y: ay + f * (by - ay), dir: Math.atan2(by - ay, bx - ax) };
}

function polyBounds(xy: number[][]): number[] {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of xy) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
  return [x0, y0, x1, y1];
}
