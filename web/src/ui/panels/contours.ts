import { api, ApiError, jobStatusText, waitForJob, type ContourSet, type ContourStyle } from "../../api";
import { store, toast } from "../../state";
import { button, el, field, numberInput, select } from "../dom";
import type { Workspace } from "../workspace";

const DEFAULT_STYLE: ContourStyle = { major_color: "#c2410c", minor_color: "#f59e0b", major_width: 2, minor_width: 1, ramp: null, opacity: 1, label_format: "{z:.2f}", label_prefix: "", label_suffix: "", label_every: 100, label_major_only: true, text_height: 1, show_labels: true };

function styleForm(style: Partial<ContourStyle>): { root: HTMLElement; read: () => ContourStyle } {
  const s = { ...DEFAULT_STYLE, ...style };
  const majorColor = el("input", { type: "color", value: s.major_color });
  const minorColor = el("input", { type: "color", value: s.minor_color });
  const majorWidth = numberInput(s.major_width, { min: "0.5", step: "0.5" });
  const minorWidth = numberInput(s.minor_width, { min: "0.5", step: "0.5" });
  const ramp = select([{ value: "", label: "major / minor colours" }, { value: "terrain", label: "terrain ramp" }, { value: "viridis", label: "viridis ramp" }, { value: "grey", label: "grey ramp" }], s.ramp || "");
  const opacity = el("input", { type: "range", min: "0.1", max: "1", step: "0.05", value: String(s.opacity) });
  const fmtIn = el("input", { type: "text", value: s.label_format });
  const prefix = el("input", { type: "text", value: s.label_prefix, placeholder: "e.g. RL " });
  const suffix = el("input", { type: "text", value: s.label_suffix, placeholder: "e.g. m" });
  const every = numberInput(s.label_every, { min: "5", step: "5" });
  const majorOnly = el("input", { type: "checkbox", checked: s.label_major_only });
  const showLabels = el("input", { type: "checkbox", checked: s.show_labels });
  const textH = numberInput(s.text_height, { min: "0.1", step: "0.1" });
  const root = el("div", {},
    el("div", { class: "row" }, field("Index (major) colour", majorColor), field("Minor colour", minorColor)),
    el("div", { class: "row3" }, field("Major width", majorWidth), field("Minor width", minorWidth), field("Opacity", opacity)),
    field("Colour by elevation", ramp, "overrides the two colours when set"),
    el("div", { class: "row3" }, field("Label format", fmtIn, "{z:.2f} · {z:.0f} · {z:.1f}"), field("Prefix", prefix), field("Suffix", suffix)),
    el("div", { class: "row" }, field("Label every (m)", every), field("DXF text height", textH)),
    el("label", { class: "check" }, showLabels, "Show labels"),
    el("label", { class: "check" }, majorOnly, "Label index contours only"),
  );
  const read = (): ContourStyle => ({ major_color: majorColor.value, minor_color: minorColor.value, major_width: Number(majorWidth.value), minor_width: Number(minorWidth.value),
    ramp: ramp.value || null, opacity: Number(opacity.value), label_format: fmtIn.value || "{z:.2f}", label_prefix: prefix.value, label_suffix: suffix.value,
    label_every: Number(every.value), label_major_only: majorOnly.checked, text_height: Number(textH.value), show_labels: showLabels.checked });
  return { root, read };
}

export function renderContoursPanel(ws: Workspace, host: HTMLElement): void {
  const pid = ws.project.id;
  const run = store.get("currentRun");
  const interval = numberInput(1, { min: "0.01", step: "0.5" });
  const major = numberInput(5, { min: "0", step: "1" });
  const base = numberInput(0, { step: "0.5" });
  const smoothing = select([{ value: "none", label: "none (exact on TIN)" }, { value: "chaikin", label: "Chaikin (rounded)" }], "none");
  const minSpacing = numberInput(0, { min: "0", step: "0.1" });
  const minLength = numberInput(0, { min: "0", step: "1" });
  const name = el("input", { type: "text", placeholder: "e.g. 1 m contours" });
  const sf = styleForm({});
  interval.addEventListener("change", () => { const v = Number(interval.value); major.value = String(v >= 1 ? 5 : 4); });

  const gen = button("Generate contours", async () => {
    if (store.get("currentRun") === null) { toast("Build a TIN first", "error"); return; }
    store.set("busy", "Contouring");
    try {
      let job = await api.contours.create(pid, { run_id: store.get("currentRun"), name: name.value, interval: Number(interval.value), major_every: Number(major.value), base: Number(base.value),
        smoothing: smoothing.value, min_spacing: Number(minSpacing.value), min_length: Number(minLength.value), style: sf.read() });
      if (job.status === "pending") store.set("busy", jobStatusText(job, "Contouring"));
      job = await waitForJob(job, (j) => store.set("busy", jobStatusText(j, "Contouring")));
      toast(`Contours generated: ${job.result?.n_lines} lines`, "ok");
      const sets = await api.contours.list(pid);
      store.set("visibleContourSets", [job.result!.id]);
      store.set("contourSets", sets);
      await ws.redrawContours();
      await ws.refreshProject();
      renderSets();
    } catch (e) {
      toast(e instanceof ApiError ? e.detail : String(e), "error");
    } finally {
      store.set("busy", null);
    }
  }, "btn primary");

  host.append(el("h3", {}, "Generate contours"),
    run === null ? el("p", { class: "hint" }, "Build a TIN first (TIN tab).") : el("p", { class: "hint" }, `From TIN run ${run}.`),
    el("div", { class: "card" },
      el("div", { class: "row3" }, field("Interval (m)", interval), field("Index every N", major, "0 = none"), field("Datum / base", base)),
      el("div", { class: "row3" }, field("Smoothing", smoothing), field("Min. vertex spacing (m)", minSpacing), field("Min. length (m)", minLength)),
      field("Name", name),
      el("details", { open: true }, el("summary", {}, "Style & labels"), sf.root),
      el("div", { class: "btn-row" }, gen),
    ));

  const setsEl = el("div");
  host.append(el("h3", {}, "Contour sets"), setsEl);
  function renderSets() {
    setsEl.innerHTML = "";
    const sets = store.get("contourSets");
    const visible = store.get("visibleContourSets");
    if (!sets.length) setsEl.appendChild(el("p", { class: "muted" }, "No contour sets yet."));
    for (const s of [...sets].reverse()) setsEl.appendChild(setCard(s, visible.includes(s.id)));
  }
  function setCard(s: ContourSet, isVisible: boolean): HTMLElement {
    const nameIn = el("input", { type: "text", value: s.name || "", placeholder: `Set ${s.id}` });
    const sf2 = styleForm(s.style);
    const details = el("details", {}, el("summary", {}, "Edit name & style"), field("Name", nameIn), sf2.root,
      el("div", { class: "btn-row" }, button("Apply", async () => {
        try {
          await api.contours.patch(pid, s.id, { name: nameIn.value, style: sf2.read() });
          store.set("contourSets", await api.contours.list(pid));
          await ws.redrawContours();
          toast("Style updated", "ok");
          renderSets();
        } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
      }, "btn primary small")));
    return el("div", { class: `card${isVisible ? " selected" : ""}` },
      el("div", { style: "display:flex;gap:6px;align-items:center" }, el("b", {}, s.name || `Set ${s.id}`), el("span", { class: "badge" }, `${s.params.interval} m`),
        el("span", { class: "muted" }, `${s.n_lines} lines · ${s.levels.length} levels · TIN ${s.run_id}`), el("span", { style: "flex:1" }),
        button("✕", async () => { if (confirm("Delete this contour set?")) { await api.contours.delete(pid, s.id); await ws.refreshContours(); renderSets(); } }, "btn small danger")),
      el("div", { class: "legend", style: "margin:4px 0" }, el("span", {}, el("i", { style: `background:${s.style.major_color || "#c2410c"}` }), "index"), el("span", {}, el("i", { style: `background:${s.style.minor_color || "#f59e0b"}` }), "minor"),
        el("span", {}, `labels ${s.style.show_labels === false ? "off" : `every ${s.style.label_every ?? 100} m`}`)),
      details,
    );
  }
  renderSets();
}
