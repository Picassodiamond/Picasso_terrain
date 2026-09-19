import { api, ApiError, waitForJob } from "../../api";
import { renderProfile } from "../../charts/profile";
import { store, toast } from "../../state";
import { button, el, field, fmt, numberInput, select } from "../dom";
import type { Workspace } from "../workspace";

export function renderTinPanel(ws: Workspace, host: HTMLElement): void {
  const pid = ws.project.id;
  const tol = numberInput(0.001, { min: "0.0001", step: "0.001" });
  const useFeatures = el("input", { type: "checkbox", checked: true });
  const useBoundary = el("input", { type: "checkbox", checked: true });
  const useVoids = el("input", { type: "checkbox", checked: true });
  const dropZero = el("input", { type: "checkbox" });
  const mode = select([{ value: "inside", label: "inside boundary (recommended)" }, { value: "legacy_cross", label: "legacy: delete crossing triangles" }], "inside");
  const name = el("input", { type: "text", placeholder: "optional name" });
  const build = button("Build TIN", async () => {
    store.set("busy", "Triangulating");
    try {
      let job = await api.tin.create(pid, { name: name.value, dedupe_tol: Number(tol.value), use_features: useFeatures.checked, use_boundary: useBoundary.checked,
        use_voids: useVoids.checked, boundary_mode: mode.value, drop_zero_z: dropZero.checked });
      job = await waitForJob(job, (j) => store.set("busy", `Triangulating ${Math.round(j.progress * 100)}%`));
      const r = job.result!;
      toast(`TIN built: ${r.n_triangles} triangles${r.issues_count ? `, ${r.issues_count} issues` : ""}`, "ok");
      store.set("currentRun", null);
      await ws.refreshTinRuns();
      await ws.refreshProject();
      renderRuns();
    } catch (e) {
      toast(e instanceof ApiError ? e.detail : String(e), "error");
    } finally {
      store.set("busy", null);
    }
  }, "btn primary");

  host.append(el("h3", {}, "Triangulation"), el("div", { class: "card" },
    el("div", { class: "row" }, field("Duplicate tolerance (m)", tol), field("Name", name)),
    el("label", { class: "check" }, useFeatures, "Use feature (break) lines"),
    el("label", { class: "check" }, useBoundary, "Clip to boundary"),
    el("label", { class: "check" }, useVoids, "Exclude voids"),
    el("label", { class: "check" }, dropZero, "Discard points with Z = 0 (legacy)"),
    field("Boundary rule", mode),
    el("div", { class: "btn-row" }, build),
  ));

  host.append(el("p", { class: "hint" }, "Surface colouring, outline and issue markers are in the Layers panel (top-right of the map)."));

  // tools
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

  const runsEl = el("div");
  host.append(el("h3", {}, "TIN runs"), runsEl);
  function renderRuns() {
    runsEl.innerHTML = "";
    const runs = store.get("tinRuns");
    const cur = store.get("currentRun");
    if (!runs.length) runsEl.appendChild(el("p", { class: "muted" }, "No TIN yet. Import points and click Build TIN."));
    for (const r of [...runs].reverse()) {
      const s = r.stats;
      const card = el("div", { class: `card clickable${r.id === cur ? " selected" : ""}`, onClick: async () => { await ws.selectRun(r.id); await ws.redrawContours(); renderRuns(); } },
        el("div", { style: "display:flex;gap:6px;align-items:center" }, el("b", {}, `Run ${r.id}${r.name ? " · " + r.name : ""}`),
          r.issues_count ? el("span", { class: "badge warn" }, `${r.issues_count} issues`) : el("span", { class: "badge ok" }, "clean"),
          el("span", { class: "spacer", style: "flex:1" }),
          button("✕", async (ev) => { ev.stopPropagation(); if (confirm(`Delete TIN run ${r.id} and its contours/sections?`)) { await api.tin.delete(pid, r.id); store.set("currentRun", null); await ws.refreshTinRuns(); await ws.refreshContours(); await ws.refreshSectionSets(); renderRuns(); } }, "btn small danger")),
        el("dl", { class: "kv" },
          el("dt", {}, "triangles"), el("dd", {}, `${r.n_triangles} (${r.n_nodes} nodes, ${s.edges ?? "–"} edges)`),
          el("dt", {}, "removed"), el("dd", {}, `${s.duplicates_removed ?? 0} duplicates · ${s.triangles_removed_by_boundary ?? 0} by boundary`),
          el("dt", {}, "features"), el("dd", {}, `${s.segments ?? 0} segments, ${s.steiner_points ?? 0} crossings`),
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
