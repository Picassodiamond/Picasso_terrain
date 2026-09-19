import { api, ApiError, waitForJob } from "../../api";
import { store, toast } from "../../state";
import { button, download, el, field, fmt, fmtChainage, numberInput, parseChainage, select } from "../dom";
import type { Workspace } from "../workspace";

export function renderSectionsPanel(ws: Workspace, host: HTMLElement): void {
  const pid = ws.project.id;
  const als = store.get("alignments");
  const runs = store.get("tinRuns");
  const alSel = select(als.map((a) => ({ value: String(a.id), label: `${a.name} (${fmt(a.length, 0)} m)` })), String(store.get("currentAlignment") ?? ""));
  const runSel = select(runs.map((r) => ({ value: String(r.id), label: `TIN ${r.id} (${r.n_triangles} tri)` })), String(store.get("currentRun") ?? ""));
  const interval = numberInput(20, { min: "0.5", step: "5" });
  const profInt = numberInput(10, { min: "0.5", step: "5" });
  const left = numberInput(15, { min: "0.5", step: "1" });
  const right = numberInput(15, { min: "0.5", step: "1" });
  const curvePts = el("input", { type: "checkbox", checked: true });
  const edges = el("input", { type: "checkbox", checked: true });
  const extra = el("input", { type: "text", placeholder: "1+250, 1+275.5" });
  const name = el("input", { type: "text", placeholder: "optional" });
  const gen = button("Generate profile & cross-sections", async () => {
    if (!alSel.value) { toast("Create an alignment first", "error"); return; }
    if (!runSel.value) { toast("Build a TIN first", "error"); return; }
    store.set("busy", "Sampling sections");
    try {
      let job = await api.sections.create(pid, { alignment_id: Number(alSel.value), run_id: Number(runSel.value), interval: Number(interval.value), profile_interval: Number(profInt.value),
        left: Number(left.value), right: Number(right.value), include_curve_points: curvePts.checked, include_edge_crossings: edges.checked, name: name.value,
        extra_chainages: extra.value.split(/[;,]/).map((s) => s.trim()).filter(Boolean).map(parseChainage) });
      job = await waitForJob(job);
      toast(`${job.result?.summary?.sections ?? ""} cross-sections generated`, "ok");
      store.set("currentSectionIndex", 0);
      const sets = await api.sections.list(pid);
      store.set("sectionSets", sets);
      store.set("currentSectionSet", job.result!.id);
      ws.showCharts(true);
      renderSets();
    } catch (e) {
      toast(e instanceof ApiError ? e.detail : String(e), "error");
    } finally {
      store.set("busy", null);
    }
  }, "btn primary");

  host.append(el("h3", {}, "Profile & cross-sections"),
    el("div", { class: "card" },
      el("div", { class: "row" }, field("Alignment", alSel), field("TIN", runSel)),
      el("div", { class: "row" }, field("Section interval (m)", interval), field("Profile station interval (m)", profInt)),
      el("div", { class: "row" }, field("Left width (m)", left), field("Right width (m)", right)),
      el("label", { class: "check" }, curvePts, "Sections at curve BC / MC / EC"),
      el("label", { class: "check" }, edges, "Include every TIN edge crossing (exact ground line)"),
      el("div", { class: "row" }, field("Extra chainages", extra, "comma separated, 0+000 or metres"), field("Name", name)),
      el("div", { class: "btn-row" }, gen),
      als.length ? null : el("p", { class: "hint" }, "Draw an alignment first (Alignment tab)."),
    ));

  host.append(el("p", { class: "hint" }, "Click a section line on the map or use ◀ ▶ (or arrow keys) in the chart pane to browse stations. Hover the charts to see the position on the map. Section lines can be hidden in the Layers panel."));

  const setsEl = el("div");
  host.append(el("h3", {}, "Section sets"), setsEl);
  function renderSets() {
    setsEl.innerHTML = "";
    const sets = store.get("sectionSets");
    const cur = store.get("currentSectionSet");
    if (!sets.length) setsEl.appendChild(el("p", { class: "muted" }, "None yet."));
    for (const s of [...sets].reverse()) {
      const a = als.find((x) => x.id === s.alignment_id);
      setsEl.appendChild(el("div", { class: `card clickable${s.id === cur ? " selected" : ""}`, onClick: () => { store.set("currentSectionIndex", 0); store.set("currentSectionSet", s.id); ws.showCharts(true); renderSets(); } },
        el("div", { style: "display:flex;gap:6px;align-items:center;flex-wrap:wrap" }, el("b", {}, s.name || `${a?.name ?? "alignment " + s.alignment_id} · every ${s.params.interval} m`),
          el("span", { class: "badge" }, `${s.summary.sections} sections`), s.summary.outside_sections ? el("span", { class: "badge warn" }, `${s.summary.outside_sections} outside TIN`) : null,
          el("span", { style: "flex:1" }),
          button("✕", async (ev) => { ev.stopPropagation(); if (confirm("Delete this section set?")) { await api.sections.delete(pid, s.id); await ws.refreshSectionSets(); renderSets(); } }, "btn small danger")),
        el("div", { class: "muted" }, `L ${s.params.left} m / R ${s.params.right} m · profile ${s.summary.points} points · RL ${fmt(s.summary.z_min, 2)} – ${fmt(s.summary.z_max, 2)} · ${fmtChainage(s.summary.chainage_start ?? 0)} → ${fmtChainage(s.summary.chainage_end ?? 0)}`),
        el("div", { class: "btn-row" }, button("Profile.csv", (ev) => { ev.stopPropagation(); download(api.sections.profileCsvUrl(pid, s.id)); }, "btn small"),
          button("Cross.csv", (ev) => { ev.stopPropagation(); download(api.sections.crossCsvUrl(pid, s.id)); }, "btn small"),
          button("DXF (plan)", (ev) => { ev.stopPropagation(); download(api.exportUrl.dxf(pid, { section_set: s.id, alignments: s.alignment_id, points: false, contours: true })); }, "btn small")),
      ));
    }
  }
  renderSets();
}
