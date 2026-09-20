import { api, ApiError, waitForJob } from "../../api";
import { renderProfile } from "../../charts/profile";
import { store, toast } from "../../state";
import { button, el, field, fmt, numberInput, select } from "../dom";
import type { Workspace } from "../workspace";

type ConstraintMode = "auto" | "semi" | "manual";

/** Panel state that should survive tab switches (the panel is re-rendered on every visit). */
const ui = { mode: "auto" as ConstraintMode, edgeFactor: 3.0, maxEdge: "", maxEdgeFactor: "", minAngle: 0 };

export function renderTinPanel(ws: Workspace, host: HTMLElement): void {
  const pid = ws.project.id;

  // ------------------------------------------------------------------ constraints
  const modeSel = select([
    { value: "auto", label: "Automatic - detect survey limit and gaps" },
    { value: "semi", label: "Semi-automatic - review suggestions first" },
    { value: "manual", label: "Manual - drawn / imported constraints only" },
  ], ui.mode);
  const modeHint = el("p", { class: "hint" });
  const edgeFactor = numberInput(ui.edgeFactor, { min: "1.2", step: "0.5", title: "A triangle edge longer than this many times the median edge marks the data limit or a gap" });
  const detectRow = el("div", { class: "row" }, field("Long-edge factor", edgeFactor, "x median edge"));
  const suggestionsEl = el("div");
  let suggestions: any[] = [];
  let selected = new Set<number>();

  const setModeHint = () => {
    modeHint.textContent = {
      auto: "The build works out the survey limit and unsurveyed gaps from the points when you have not drawn or accepted a boundary. The detected constraints are stored (source: auto, dashed on the map) and listed under Data > Constraint lines, where you can delete or re-kind them.",
      semi: "Click Detect to preview the suggested boundary and gaps, untick what you do not want, then Accept. Accepted lines become ordinary constraints that you can edit or delete; the build then uses them as they are.",
      manual: "Only the constraint lines you drew or imported are used (boundary, holes, breaklines). Nothing is detected.",
    }[ui.mode];
    detectRow.style.display = ui.mode === "manual" ? "none" : "";
    detectBtn.style.display = ui.mode === "semi" ? "" : "none";
    if (ui.mode !== "semi") clearSuggestions();
  };
  const clearSuggestions = () => {
    suggestions = [];
    selected = new Set();
    suggestionsEl.innerHTML = "";
    ws.layers.setSuggestions(null);
  };
  const renderSuggestions = () => {
    suggestionsEl.innerHTML = "";
    if (!suggestions.length) return;
    const list = el("div", { class: "card" });
    for (const f of suggestions) {
      const p = f.properties;
      const cb = el("input", { type: "checkbox", checked: selected.has(p.index) });
      cb.addEventListener("change", () => { if (cb.checked) selected.add(p.index); else selected.delete(p.index); ws.layers.setSuggestions(suggestions, selected); });
      const coords: number[][] = f.geometry.coordinates;
      const fly = el("a", { href: "#", onClick: (ev: Event) => {
        ev.preventDefault();
        const xs = coords.map((c) => c[0]), ys = coords.map((c) => c[1]);
        store.emit("map:flyTo", [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]);
      } }, p.kind === "boundary" ? "boundary" : "hole");
      list.appendChild(el("label", { class: "check", style: "align-items:flex-start" }, cb,
        el("span", {}, el("b", {}, fly), el("span", { class: "badge", style: "margin-left:6px" }, `${Math.round(p.confidence * 100)}%`),
          el("div", { class: "muted", style: "font-size:11px" }, p.reason))));
    }
    list.appendChild(el("div", { class: "btn-row" },
      button("Accept selected", async () => {
        const feats = suggestions.filter((f) => selected.has(f.properties.index)).map((f) => ({ kind: f.properties.kind, coords: f.geometry.coordinates, name: f.properties.reason }));
        if (!feats.length) { toast("Nothing selected", "error"); return; }
        try {
          await api.constraints.accept(pid, { features: feats, source: "accepted", replace_auto: true });
          toast(`${feats.length} constraint${feats.length > 1 ? "s" : ""} accepted`, "ok");
          clearSuggestions();
          await ws.refreshLines();
          store.emit("refresh:lines");
        } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
      }, "btn primary"),
      button("Discard", () => clearSuggestions()),
    ));
    suggestionsEl.appendChild(list);
  };
  const detectBtn = button("Detect constraints", async () => {
    store.set("busy", "Analysing points");
    try {
      const fc = await api.constraints.detect(pid, { edge_factor: Number(edgeFactor.value) || 3 });
      suggestions = fc.features;
      selected = new Set(suggestions.map((f) => f.properties.index));
      ws.layers.setSuggestions(suggestions, selected);
      renderSuggestions();
      const s = fc.stats || {};
      toast(suggestions.length ? `${suggestions.length} suggestion${suggestions.length > 1 ? "s" : ""} (threshold ${fmt(s.threshold, 1)} m, ${s.peeled_triangles ?? 0} edge triangles peeled)` : "Nothing to suggest: the points fill their convex hull", suggestions.length ? "ok" : "info");
    } catch (e) {
      toast(e instanceof ApiError ? e.detail : String(e), "error");
    } finally {
      store.set("busy", null);
    }
  });
  modeSel.addEventListener("change", () => { ui.mode = modeSel.value as ConstraintMode; setModeHint(); });
  edgeFactor.addEventListener("change", () => { ui.edgeFactor = Number(edgeFactor.value) || 3; });

  host.append(el("h3", {}, "Constraints"), el("div", { class: "card" },
    field("Mode", modeSel), modeHint, detectRow,
    el("div", { class: "btn-row" }, detectBtn),
  ), suggestionsEl);
  setModeHint();

  // ------------------------------------------------------------------ triangulation
  const tol = numberInput(0.001, { min: "0.0001", step: "0.001" });
  const useFeatures = el("input", { type: "checkbox", checked: true });
  const useBoundary = el("input", { type: "checkbox", checked: true });
  const useVoids = el("input", { type: "checkbox", checked: true });
  const dropZero = el("input", { type: "checkbox" });
  const mode = select([{ value: "inside", label: "constraints + nesting (recommended)" }, { value: "legacy_cross", label: "legacy: delete crossing triangles" }], "inside");
  const maxEdge = el("input", { type: "number", min: "0", step: "1", placeholder: "off", value: ui.maxEdge, title: "Edge triangles with an edge longer than this are removed (constraint edges are never removed)" });
  const maxEdgeFactor = el("input", { type: "number", min: "1", step: "0.5", placeholder: "off", value: ui.maxEdgeFactor, title: "... or longer than this many times the median edge" });
  const minAngle = numberInput(ui.minAngle, { min: "0", max: "45", step: "1", title: "Edge triangles with a smaller angle than this are removed" });
  const name = el("input", { type: "text", placeholder: "optional name" });
  const build = button("Build TIN", async () => {
    store.set("busy", "Triangulating");
    ui.maxEdge = maxEdge.value; ui.maxEdgeFactor = maxEdgeFactor.value; ui.minAngle = Number(minAngle.value) || 0;
    try {
      let job = await api.tin.create(pid, {
        name: name.value, dedupe_tol: Number(tol.value), use_features: useFeatures.checked, use_boundary: useBoundary.checked,
        use_voids: useVoids.checked, boundary_mode: mode.value, drop_zero_z: dropZero.checked,
        constraint_mode: ui.mode, detect: { edge_factor: Number(edgeFactor.value) || 3 },
        max_edge_length: maxEdge.value ? Number(maxEdge.value) : null,
        max_edge_factor: maxEdgeFactor.value ? Number(maxEdgeFactor.value) : null,
        min_angle_deg: Number(minAngle.value) || 0,
      });
      job = await waitForJob(job, (j) => store.set("busy", `Triangulating ${Math.round(j.progress * 100)}%`));
      const r = job.result!;
      const rej = r.stats?.raw_triangles ? r.stats.raw_triangles - r.n_triangles : 0;
      toast(`TIN built: ${r.n_triangles} triangles${rej ? `, ${rej} rejected` : ""}${r.issues_count ? `, ${r.issues_count} issues` : ""}`, "ok");
      store.set("currentRun", null);
      await ws.refreshTinRuns();
      await ws.refreshProject();
      await ws.refreshLines(); // automatic mode may have stored detected constraints
      store.emit("refresh:lines");
      renderRuns();
    } catch (e) {
      toast(e instanceof ApiError ? e.detail : String(e), "error");
    } finally {
      store.set("busy", null);
    }
  }, "btn primary");

  host.append(el("h3", {}, "Triangulation"), el("div", { class: "card" },
    el("div", { class: "row" }, field("Duplicate tolerance (m)", tol), field("Name", name)),
    el("label", { class: "check" }, useFeatures, "Use breaklines (feature lines, digitised contours)"),
    el("label", { class: "check" }, useBoundary, "Use boundary constraints"),
    el("label", { class: "check" }, useVoids, "Use hole / void constraints"),
    el("label", { class: "check" }, dropZero, "Discard points with Z = 0 (legacy)"),
    field("Boundary rule", mode),
    el("details", {}, el("summary", { class: "muted" }, "Edge triangle filters"),
      el("p", { class: "hint" }, "Long or badly shaped triangles are peeled from the outer edge of the mesh inwards. Triangles along a constraint line are never removed. Rejected triangles can be shown from the Layers panel."),
      el("div", { class: "row" }, field("Max edge length (m)", maxEdge), field("or x median edge", maxEdgeFactor)),
      field("Min angle (deg)", minAngle)),
    el("div", { class: "btn-row" }, build),
  ));

  host.append(el("p", { class: "hint" }, "Surface colouring, outline, issue markers and rejected triangles are in the Layers panel (top-right of the map)."));

  // ------------------------------------------------------------------ tools
  host.append(el("h3", {}, "Tools"), el("div", { class: "card" },
    el("div", { class: "btn-row" },
      button("Spot height", () => {
        const run = store.get("currentRun");
        if (run === null) { toast("Build a TIN first", "error"); return; }
        ws.toolHandlers = { hint: "Spot height: click on the terrain (Esc to stop)", onClick: async (p) => {
          const r = await api.tin.elevation(pid, run, p.x, p.y);
          toast(r.inside ? `E ${fmt(p.x, 2)}  N ${fmt(p.y, 2)}  RL ${fmt(r.z, 3)}` : "Outside the TIN");
        } };
        ws.setTool("spot");
      }),
      button("Quick profile", () => {
        const run = store.get("currentRun");
        if (run === null) { toast("Build a TIN first", "error"); return; }
        const coords: number[][] = [];
        ws.toolHandlers = { hint: "Quick profile: click points along a line, double-click to finish",
          onClick: (p) => { coords.push([p.x, p.y]); ws.layers.setDraftLine(coords.map((c) => [c[0], c[1], ws.zAt(c[0], c[1])])); },
          onMove: (p) => { if (p && coords.length) ws.layers.setDraftLine([...coords, [p.x, p.y]].map((c) => [c[0], c[1], ws.zAt(c[0], c[1])])); },
          onDouble: async () => {
            if (coords.length < 2) return;
            const r = await api.tin.profile(pid, run, coords);
            ws.showCharts(true);
            const box = document.querySelector<HTMLElement>(".chart-body > div");
            if (box) renderProfile(box, r.distance.map((d, i) => ({ chainage: d, x: r.xy[i][0], y: r.xy[i][1], z: r.z[i], source: "edge" })), { vScale: 2,
              onHover: (p) => store.emit("hover:chainage", p ? { x: p.x, y: p.y, z: p.z ?? 0 } : null) });
            ws.setTool("none");
          } };
        ws.setTool("profile");
      }),
    ),
  ));

  // ------------------------------------------------------------------ runs
  const runsEl = el("div");
  host.append(el("h3", {}, "TIN runs"), runsEl);
  function renderRuns() {
    runsEl.innerHTML = "";
    const runs = store.get("tinRuns");
    const cur = store.get("currentRun");
    if (!runs.length) runsEl.appendChild(el("p", { class: "muted" }, "No TIN yet. Import points and click Build TIN."));
    for (const r of [...runs].reverse()) {
      const s = r.stats || {};
      const cons = s.constraints || {};
      const rej = s.rejected || {};
      const rejText = Object.keys(rej).length ? Object.entries(rej).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(" · ") : "none";
      const valid = s.validation_problems ? el("span", { class: "badge warn" }, `${s.validation_problems} validation problems`) : el("span", { class: "badge ok" }, "validated");
      const card = el("div", { class: `card clickable${r.id === cur ? " selected" : ""}`, onClick: async () => { await ws.selectRun(r.id); await ws.redrawContours(); renderRuns(); } },
        el("div", { style: "display:flex;gap:6px;align-items:center;flex-wrap:wrap" }, el("b", {}, `Run ${r.id}${r.name ? " · " + r.name : ""}`),
          valid,
          r.issues_count ? el("span", { class: "badge warn" }, `${r.issues_count} issues`) : null,
          el("span", { class: "spacer", style: "flex:1" }),
          button("✕", async (ev) => { ev.stopPropagation(); if (confirm(`Delete TIN run ${r.id} and its contours/sections?`)) { await api.tin.delete(pid, r.id); store.set("currentRun", null); await ws.refreshTinRuns(); await ws.refreshContours(); await ws.refreshSectionSets(); renderRuns(); } }, "btn small danger")),
        el("dl", { class: "kv" },
          el("dt", {}, "triangles"), el("dd", {}, `${r.n_triangles} (${r.n_nodes} nodes, ${s.edges ?? "–"} edges)`),
          el("dt", {}, "constraints"), el("dd", {}, `${cons.boundary ?? 0} boundary · ${cons.hole ?? 0} holes · ${cons.breakline ?? 0} breaklines · ${s.constraint_edges ?? s.segments ?? 0} edges${s.steiner_points ? ` · ${s.steiner_points} crossings` : ""}`),
          el("dt", {}, "mode"), el("dd", {}, `${s.constraint_mode ?? "manual"}${s.detection ? ` (detected ${s.detection.suggestions} · threshold ${fmt(s.detection.threshold, 1)} m)` : ""}`),
          el("dt", {}, "rejected"), el("dd", {}, rejText),
          el("dt", {}, "removed"), el("dd", {}, `${s.duplicates_removed ?? 0} duplicates${s.max_edge_used ? ` · max edge ${fmt(s.max_edge_used, 1)} m` : ""}${s.min_angle_used ? ` · min angle ${s.min_angle_used}°` : ""}`),
          el("dt", {}, "RL"), el("dd", {}, `${fmt(r.z_range[0] ?? undefined, 2)} – ${fmt(r.z_range[1] ?? undefined, 2)}`),
          el("dt", {}, "created"), el("dd", {}, new Date(r.created).toLocaleString()),
        ),
      );
      if (r.issues?.length) {
        const list = el("ul", { style: "margin:6px 0 0;padding-left:16px;font-size:11px" });
        for (const i of r.issues.slice(0, 6)) list.appendChild(el("li", {}, el("a", { href: "#", onClick: (ev: Event) => { ev.preventDefault(); ev.stopPropagation(); store.emit("map:flyTo", [i.x - 15, i.y - 15, i.x + 15, i.y + 15]); } }, i.kind.replace(/_/g, " ")), ` at ${fmt(i.x, 1)}, ${fmt(i.y, 1)}`));
        if (r.issues.length > 6) list.appendChild(el("li", { class: "muted" }, `… ${r.issues_count - 6} more (enable issue markers)`));
        card.appendChild(list);
      }
      runsEl.appendChild(card);
    }
  }
  renderRuns();
}
