/** Floating layer tree (top-right of the map) - the single place for layer visibility. */
import { api } from "../api";
import { DEFAULT_LAYERS, store, type LayerVisibility } from "../state";
import { el } from "./dom";
import { mountInspector } from "./selection";
import type { Workspace } from "./workspace";

type Key = keyof LayerVisibility;

interface Node { label: string; key?: Key; children?: Node[]; count?: () => string | null; extra?: () => HTMLElement | null; onToggle?: (on: boolean) => void }

export class LayerTree {
  private root: HTMLElement;
  private body: HTMLElement;
  /** the Properties pane: the one inspector, embedded here rather than floating over the map */
  private props: HTMLElement;
  private tabs: HTMLElement;
  private tab: "layers" | "properties" = "layers";
  private unmountInspector: () => void;
  private collapsed = false;
  private open: Record<string, boolean> = { "Survey data": true, Terrain: true, Contours: true, Alignment: true, Sections: true, Team: false };
  private hullLoaded = false;
  private issuesLoaded = false;
  private rejectedLoaded = false;

  constructor(private ws: Workspace, host: HTMLElement) {
    this.body = el("div", { class: "layer-body" });
    this.props = el("div", { class: "layer-props", style: "display:none" });
    this.tabs = el("div", { class: "layer-tabs" });
    const toggle = el("button", { class: "layer-collapse", title: "Collapse", onClick: () => { this.collapsed = !this.collapsed; this.root.classList.toggle("collapsed", this.collapsed); toggle.textContent = this.collapsed ? "☰" : "–"; } }, "–");
    this.root = el("div", { class: "layer-panel" },
      el("div", { class: "layer-head", onClick: (e: Event) => { if ((e.target as HTMLElement).tagName !== "BUTTON") toggle.click(); } }, el("span", {}, "Layers & properties"), el("span", { class: "spacer" }), toggle),
      this.tabs, this.body, this.props);
    host.appendChild(this.root);
    this.unmountInspector = mountInspector(this.props, { embedded: true });
    // a new selection brings its tab forward: the properties are what you just asked to see
    store.on("selection", (sel) => { this.renderTabs(); if (sel) this.showTab("properties"); });
    this.renderTabs();
    for (const k of ["tinRuns", "currentRun", "contourSets", "visibleContourSets", "alignments", "currentAlignment", "sectionSets", "currentSectionSet", "layers", "tinStyle"] as const) {
      store.on(k, () => this.render());
    }
    store.subscribe("refresh:comments", () => this.render());
    this.render();
  }

  private showTab(t: "layers" | "properties"): void {
    this.tab = t;
    this.body.style.display = t === "layers" ? "" : "none";
    this.props.style.display = t === "properties" ? "" : "none";
    if (this.collapsed) { this.collapsed = false; this.root.classList.remove("collapsed"); }
    this.renderTabs();
  }

  private renderTabs(): void {
    const sel = store.get("selection");
    this.tabs.innerHTML = "";
    for (const [key, label] of [["layers", "Layers"], ["properties", sel ? `Properties: ${sel.kind}` : "Properties"]] as const) {
      this.tabs.appendChild(el("button", { class: `layer-tab${this.tab === key ? " active" : ""}`,
        onClick: () => this.showTab(key as "layers" | "properties") }, label));
    }
  }

  /** Take the panel down with the workspace. */
  destroy(): void {
    this.unmountInspector();
    this.root.remove();
  }

  private set(key: Key, on: boolean): void {
    store.set("layers", { ...store.get("layers"), [key]: on });
  }

  private nodes(): Node[] {
    const ws = this.ws;
    const p = ws.project.summary;
    const run = store.get("currentRun");
    const runInfo = store.get("tinRuns").find((r) => r.id === run);
    const sets = store.get("contourSets");
    const visible = store.get("visibleContourSets");
    const al = store.get("alignments").find((a) => a.id === store.get("currentAlignment"));
    const secSet = store.get("sectionSets").find((s) => s.id === store.get("currentSectionSet"));
    const lines = p?.lines || {};
    return [
      { label: "Survey data", children: [
        { label: "Points", key: "points", count: () => `${p?.points ?? 0}` },
        { label: "Point numbers / RL", key: "pointLabels", onToggle: () => void ws.refreshPoints() },
        { label: "Feature lines", key: "featureLines", count: () => `${lines.feature ?? 0}` },
        { label: "Boundary", key: "boundary", count: () => `${lines.boundary ?? 0}` },
        { label: "Voids", key: "voids", count: () => `${lines.void ?? 0}` },
        { label: "Digitised contours", key: "digitisedContours", count: () => `${lines.contour ?? 0}` },
      ] },
      { label: "Terrain", children: [
        { label: runInfo ? `TIN surface (run ${runInfo.id})` : "TIN surface", key: "tin", count: () => (runInfo ? `${runInfo.n_triangles} tri` : "none"), extra: () => this.tinStyleControls() },
        { label: "Triangle edges", key: "tinEdges", count: () => (runInfo ? `${runInfo.n_triangles} tri` : null),
          onToggle: (on) => this.ensureEdges(on) },
        { label: "TIN outline", key: "tinHull", onToggle: (on) => void this.ensureHull(on) },
        { label: "Issue markers", key: "tinIssues", count: () => (runInfo ? `${runInfo.issues_count}` : null), onToggle: (on) => void this.ensureIssues(on) },
        { label: "Rejected triangles", key: "tinRejected", count: () => (runInfo?.stats?.raw_triangles ? `${runInfo.stats.raw_triangles - runInfo.n_triangles}` : null), onToggle: (on) => void this.ensureRejected(on) },
      ] },
      { label: "Contours", children: [
        ...sets.map<Node>((s) => ({ label: s.name || `Set ${s.id} · ${s.params.interval} m`, count: () => `${s.n_lines}`,
          extra: () => this.setCheckbox(s.id, visible.includes(s.id), `${s.style.major_color || "#c2410c"}`) })),
        { label: "Contour labels", key: "contourLabels" },
      ] },
      { label: "Alignment", children: [
        { label: al ? al.name : "Alignment", key: "alignment", count: () => (al ? `${al.length.toFixed(0)} m` : "none") },
        { label: "Chainage labels", key: "chainageLabels" },
        { label: "BC / MC / EC markers", key: "keyPoints" },
      ] },
      { label: "Sections", children: [
        { label: secSet ? `Section lines (set ${secSet.id})` : "Section lines", key: "sections", count: () => (secSet ? `${secSet.summary.sections}` : "none") },
      ] },
      { label: "Team", children: [
        { label: "Comment pins", key: "comments", count: () => `${ws.comments.filter((c) => c.x != null && !c.parent_id).length}` },
      ] },
    ];
  }

  private setCheckbox(setId: number, on: boolean, colour: string): HTMLElement {
    const c = el("input", { type: "checkbox", checked: on });
    c.addEventListener("change", async () => {
      const v = store.get("visibleContourSets").filter((id) => id !== setId);
      if (c.checked) v.push(setId);
      store.set("visibleContourSets", v);
      await this.ws.redrawContours();
    });
    return el("span", { class: "layer-set" }, c, el("i", { class: "swatch", style: `background:${colour}` }));
  }

  private tinStyleControls(): HTMLElement {
    const st = store.get("tinStyle");
    const mode = el("select", { class: "mini" }, ...[["ramp", "elevation"], ["flat", "shaded"]].map(([v, l]) => el("option", { value: v, selected: v === st.mode }, l)));
    const op = el("input", { type: "range", min: "0.1", max: "1", step: "0.05", value: String(st.opacity), class: "mini", title: "opacity" });
    const apply = () => { store.set("tinStyle", { mode: mode.value as any, opacity: Number(op.value) }); this.ws.restyleTin(); };
    mode.addEventListener("change", apply);
    op.addEventListener("change", apply);
    return el("span", { class: "layer-extra" }, mode, op);
  }

  /** The triangulation is built from the mesh as it loads, so switching it on the first time
   *  reloads the run; switching it off (or on again afterwards) is just a visibility change. */
  private ensureEdges(on: boolean): void {
    if (on && !this.ws.layers.tinWires.length && store.get("currentRun") !== null) { this.ws.restyleTin(); return; }
    this.ws.layers.setVisibility(store.get("layers"));
  }

  private async ensureHull(on: boolean): Promise<void> {
    const run = store.get("currentRun");
    if (on && run !== null && !this.hullLoaded) {
      this.ws.layers.setHull(await api.tin.hull(this.ws.project.id, run));
      this.hullLoaded = true;
    }
    this.ws.layers.setVisibility(store.get("layers"));
  }

  private async ensureIssues(on: boolean): Promise<void> {
    const run = store.get("currentRun");
    if (on && run !== null && !this.issuesLoaded) {
      this.ws.layers.setIssues(await api.tin.issues(this.ws.project.id, run));
      this.issuesLoaded = true;
    }
    this.ws.layers.setVisibility(store.get("layers"));
  }

  private async ensureRejected(on: boolean): Promise<void> {
    const run = store.get("currentRun");
    if (on && run !== null && !this.rejectedLoaded) {
      this.ws.layers.setRejected(await api.tin.rejected(this.ws.project.id, run));
      this.rejectedLoaded = true;
    }
    this.ws.layers.setVisibility(store.get("layers"));
  }

  /** call when the TIN run changes so outline/issues/rejected reload on next toggle */
  invalidateTin(): void {
    this.hullLoaded = false;
    this.issuesLoaded = false;
    this.rejectedLoaded = false;
    this.ws.layers.setRejected(null);
    const v = store.get("layers");
    if (v.tinHull) void this.ensureHull(true);
    if (v.tinIssues) void this.ensureIssues(true);
    if (v.tinRejected) void this.ensureRejected(true);
  }

  render(): void {
    this.body.innerHTML = "";
    const vis = store.get("layers");
    for (const cat of this.nodes()) {
      const isOpen = this.open[cat.label] ?? true;
      const kids = cat.children || [];
      const keyed = kids.filter((k) => k.key);
      const allOn = keyed.length > 0 && keyed.every((k) => vis[k.key!]);
      const anyOn = keyed.some((k) => vis[k.key!]);
      const master = el("input", { type: "checkbox", checked: anyOn, title: "toggle category" });
      (master as HTMLInputElement).indeterminate = anyOn && !allOn;
      master.addEventListener("change", () => {
        const next = { ...store.get("layers") };
        for (const k of keyed) next[k.key!] = master.checked;
        store.set("layers", next);
        for (const k of keyed) k.onToggle?.(master.checked);
      });
      const arrow = el("span", { class: "layer-arrow", onClick: () => { this.open[cat.label] = !isOpen; this.render(); } }, isOpen ? "▾" : "▸");
      const catEl = el("div", { class: "layer-cat" }, el("div", { class: "layer-cat-head" }, arrow, master, el("span", { class: "layer-cat-label", onClick: () => { this.open[cat.label] = !isOpen; this.render(); } }, cat.label)));
      if (isOpen) {
        for (const n of kids) {
          const c = n.key ? el("input", { type: "checkbox", checked: vis[n.key] }) : null;
          c?.addEventListener("change", () => { this.set(n.key!, (c as HTMLInputElement).checked); n.onToggle?.((c as HTMLInputElement).checked); });
          const count = n.count?.();
          catEl.appendChild(el("div", { class: "layer-item" }, c, n.extra && !n.key ? n.extra() : null, el("span", { class: "layer-label", title: n.label }, n.label),
            count ? el("span", { class: "layer-count" }, count) : null));
          if (n.extra && n.key) catEl.appendChild(el("div", { class: "layer-item layer-sub" }, n.extra()));
        }
      }
      this.body.appendChild(catEl);
    }
    this.body.appendChild(el("div", { class: "layer-foot" },
      el("a", { href: "#", onClick: (e: Event) => { e.preventDefault(); store.set("layers", { ...DEFAULT_LAYERS }); } }, "reset"),
      " · ",
      el("a", { href: "#", onClick: (e: Event) => { e.preventDefault(); const next = { ...store.get("layers") }; (Object.keys(next) as Key[]).forEach((k) => (next[k] = false)); store.set("layers", next); } }, "hide all"),
    ));
  }
}
