/** Stage panels of the road design workspace. Each renderer reads `ws.data` and calls the
 *  workspace's save / build actions; the workspace re-renders plan, profile and section. */
import * as d3 from "d3";
import { api, ApiError } from "../../api";
import { store, toast } from "../../state";
import { button, download, el, field, fmt, fmtChainage, numberInput, select } from "../../ui/dom";
import { kindLabel, openSheetPreview } from "./sheets";
import type { RoadWorkspace } from "./workspace";

const err = (e: unknown) => toast(e instanceof ApiError ? e.detail : String(e), "error");
function appendAll(host: HTMLElement, ...nodes: (HTMLElement | null)[]): void { for (const n of nodes) if (n) host.appendChild(n); }
const num = (v: any, d = 2) => (v == null || Number.isNaN(Number(v)) ? "–" : Number(v).toFixed(d));

// ---------------------------------------------------------------- shared widgets
export function renderChecks(checks: any[], emptyText = "No checks to show"): HTMLElement {
  const box = el("div", { class: "checks" });
  if (!checks?.length) { box.appendChild(el("p", { class: "muted" }, emptyText)); return box; }
  const order: Record<string, number> = { error: 0, warning: 1, info: 2 };
  for (const c of [...checks].sort((a, b) => (order[a.severity] ?? 3) - (order[b.severity] ?? 3))) {
    const cls = c.ok === false ? c.severity : c.ok === true ? (c.severity === "warning" ? "warning" : "ok") : "info";
    box.appendChild(el("div", { class: `check-row ${cls}`, title: `${c.source || ""}${c.status ? ` [${c.status}]` : ""}` },
      el("span", { class: "dot" }),
      el("span", {}, el("span", { class: "where" }, c.where ? `${c.where}: ` : ""), c.message,
        c.status === "placeholder" ? el("span", { class: "badge warn", title: "standard value not yet verified against the printed document" }, "verify") : null,
        c.status === "deviation" ? el("span", { class: "badge dev" }, "deviation") : null)));
  }
  return box;
}

function checkSummary(checks: any[]): HTMLElement {
  const errors = checks.filter((c) => c.ok === false && c.severity === "error").length;
  const warns = checks.filter((c) => (c.ok === false || c.ok === true) && c.severity === "warning").length;
  return el("span", {}, errors ? el("span", { class: "badge err" }, `${errors} failing`) : null, warns ? el("span", { class: "badge warn" }, `${warns} warnings`) : null,
    !errors && !warns && checks.length ? el("span", { class: "badge ok" }, "all checks pass") : null);
}

function numCell(value: number | null | undefined, onChange: (v: number) => void, step = "0.01", cls = "num"): HTMLInputElement {
  const i = el("input", { type: "number", class: cls, step, value: value == null ? "" : String(Number(value.toFixed ? value.toFixed(3) : value)) });
  i.addEventListener("change", () => onChange(Number(i.value)));
  return i;
}

// ---------------------------------------------------------------- 1. alignment
export function renderAlignmentStage(host: HTMLElement, ws: RoadWorkspace): void {
  const d = ws.data;
  const run = ws.run;
  // terrain snapshot
  const newer = ws.runs.filter((r) => run && r.id > run.id);
  host.appendChild(el("div", { class: "card" }, el("h4", {}, "Terrain snapshot"),
    run ? el("dl", { class: "kv" }, el("dt", {}, "TIN run"), el("dd", {}, `${run.id}${run.name ? " · " + run.name : ""}`), el("dt", {}, "triangles"), el("dd", {}, `${run.n_triangles}`),
      el("dt", {}, "RL"), el("dd", {}, `${fmt(run.z_range[0] ?? undefined, 2)} – ${fmt(run.z_range[1] ?? undefined, 2)}`)) : el("p", { class: "muted" }, "No TIN run."),
    newer.length ? el("div", { class: "terrain-badge stale" }, `Newer terrain available: run ${newer[newer.length - 1].id}`, button("Rebase", () => void ws.rebase(newer[newer.length - 1].id), "btn small"))
      : el("div", { class: "terrain-badge" }, "Up to date with the latest terrain")));

  // design parameters -> standards context (option lists come from the standard file)
  const st = ws.design.settings || {};
  const stdInfo = d.standards;
  const opt = stdInfo?.options || {};
  const ctx = d.overview?.context || {};
  const pick = (list: any[] | undefined, fallback: [string, string][]) => (list?.length ? list.map((o: any) => ({ value: String(o.value), label: String(o.label) })) : fallback.map(([v, l]) => ({ value: v, label: l })));
  const cls = select(pick(opt.classes, [["I", "Class I"], ["II", "Class II"], ["III", "Class III"], ["IV", "Class IV"]]), String(ctx.road_class ?? st.road_class ?? "III"));
  const ter = select(pick(opt.terrains, [["plain", "Plain"], ["rolling", "Rolling"], ["mountainous", "Mountainous"], ["steep", "Steep"]]), String(st.terrain ?? "rolling"));
  const speeds: number[] = opt.speeds?.length ? opt.speeds : [120, 100, 80, 60, 40, 30, 20];
  const spd = select([{ value: "", label: `standard (${ctx.design_speed ?? "-"} km/h)` }, ...speeds.map((v) => ({ value: String(v), label: `${v} km/h` }))], st.design_speed ? String(st.design_speed) : "");
  const surf = select(pick(opt.surfaces, [["bituminous", "bituminous"], ["concrete", "concrete"], ["gravel", "gravel"], ["earthen", "earthen"]]), String(st.surface ?? "bituminous"));
  const mat = select(pick(opt.materials, [["soil", "Ordinary soil"], ["disintegrated_rock", "Disintegrated rock"], ["soft_rock", "Soft rock, shale"], ["medium_rock", "Medium rock"], ["hard_rock", "Hard rock"]]), String(ctx.material ?? st.material ?? "soil"));
  const rtype = select(pick(opt.road_types, [["national_highway", "National Highway"], ["feeder_road", "Feeder Road"], ["district_road", "District Road"]]), String(ctx.road_type ?? st.road_type ?? "feeder_road"));
  const snow = el("input", { type: "checkbox", checked: !!st.snow_bound });
  const alt = el("input", { type: "checkbox", checked: st.altitude_compensation !== false });
  const save = () => void ws.saveSettings({ road_class: cls.value, terrain: ter.value, design_speed: spd.value ? Number(spd.value) : null, surface: surf.value, material: mat.value,
    road_type: rtype.value, snow_bound: snow.checked, altitude_compensation: alt.checked });
  for (const c of [cls, ter, spd, surf, mat, rtype, snow, alt]) c.addEventListener("change", save);
  host.appendChild(el("div", { class: "card" }, el("h4", {}, "Design parameters"),
    el("div", { class: "row" }, field("Road class (functional)", cls), field("Terrain", ter)), el("div", { class: "row" }, field("Design speed", spd), field("Surface", surf)),
    el("div", { class: "row" }, field("Cut material", mat), field("Road type (right of way)", rtype)),
    el("div", { class: "row" }, el("label", { class: "check", title: "e_max limited to 7 % in snow-bound areas (NRS 2070 cl. 11.6 b)" }, snow, "snow-bound area"),
      el("label", { class: "check", title: "maximum gradient eased by 0.5 % per 500 m above mean sea level (NRS 2070 cl. 10.1.2 a)" }, alt, "ease gradients for altitude")),
    el("p", { class: "hint" }, `Standard: ${stdInfo?.standard?.name ?? "–"} `, stdInfo?.standard?.status === "verified" ? el("span", { class: "badge ok", title: stdInfo.standard.version || "" }, "verified") : stdInfo?.standard?.status === "placeholder" ? el("span", { class: "badge warn", title: "values transcribed as a skeleton; verify each against the printed standard" }, "placeholder values") : el("span", { class: "badge ok" }, "verified")),
    el("details", {}, el("summary", { class: "muted" }, "Resolved parameters"),
      el("table", { class: "data" }, el("tr", {}, el("th", {}, "parameter"), el("th", {}, "value"), el("th", {}, "source")),
        ...(stdInfo?.parameters || []).map((p: any) => el("tr", {}, el("td", {}, p.key.replace(/_/g, " ")), el("td", {}, p.value == null ? "–" : `${p.value} ${p.unit}`), el("td", { class: "muted", title: p.note || "" }, `${p.source || p.status}${p.status === "deviation" ? " (deviation)" : ""}`)))))));

  // horizontal alignment: editable IP table
  const h = d.horizontal;
  const ips: any[] = ws.editIps;
  const startIn = numCell(ws.editStart, (v) => { ws.editStart = v; ws.markDirty("horizontal"); }, "1");
  const tbl = el("table", { class: "data" }, el("tr", {}, el("th", {}, "#"), el("th", {}, "X"), el("th", {}, "Y"), el("th", {}, "R"), el("th", {}, "Ls"), el("th")));
  const renderRows = () => {
    while (tbl.rows.length > 1) tbl.deleteRow(1);
    ips.forEach((p, i) => {
      const last = i === ips.length - 1;
      const fixedEnd = i === 0 || last;
      tbl.appendChild(el("tr", {}, el("td", {}, p.label || String(i)),
        el("td", {}, numCell(p.x, (v) => { p.x = v; ws.markDirty("horizontal"); ws.previewHorizontal(); })),
        el("td", {}, numCell(p.y, (v) => { p.y = v; ws.markDirty("horizontal"); ws.previewHorizontal(); })),
        el("td", {}, fixedEnd ? el("span", { class: "muted" }, "–") : numCell(p.radius, (v) => { p.radius = v; ws.markDirty("horizontal"); ws.previewHorizontal(); }, "5")),
        el("td", {}, fixedEnd ? el("span", { class: "muted" }, "–") : numCell(p.transition, (v) => { p.transition = v; ws.markDirty("horizontal"); ws.previewHorizontal(); }, "5")),
        el("td", {}, el("div", { class: "btn-row" },
          button("+", () => { const q = ips[Math.min(i + 1, ips.length - 1)]; ips.splice(i + 1, 0, { x: (p.x + q.x) / 2, y: (p.y + q.y) / 2, radius: 0, transition: 0, label: "" }); ips.forEach((r, k) => { if (!r.label || /^\d+$/.test(r.label)) r.label = String(k); }); ws.markDirty("horizontal"); renderRows(); ws.previewHorizontal(); }, "btn small"),
          ips.length > 2 ? button("✕", () => { ips.splice(i, 1); ips.forEach((r, k) => { if (!r.label || /^\d+$/.test(r.label)) r.label = String(k); }); ws.markDirty("horizontal"); renderRows(); ws.previewHorizontal(); }, "btn small danger") : null))));
    });
  };
  renderRows();
  const saveBtn = button("Save alignment", () => void ws.saveHorizontal(), "btn primary");
  host.appendChild(el("div", { class: "card" }, el("h4", {}, "Horizontal alignment ", h?.checks ? checkSummary(h.checks) : null),
    el("p", { class: "hint" }, `Drag the IP squares on the plan, or edit the table. R = curve radius, Ls = transition (spiral) length; 0 = none. ${h?.seeded_from ? `Seeded from project alignment "${h.seeded_from.name}".` : ""}`),
    el("div", { class: "row" }, field("Start chainage", startIn), el("div", { class: "field" }, el("span", { class: "field-label" }, "Length"), el("span", { class: "mono" }, h?.length ? `${fmt(h.length, 2)} m` : "–"))),
    tbl,
    el("div", { class: "btn-row" }, saveBtn, button("Revert", () => { ws.revertHorizontal(); }, "btn"), button("Fit alignment", () => ws.fitAlignment(), "btn small"))));

  // what follows an alignment change
  const followSel = select([
    { value: "stretch", label: "stretch the grade line to the new length (keeps cut / fill depths)" },
    { value: "refit", label: "re-fit the grade line to the ground" },
    { value: "trim", label: "trim / extend the grade line to the new range" },
    { value: "keep", label: "keep the grade line as it is (flag it as out of date)" },
  ], ws.followMode, { "data-follow": "vertical" });
  followSel.addEventListener("change", () => void ws.setFollow({ vertical: followSel.value }));
  const autoCor = el("input", { type: "checkbox", checked: ws.autoCorridor });
  autoCor.addEventListener("change", () => void ws.setFollow({ corridor: autoCor.checked }));
  host.appendChild(el("div", { class: "card" }, el("h4", {}, "When the alignment changes"),
    field("Grade line", followSel),
    el("label", { class: "check" }, autoCor, "rebuild the corridor and cross-sections at once (last corridor settings)"),
    el("p", { class: "hint" }, "Applied when you save the alignment or the profile. While you drag an IP the profile already shows the ground under the new centre line.")));

  if (h?.table?.length) {
    const rows = h.table.filter((r: any) => r.radius > 0);
    host.appendChild(el("details", { class: "card", open: rows.length ? "true" : undefined }, el("summary", {}, el("b", {}, `Curve table (${rows.length})`)),
      el("table", { class: "data" }, el("tr", {}, el("th", {}, "IP"), el("th", {}, "R"), el("th", {}, "Δ°"), el("th", {}, "T"), el("th", {}, "Lc"), el("th", {}, "Ls"), el("th", {}, "E"), el("th", {}, "e %"), el("th", {}, "TS/BC"), el("th", {}, "ST/EC")),
        ...rows.map((r: any) => el("tr", { class: r.valid ? "" : "error", title: r.message || "" }, el("td", {}, r.label || r.index), el("td", {}, num(r.radius, 0)), el("td", {}, num(r.deflection_deg, 2)), el("td", {}, num(r.tangent)),
          el("td", {}, num(r.arc_length)), el("td", {}, num(r.transition, 0)), el("td", {}, num(r.external)), el("td", {}, num(r.superelevation_pct, 1)),
          el("td", {}, r.ts != null ? fmtChainage(r.ts) : "–"), el("td", {}, r.st != null ? fmtChainage(r.st) : "–"))))));
    const sup = h.superelevation?.curves || [];
    if (sup.length) host.appendChild(el("details", { class: "card" }, el("summary", {}, el("b", {}, "Superelevation development")),
      el("p", { class: "hint" }, `e_max ${h.superelevation.settings.e_max} %, camber ${h.superelevation.settings.camber} %, runoff over the transition or ${h.superelevation.settings.relative_gradient} % relative gradient.`),
      el("table", { class: "data" }, el("tr", {}, el("th", {}, "IP"), el("th", {}, "e %"), el("th", {}, "runoff"), el("th", {}, "start"), el("th", {}, "full"), el("th", {}, "end")),
        ...sup.map((c: any) => el("tr", { class: c.feasible ? "" : "error" }, el("td", {}, c.ip_index), el("td", {}, num(c.e_pct, 1)), el("td", {}, num(c.runoff, 0)), el("td", {}, fmtChainage(c.start)), el("td", {}, `${fmtChainage(c.full_from)} – ${fmtChainage(c.full_to)}`), el("td", {}, fmtChainage(c.end)))))));
  }
  host.appendChild(el("div", { class: "card" }, el("h4", {}, "Checks"), renderChecks(h?.checks || [], "Save the alignment to run the checks")));
}

// ---------------------------------------------------------------- 2. profile
export function renderProfileStage(host: HTMLElement, ws: RoadWorkspace): void {
  const v = ws.data.vertical;
  const spacing = numberInput(200, { min: "20", step: "10", style: "width:90px" });
  host.appendChild(el("div", { class: "card" }, el("h4", {}, "Vertical alignment ", v?.checks ? checkSummary(v.checks) : null),
    el("p", { class: "hint" }, "Fit to ground creates a grade line that follows the terrain with PVIs at the spacing; then drag PVIs in the profile below or edit the table. L = length of the parabolic vertical curve."),
    el("div", { class: "row", style: "align-items:flex-end" }, field("PVI spacing (m)", spacing), button("Fit to ground", () => void ws.autoVertical(Number(spacing.value) || 200), "btn primary")),
    !ws.data.horizontal?.ips?.length ? el("p", { class: "error" }, "Define the horizontal alignment first.") : null));
  if (v?.stale) host.appendChild(el("div", { class: "terrain-badge stale stale-banner", style: "display:block" },
    el("div", {}, el("b", {}, "Grade line out of date. "), `${(v.stale_reasons || []).join("; ")}.`),
    el("div", { class: "btn-row", style: "margin:6px 0 0" },
      button("Stretch to new length", () => void ws.followVertical("stretch"), "btn small"),
      button("Re-fit to ground", () => void ws.followVertical("refit"), "btn small"),
      button("Trim / extend to range", () => void ws.followVertical("trim"), "btn small"))));
  const pvis: any[] = ws.editPvis;
  const tbl = el("table", { class: "data" }, el("tr", {}, el("th", {}, "#"), el("th", {}, "chainage"), el("th", {}, "RL"), el("th", {}, "L"), el("th", {}, "grade out"), el("th", {}, "K"), el("th")));
  const rows = (): void => {
    while (tbl.rows.length > 1) tbl.deleteRow(1);
    const info = new Map<number, any>((v?.table || []).map((r: any) => [r.index, r]));
    pvis.forEach((p, i) => {
      const r = info.get(i) || {};
      tbl.appendChild(el("tr", {}, el("td", {}, String(i)),
        el("td", {}, numCell(p.chainage, (x) => { p.chainage = x; ws.markDirty("vertical"); }, "1")),
        el("td", {}, numCell(p.elevation, (x) => { p.elevation = x; ws.markDirty("vertical"); })),
        el("td", {}, i === 0 || i === pvis.length - 1 ? el("span", { class: "muted" }, "–") : numCell(p.length, (x) => { p.length = x; ws.markDirty("vertical"); }, "5")),
        el("td", { class: "mono" }, r.grade_out != null ? `${r.grade_out >= 0 ? "+" : ""}${num(r.grade_out)} %` : "–"),
        el("td", { class: "mono" }, r.K != null ? `${num(r.K, 1)} ${r.kind}` : "–"),
        el("td", {}, el("div", { class: "btn-row" },
          button("+", () => { const q = pvis[Math.min(i + 1, pvis.length - 1)]; pvis.splice(i + 1, 0, { chainage: (p.chainage + q.chainage) / 2, elevation: (p.elevation + q.elevation) / 2, length: 40 }); ws.markDirty("vertical"); rows(); }, "btn small"),
          pvis.length > 2 ? button("✕", () => { pvis.splice(i, 1); ws.markDirty("vertical"); rows(); }, "btn small danger") : null))));
    });
  };
  rows();
  host.appendChild(el("div", { class: "card" }, tbl, el("div", { class: "btn-row" }, button("Save profile", () => void ws.saveVertical(), "btn primary"), button("Revert", () => ws.revertVertical(), "btn")),
    v?.pvis?.length ? el("p", { class: "hint" }, `${v.pvis.length} PVIs · ${fmtChainage(v.start_chainage)} – ${fmtChainage(v.end_chainage)} · source ${v.source}`) : null));
  host.appendChild(el("div", { class: "card" }, el("h4", {}, "Checks"), renderChecks(v?.checks || [], "Fit or save a vertical alignment to run the checks")));
}

// ---------------------------------------------------------------- 3. templates
export function renderTemplatesStage(host: HTMLElement, ws: RoadWorkspace): void {
  const doc = ws.editTemplates;
  if (!doc) { host.appendChild(el("p", { class: "muted" }, "Loading templates…")); return; }
  const catalogue: any[] = ws.data.templates?.catalogue || [];
  let current = doc.templates[0]?.id;
  const box = el("div");
  const rerender = () => { box.innerHTML = ""; drawTemplate(); };
  const tplSel = select(doc.templates.map((t: any) => ({ value: t.id, label: `${t.name} (${t.id})` })), current);
  tplSel.addEventListener("change", () => { current = tplSel.value; rerender(); });
  const compRow = (comps: any[], c: any, i: number) => el("tr", {},
    el("td", {}, (() => { const s = select(catalogue.map((k) => ({ value: k.kind, label: k.label })), c.kind, { class: "mini" }); s.addEventListener("change", () => { c.kind = s.value; c.name = s.value; ws.markDirty("templates"); }); return s; })()),
    el("td", {}, numCell(c.width, (x) => { c.width = x; ws.markDirty("templates"); })),
    el("td", {}, numCell(c.slope, (x) => { c.slope = x; ws.markDirty("templates"); }, "0.5")),
    el("td", {}, (() => { const cb = el("input", { type: "checkbox", checked: !!c.superelevate }); cb.addEventListener("change", () => { c.superelevate = cb.checked; ws.markDirty("templates"); }); return cb; })()),
    el("td", {}, button("✕", () => { comps.splice(i, 1); ws.markDirty("templates"); rerender(); }, "btn small danger")));
  const sideTable = (t: any, side: "left" | "right") => {
    const tbl = el("table", { class: "data" }, el("tr", {}, el("th", {}, side === "left" ? "Left (from CL outward)" : "Right (from CL outward)"), el("th", {}, "width m"), el("th", {}, "slope %"), el("th", {}, "super"), el("th")));
    (t[side] || []).forEach((c: any, i: number) => tbl.appendChild(compRow(t[side], c, i)));
    const add = select(catalogue.map((k) => ({ value: k.kind, label: `+ ${k.label}` })), catalogue[0]?.kind ?? "lane", { class: "mini" });
    return el("div", {}, tbl, el("div", { class: "btn-row" }, add, button("Add", () => { const k = catalogue.find((x) => x.kind === add.value); t[side].push({ kind: add.value, name: add.value, ...(k?.defaults || {}) }); ws.markDirty("templates"); rerender(); }, "btn small"),
      button("Mirror to " + (side === "left" ? "right" : "left"), () => { t[side === "left" ? "right" : "left"] = JSON.parse(JSON.stringify(t[side])); ws.markDirty("templates"); rerender(); }, "btn small")));
  };
  const drawTemplate = () => {
    const t = doc.templates.find((x: any) => x.id === current) || doc.templates[0];
    if (!t) return;
    const name = el("input", { type: "text", value: t.name }); name.addEventListener("change", () => { t.name = name.value; ws.markDirty("templates"); });
    const cut = t.cut || {}, fill = t.fill || {};
    const ditchOn = el("input", { type: "checkbox", checked: !!cut.ditch });
    const ditch = cut.ditch || { foreslope: 1.0, depth: 0.5, bottom: 0.5 };
    ditchOn.addEventListener("change", () => { t.cut.ditch = ditchOn.checked ? { ...ditch } : null; ws.markDirty("templates"); rerender(); });
    appendAll(box,
      field("Template name", name),
      sideTable(t, "left"), sideTable(t, "right"),
      el("h4", {}, "Cut side"), el("div", { class: "row" }, field("Slope h:v", numCell(cut.slope, (x) => { t.cut.slope = x; ws.markDirty("templates"); }, "0.25")),
        field("Bench height m", numCell(cut.bench_height, (x) => { t.cut.bench_height = x; ws.markDirty("templates"); }, "0.5")), field("Bench width m", numCell(cut.bench_width, (x) => { t.cut.bench_width = x; ws.markDirty("templates"); }, "0.5"))),
      el("label", { class: "check" }, ditchOn, "Side ditch in cut"),
      cut.ditch ? el("div", { class: "row" }, field("Ditch depth", numCell(cut.ditch.depth, (x) => { t.cut.ditch.depth = x; ws.markDirty("templates"); }, "0.1")), field("Ditch bottom", numCell(cut.ditch.bottom, (x) => { t.cut.ditch.bottom = x; ws.markDirty("templates"); }, "0.1")),
        field("Foreslope h:v", numCell(cut.ditch.foreslope, (x) => { t.cut.ditch.foreslope = x; ws.markDirty("templates"); }, "0.25"))) : null,
      el("h4", {}, "Fill side"), el("div", { class: "row" }, field("Slope h:v", numCell(fill.slope, (x) => { t.fill.slope = x; ws.markDirty("templates"); }, "0.25")),
        field("Bench height m (0 = none)", numCell(fill.bench_height, (x) => { t.fill.bench_height = x; ws.markDirty("templates"); }, "0.5")), field("Bench width m", numCell(fill.bench_width, (x) => { t.fill.bench_width = x; ws.markDirty("templates"); }, "0.5"))),
      field("Daylight search width m", numCell(t.max_offset ?? 60, (x) => { t.max_offset = x; ws.markDirty("templates"); }, "5"), "how far from the centreline a side slope may look for the ground; beyond it the section is flagged (wall candidate)"),
    );
  };
  drawTemplate();
  host.appendChild(el("div", { class: "card" }, el("h4", {}, "Templates (typical sections)"),
    el("p", { class: "hint" }, "Components are laid out from the centreline outward on each side; the outermost point is the hinge where the cut or fill slope starts. Slopes are % rise outward (negative falls away). Components marked 'super' follow the superelevation."),
    el("div", { class: "row", style: "align-items:flex-end" }, field("Template", tplSel),
      button("New", () => { const id = `t${doc.templates.length + 1}`; doc.templates.push({ ...JSON.parse(JSON.stringify(doc.templates[0] || ws.data.templates.defaults)), id, name: `Template ${doc.templates.length + 1}` }); current = id; ws.markDirty("templates"); ws.renderStage(); }, "btn small"),
      doc.templates.length > 1 ? button("Delete", () => { doc.templates = doc.templates.filter((x: any) => x.id !== current); doc.assignments = doc.assignments.filter((a: any) => a.template_id !== current); current = doc.templates[0].id; ws.markDirty("templates"); ws.renderStage(); }, "btn small danger") : null),
    box));

  // assignments
  const asg = el("table", { class: "data" }, el("tr", {}, el("th", {}, "from"), el("th", {}, "to"), el("th", {}, "template"), el("th")));
  const drawAsg = () => {
    while (asg.rows.length > 1) asg.deleteRow(1);
    doc.assignments.forEach((a: any, i: number) => {
      const s = select(doc.templates.map((t: any) => ({ value: t.id, label: t.name })), a.template_id, { class: "mini" }); s.addEventListener("change", () => { a.template_id = s.value; ws.markDirty("templates"); });
      asg.appendChild(el("tr", {}, el("td", {}, numCell(a.from, (x) => { a.from = x; ws.markDirty("templates"); }, "10")), el("td", {}, numCell(a.to, (x) => { a.to = x; ws.markDirty("templates"); }, "10")), el("td", {}, s),
        el("td", {}, button("✕", () => { doc.assignments.splice(i, 1); ws.markDirty("templates"); drawAsg(); }, "btn small danger"))));
    });
  };
  drawAsg();
  const h = ws.data.horizontal;
  host.appendChild(el("div", { class: "card" }, el("h4", {}, "Assignments by chainage"),
    el("p", { class: "hint" }, "Where no assignment applies the first template is used."), asg,
    el("div", { class: "btn-row" }, button("Add range", () => { doc.assignments.push({ from: h?.start_chainage ?? 0, to: h?.end_chainage ?? 100, template_id: doc.templates[0].id }); ws.markDirty("templates"); drawAsg(); }, "btn small"))));

  // superelevation settings
  const se = doc.superelevation || {};
  const defaults = ws.data.templates?.superelevation || {};
  const en = el("input", { type: "checkbox", checked: se.enabled !== false }); en.addEventListener("change", () => { se.enabled = en.checked; doc.superelevation = se; ws.markDirty("templates"); });
  const seField = (key: string, label: string, step: string) => field(label, numCell(se[key] ?? defaults[key], (x) => { se[key] = x; doc.superelevation = se; ws.markDirty("templates"); }, step), `standard: ${defaults[key]}`);
  host.appendChild(el("div", { class: "card" }, el("h4", {}, "Superelevation"), el("label", { class: "check" }, en, "Apply superelevation on curves"),
    el("div", { class: "row" }, seField("e_max", "e max %", "0.5"), seField("camber", "Normal camber %", "0.5")),
    el("div", { class: "row" }, seField("relative_gradient", "Relative gradient %", "0.05"), seField("rotated_width", "Rotated width m", "0.25")),
    el("div", { class: "btn-row" }, button("Save templates", () => void ws.saveTemplates(), "btn primary"), button("Revert", () => ws.revertTemplates(), "btn")),
    ws.data.templates?.problems?.length ? el("p", { class: "error" }, ws.data.templates.problems.join("; ")) : null));
}

// ---------------------------------------------------------------- 4. earthworks
export function renderEarthworksStage(host: HTMLElement, ws: RoadWorkspace): void {
  const cor = ws.data.corridor;
  if (cor && ws.data.overview?.corridor_stale) host.appendChild(el("div", { class: "terrain-badge stale stale-banner", style: "display:block" },
    el("b", {}, "Corridor out of date. "), "The alignment, profile or templates changed after this corridor was built; sections, quantities and drawings still show the old geometry. ",
    el("div", { class: "btn-row", style: "margin:6px 0 0" }, button("Rebuild now", () => void ws.buildCorridor(ws.lastCorridorParams()), "btn small primary"))));
  const interval = numberInput(cor?.summary?.params?.interval ?? 20, { min: "5", step: "5", style: "width:80px" });
  const prism = el("input", { type: "checkbox", checked: !!cor?.summary?.params?.prismoidal });
  const cutF = numberInput(cor?.summary?.params?.cut_factor ?? 1.0, { min: "0.5", max: "1.5", step: "0.05", style: "width:80px" });
  const fillF = numberInput(cor?.summary?.params?.fill_factor ?? 1.0, { min: "0.5", max: "1.5", step: "0.05", style: "width:80px" });
  host.appendChild(el("div", { class: "card" }, el("h4", {}, "Corridor and earthworks"),
    el("p", { class: "hint" }, "Sweeps the assigned templates along the alignment, daylights each section against the terrain and computes cut / fill areas, volumes (average end area or prismoidal) and the mass haul."),
    el("div", { class: "row", style: "align-items:flex-end" }, field("Section interval m", interval), field("Cut factor", cutF, "shrinkage of cut placed as fill"), field("Fill factor", fillF, "compaction demand")),
    el("label", { class: "check" }, prism, "Prismoidal volumes (mid-sections, twice the work)"),
    el("div", { class: "btn-row" }, button("Build corridor", () => void ws.buildCorridor({ interval: Number(interval.value) || 20, prismoidal: prism.checked, cut_factor: Number(cutF.value) || 1, fill_factor: Number(fillF.value) || 1 }), "btn primary"),
      cor ? button("Volumes CSV", () => window.open(ws.volumesCsvUrl(), "_blank"), "btn small") : null),
    !ws.data.vertical?.pvis?.length ? el("p", { class: "error" }, "The corridor needs a vertical alignment (Profile stage).") : null));
  if (!cor) { host.appendChild(el("p", { class: "muted" }, "No corridor built yet.")); return; }
  const t = cor.summary.totals;
  host.appendChild(el("div", { class: "card" }, el("h4", {}, "Quantities"),
    el("dl", { class: "mini-kv" }, el("dt", {}, "sections"), el("dd", {}, `${cor.summary.sections} (${fmtChainage(cor.summary.start)} – ${fmtChainage(cor.summary.end)})`),
      el("dt", {}, "cut"), el("dd", {}, `${fmt(t.cut, 1)} m³`), el("dt", {}, "fill"), el("dd", {}, `${fmt(t.fill, 1)} m³`), el("dt", {}, "net (cut − fill)"), el("dd", {}, `${fmt(t.net, 1)} m³`),
      el("dt", {}, "max surplus / deficit"), el("dd", {}, `${fmt(t.max_surplus, 0)} / ${fmt(t.max_deficit, 0)} m³`),
      el("dt", {}, "balance points"), el("dd", {}, t.balance_points?.length ? t.balance_points.map((c: number) => fmtChainage(c)).join(", ") : "none")),
    cor.summary.flags?.length ? el("p", { class: "hint" }, "Flags: ", ...cor.summary.flags.map((f: string) => el("span", { class: "badge warn" }, f.replace(/_/g, " ")))) : null,
    el("div", { class: "masshaul" })));
  drawMassHaul(host.querySelector(".masshaul")!, cor.mass_haul, (ch) => ws.setStation(ch));
  // sections table with fly-to
  const tbl = el("table", { class: "data" }, el("tr", {}, el("th", {}, "CH"), el("th", {}, "tmpl"), el("th", {}, "design"), el("th", {}, "ground"), el("th", {}, "cut m²"), el("th", {}, "fill m²"), el("th", {}, "L"), el("th", {}, "R")));
  for (const s of cor.sections) {
    const side = (sd: any) => el("td", { class: sd.kind.startsWith("no_") ? "error" : "", title: `hinge ${sd.height >= 0 ? "+" : ""}${num(sd.height)} m` }, sd.kind.replace(/_/g, " ").replace("no catch", "no catch!"));
    tbl.appendChild(el("tr", { class: "clickable", onClick: () => ws.setStation(s.chainage) }, el("td", { class: "mono" }, fmtChainage(s.chainage)), el("td", {}, s.template_id), el("td", {}, num(s.design_z)), el("td", {}, num(s.ground_z)),
      el("td", {}, num(s.cut_area)), el("td", {}, num(s.fill_area)), side(s.left), side(s.right)));
  }
  host.appendChild(el("details", { class: "card" }, el("summary", {}, el("b", {}, `Sections (${cor.sections.length}) - click a row to show it`)), el("div", { style: "max-height:320px;overflow:auto" }, tbl)));
}

function drawMassHaul(host: HTMLElement, mh: any[], onClick: (ch: number) => void): void {
  host.innerHTML = "";
  if (!mh?.length) return;
  const width = Math.max(260, host.clientWidth || 300), height = 150, m = { top: 8, right: 10, bottom: 24, left: 52 };
  const w = width - m.left - m.right, h = height - m.top - m.bottom;
  const svg = d3.select(host).append("svg").attr("width", width).attr("height", height).attr("class", "chart");
  const x = d3.scaleLinear().domain(d3.extent(mh, (d: any) => d.chainage) as [number, number]).range([0, w]);
  const ext = d3.extent(mh, (d: any) => d.cumulative) as [number, number];
  const y = d3.scaleLinear().domain([Math.min(ext[0], 0), Math.max(ext[1], 0)]).nice().range([h, 0]);
  const g = svg.append("g").attr("transform", `translate(${m.left},${m.top})`);
  g.append("g").attr("class", "axis").attr("transform", `translate(0,${h})`).call(d3.axisBottom(x).ticks(5).tickFormat((d) => fmtChainage(Number(d), 0)));
  g.append("g").attr("class", "axis").call(d3.axisLeft(y).ticks(4).tickFormat((d) => `${Number(d) >= 1000 || Number(d) <= -1000 ? (Number(d) / 1000).toFixed(1) + "k" : d}`));
  g.append("line").attr("class", "marker").attr("x1", 0).attr("x2", w).attr("y1", y(0)).attr("y2", y(0));
  g.append("path").datum(mh).attr("class", "design-line").attr("d", d3.line<any>().x((d) => x(d.chainage)).y((d) => y(d.cumulative)));
  g.append("text").attr("class", "axis-title").attr("x", w / 2).attr("y", h + 22).attr("text-anchor", "middle").text("Mass haul (m³ cumulative, cut − fill)");
  svg.append("rect").attr("x", m.left).attr("y", m.top).attr("width", w).attr("height", h).attr("fill", "transparent").style("cursor", "pointer")
    .on("click", (ev) => { const [px] = d3.pointer(ev); onClick(x.invert(px - m.left)); });
}

// ---------------------------------------------------------------- 5 / 6. structures and drainage
export function renderStructuresStage(host: HTMLElement, ws: RoadWorkspace, drainage: boolean): void {
  const data = ws.data.structures || {};
  const cat = data.catalogue || {};
  const kinds: Record<string, any> = cat.kinds || data.kinds || {};   // catalogue kinds carry the type labels
  const list: any[] = ws.editStructures;
  const inGroup = (k: string) => (kinds[k]?.group === "wall") !== drainage;
  const shortLabel = (k: string) => String(kinds[k]?.label || k).split(" (")[0];
  const kindOptions = Object.entries(kinds).filter(([k]) => inGroup(k)).map(([k, v]: [string, any]) => ({ value: k, label: shortLabel(k), title: v.note || v.label }));
  const typeTable = (kind: string) => (kinds[kind]?.group === "wall" ? cat.wall_types : kinds[kind]?.group === "cross" ? cat.culvert_types : cat.drain_types) || {};
  const typeNote = (kind: string, t: string) => {
    const spec = typeTable(kind)[t];
    if (!spec) return "";
    const range = spec.max_height ? ` Usual height up to ${spec.max_height} m.` : spec.max_span ? ` Usual span ${spec.min_span ?? 0}–${spec.max_span} m.` : "";
    return `${spec.note || ""}${range}`;
  };
  // per-group editable parameters
  const fieldsFor = (s: any): { key: string; label: string; step: string }[] => {
    const g = kinds[s.kind]?.group;
    if (g === "wall") return [{ key: "height", label: "H m", step: "0.1" }, { key: "foundation_depth", label: "fdn m", step: "0.1" }];
    if (g === "cross") return [{ key: "span", label: "span m", step: "0.1" }, { key: "cells", label: "cells", step: "1" }];
    const base = [{ key: "width", label: "w m", step: "0.05" }, { key: "depth", label: "d m", step: "0.05" }];
    return s.kind === "catch_drain" ? [...base, { key: "offset", label: "offset m", step: "0.5" }] : base;
  };

  // -------------------------------------------------- suggestions
  const mf = numberInput(6, { min: "0.5", step: "0.5", style: "width:80px" });
  const mc = numberInput(8, { min: "0.5", step: "0.5", style: "width:80px" });
  const soil = select([{ value: "clayey", label: "clayey soil" }, { value: "sandy", label: "sandy soil" }], data.soil || "clayey", { style: "width:120px" });
  const withDrains = el("input", { type: "checkbox", checked: true });
  const sugBox = el("div");
  host.appendChild(el("div", { class: "card" }, el("h4", {}, drainage ? "Drainage: culverts and drains" : "Structures: retaining and breast walls"),
    el("p", { class: "hint" }, drainage
      ? "Culverts and cross drains are suggested where stream lines in the terrain cross the alignment, at sag low points and wherever side-drain outlets would be more than 500 m apart (NRS 2070 cl. 13.8 i). Longitudinal drains follow the runs of cut and low fill; the lining type comes from Table 13-3 for the grade at that chainage. The ditch used for earthworks belongs to the template."
      : "Walls are suggested from the latest corridor: retaining walls where the fill height exceeds the limit or the fill slope does not catch the ground, breast walls for deep cuts. The type is the lightest in the catalogue that suits the height."),
    drainage
      ? el("div", { class: "row", style: "align-items:flex-end" }, field("Drain soil", soil, "NRS 2070 Table 13-3 lining rule"),
          el("label", { class: "check" }, withDrains, "suggest side, catch and toe drains"))
      : el("div", { class: "row", style: "align-items:flex-end" }, field("Max fill without wall m", mf), field("Max cut without wall m", mc)),
    el("div", { class: "btn-row" }, button("Suggest", async () => {
      try {
        const r = await ws.suggestStructures(drainage
          ? { walls: false, culverts: true, drains: withDrains.checked, soil: soil.value }
          : { walls: true, culverts: false, drains: false, max_fill_height: Number(mf.value), max_cut_depth: Number(mc.value) });
        sugBox.innerHTML = "";
        if (!r.length) { sugBox.appendChild(el("p", { class: "muted" }, drainage ? "Nothing to suggest: no stream crossings, sag points or runs of cut found." : "No wall needed at the current limits (build the corridor first if you have not).")); return; }
        const tbl = el("table", { class: "data compact" }, el("tr", {}, el("th", {}, "kind"), el("th", {}, "type"), el("th", {}, "side"), el("th", {}, "from – to"), el("th")));
        for (const s of r) tbl.appendChild(el("tr", { title: s.note || "" }, el("td", {}, shortLabel(s.kind)),
          el("td", {}, typeTable(s.kind)[s.params?.type]?.label || s.params?.type || "–"), el("td", {}, (s.side || "–").slice(0, 1).toUpperCase()),
          el("td", { class: "mono" }, s.to > s.from ? `${fmtChainage(s.from)} – ${fmtChainage(s.to)}` : fmtChainage(s.from)),
          el("td", {}, button("Add", () => { list.push({ ...s }); ws.markDirty("structures"); ws.renderStage(); }, "btn small primary"))));
        sugBox.append(tbl, el("div", { class: "btn-row" }, button("Add all", () => { for (const s of r) list.push({ ...s }); ws.markDirty("structures"); ws.renderStage(); }, "btn small")));
      } catch (e) { err(e); }
    }, "btn primary")),
    sugBox));

  // -------------------------------------------------- table of stored / edited structures
  const shown = list.filter((s) => inGroup(s.kind));
  const tbl = el("table", { class: "data compact" }, el("tr", {}, el("th", {}, "kind"), el("th", {}, "type"), el("th", {}, "side"), el("th", {}, "from"), el("th", {}, "to"), el("th", {}, "parameters"), el("th")));
  for (const s of shown) {
    const idx = list.indexOf(s);
    const kindSel = select(kindOptions, s.kind, { class: "mini" });
    const typeSel = select((kinds[s.kind]?.types || []).map((t: string) => ({ value: t, label: kinds[s.kind]?.type_labels?.[t] || t })), s.params?.type || kinds[s.kind]?.default_type, { class: "mini wide", title: typeNote(s.kind, s.params?.type) });
    kindSel.addEventListener("change", () => {
      s.kind = kindSel.value;
      s.params = { ...(kinds[s.kind]?.params || {}), ...(s.params || {}), type: kinds[s.kind]?.default_type };
      if (!kinds[s.kind]?.sided) s.side = null; else if (!s.side) s.side = "left";
      ws.markDirty("structures"); ws.renderStage();
    });
    typeSel.addEventListener("change", () => { s.params = { ...(s.params || {}), type: typeSel.value }; ws.markDirty("structures"); ws.renderStage(); });
    const sideSel = select([{ value: "left", label: "left" }, { value: "right", label: "right" }, { value: "both", label: "both" }], s.side || "left", { class: "mini" });
    sideSel.addEventListener("change", () => { s.side = sideSel.value; ws.markDirty("structures"); });
    const params = el("div", { class: "param-cells" });
    for (const f of fieldsFor(s)) {
      const inp = numCell(s.params?.[f.key] ?? null, (x) => { s.params = { ...(s.params || {}), [f.key]: x }; ws.markDirty("structures"); }, f.step, "num tiny");
      params.append(el("label", { class: "param-cell" }, el("span", { class: "muted" }, f.label), inp));
    }
    tbl.appendChild(el("tr", { class: "clickable", title: `${s.source === "suggested" ? "suggested: " : ""}${s.note || ""}\n${typeNote(s.kind, s.params?.type)}`, onClick: (ev: Event) => { if ((ev.target as HTMLElement).tagName === "TD") ws.setStation(s.from); } },
      el("td", {}, kindSel), el("td", {}, typeSel), el("td", {}, kinds[s.kind]?.sided === false ? el("span", { class: "muted" }, "–") : sideSel),
      el("td", {}, numCell(s.from, (x) => { s.from = x; ws.markDirty("structures"); }, "1")), el("td", {}, numCell(s.to, (x) => { s.to = x; ws.markDirty("structures"); }, "1")),
      el("td", {}, params),
      el("td", {}, button("✕", () => { list.splice(idx, 1); ws.markDirty("structures"); ws.renderStage(); }, "btn small danger"))));
  }
  const addKind = select(kindOptions.map((o) => ({ value: o.value, label: `+ ${o.label}` })), drainage ? "culvert" : "retaining_wall", { class: "mini" });
  host.appendChild(el("div", { class: "card" }, tbl,
    el("div", { class: "btn-row" }, addKind, button("Add", () => {
      const k = addKind.value;
      list.push({ kind: k, side: kinds[k]?.sided ? "left" : null, from: ws.station, to: ws.station + (kinds[k]?.group === "cross" ? 0 : 20), params: { ...(kinds[k]?.params || {}) }, source: "manual" });
      ws.markDirty("structures"); ws.renderStage();
    }, "btn small"),
      button("Save structures", () => void ws.saveStructures(), "btn primary"), button("Revert", () => ws.revertStructures(), "btn")),
    shown.length ? el("p", { class: "hint" }, typeNote(shown[shown.length - 1].kind, shown[shown.length - 1].params?.type)) : null,
    el("p", { class: "hint" }, "Click a row's chainage to show that station. Structures are drawn on the plan, on the cross-sections and in the exports.")));

  // -------------------------------------------------- quantities and checks
  const q = data.quantities;
  const mats = Object.entries(q?.totals || {}).filter(([, v]) => Number(v) > 0);
  if (mats.length) {
    const rows = (q.rows || []).filter((r: any) => inGroup(r.kind));
    host.appendChild(el("div", { class: "card" }, el("h4", {}, "Quantities ", el("span", { class: "badge" }, `${rows.length} item(s)`)),
      el("dl", { class: "mini-kv" }, ...mats.flatMap(([k, v]) => [el("dt", {}, q.materials?.[k]?.label || k), el("dd", { class: "mono" }, `${fmt(Number(v), 1)} ${q.materials?.[k]?.unit || ""}`)])),
      el("p", { class: "hint" }, "Totals over all structures of this design (both sides counted). Per-structure quantities are on the Structure quantities sheet of the Excel export.")));
  }
  const checks = (data.checks || []).filter((c: any) => !c.where || shown.some((s: any) => String(c.where).includes(fmtChainage(s.from))) || true);
  host.appendChild(el("div", { class: "card" }, el("h4", {}, "Checks ", checks.length ? checkSummary(checks) : null),
    renderChecks(checks, "Save the structures to run the drainage and wall checks")));
}

// ---------------------------------------------------------------- 7. output: drawing sheets and data exports
const PAPERS = ["A4", "A3", "A2", "A1", "A0"];
const TITLE_FIELDS: [string, string][] = [["organisation", "Organisation"], ["project", "Project"], ["road", "Road / design"], ["drawing_prefix", "Drawing no. prefix"],
  ["drawn_by", "Drawn"], ["checked_by", "Checked"], ["approved_by", "Approved"], ["date", "Date"], ["revision", "Revision"]];

export function renderOutputStage(host: HTMLElement, ws: RoadWorkspace): void {
  const h = ws.data.horizontal, v = ws.data.vertical, cor = ws.data.corridor;
  const guest = !!store.get("user")?.guest;
  const dl = (name: string, text: string) => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" })); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000); };
  const gated = (label: string, url: () => string, cls = "btn") => {
    const b = button(label, () => download(url()), cls);
    if (guest) { b.disabled = true; b.title = "Sign in to download exports (previews stay free)"; }
    return b;
  };
  const sh = ws.data.sheets;
  if (!sh) {
    void ws.loadSheets();
    host.appendChild(el("div", { class: "card" }, el("h4", {}, "Drawing sheets"), el("p", { class: "muted" }, "Laying out the sheets…")));
  } else {
    const st = sh.settings || {};
    const fields: Record<string, string> = { ...(st.fields || {}) };
    const paper = select(PAPERS.map((p) => ({ value: p, label: `${p} (${(sh.papers?.[p] || []).join(" × ")} mm)` })), st.paper || "A3");
    const plan = numberInput(st.plan_scale ?? 1000, { min: "100", step: "50" });
    const ph = numberInput(st.profile_h_scale ?? 1000, { min: "100", step: "50" });
    const pv = numberInput(st.profile_v_scale ?? 100, { min: "10", step: "10" });
    const sec = numberInput(st.section_scale ?? 200, { min: "50", step: "50" });
    const chi = numberInput(st.chainage_interval ?? 20, { min: "5", step: "5" });
    const rot = select([{ value: "along_road", label: "rotate along the road" }, { value: "north_up", label: "north up" }], st.rotate || "along_road");
    const inputs: Record<string, HTMLInputElement> = {};
    const fieldRows = TITLE_FIELDS.map(([k, label]) => { const i = el("input", { type: "text", value: fields[k] ?? "" }); inputs[k] = i; return field(label, i); });
    const save = () => void ws.saveSheetSettings({
      paper: paper.value, plan_scale: Number(plan.value), profile_h_scale: Number(ph.value), profile_v_scale: Number(pv.value), section_scale: Number(sec.value),
      chainage_interval: Number(chi.value), rotate: rot.value, fields: Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, i.value])),
    });
    const sheets: any[] = sh.sheets || [];
    const counts = sh.counts || {};
    const groups = el("div", { class: "sheet-groups" });
    for (const kind of ["plan", "profile", "sections"]) {
      const items = sheets.filter((s) => s.kind === kind);
      const row = el("div", { class: "sheet-row" }, el("span", { class: "sheet-kind" }, `${kindLabel(kind)} `, el("span", { class: "badge" }, String(counts[kind] ?? items.length))));
      if (!items.length) row.appendChild(el("span", { class: "muted" }, kind === "sections" ? "build the corridor first" : kind === "profile" ? "needs ground or a vertical alignment" : "needs an alignment"));
      for (const s of items) {
        row.appendChild(el("button", { class: "chip", title: s.title, onClick: () => openSheetPreview(ws.pid, ws.did, sheets, sheets.indexOf(s), ws.design.name) },
          s.number || String(s.index + 1)));
      }
      groups.appendChild(row);
    }
    host.appendChild(el("div", { class: "card" }, el("h4", {}, "Drawing sheets ", el("span", { class: "badge" }, `${sheets.length} sheets`)),
      el("p", { class: "hint" }, "Plan, longitudinal section and cross-sections on the standard sheet template (frame, notes, curve data, title block). Preview shows exactly what the DXF contains."),
      el("div", { class: "grid2" }, field("Paper", paper), field("Plan 1:", plan), field("Profile H 1:", ph), field("Profile V 1:", pv), field("Sections 1:", sec), field("Chainage interval (m)", chi)),
      field("Plan orientation", rot),
      el("details", {}, el("summary", {}, "Title block"), el("div", { class: "grid2" }, ...fieldRows)),
      el("div", { class: "btn-row" }, button("Save sheet settings", save, "btn primary"), button("Refresh", () => void ws.loadSheets(true), "btn"),
        button("Help", () => window.open("/help/road.html#output", "_blank"), "btn small")),
      groups,
      sh.problems?.length ? el("ul", { class: "hint" }, ...sh.problems.map((p: string) => el("li", {}, p))) : null,
      el("div", { class: "btn-row" },
        button("Preview drawings", () => openSheetPreview(ws.pid, ws.did, sheets, 0, ws.design.name), "btn primary"),
        gated("DXF: all sheets", () => api.road.sheetsDxfUrl(ws.pid, ws.did)),
        gated("DXF: plan", () => api.road.sheetsDxfUrl(ws.pid, ws.did, "plan"), "btn small"),
        gated("DXF: profile", () => api.road.sheetsDxfUrl(ws.pid, ws.did, "profile"), "btn small"),
        gated("DXF: sections", () => api.road.sheetsDxfUrl(ws.pid, ws.did, "sections"), "btn small"),
        gated("Model DXF (project coordinates)", () => api.road.modelDxfUrl(ws.pid, ws.did), "btn small")),
      guest ? el("p", { class: "hint" }, "Guests can preview every sheet; downloads need an account.") : null));
  }
  host.appendChild(el("div", { class: "card" }, el("h4", {}, "Data exports"),
    el("div", { class: "btn-row" },
      gated("Excel workbook (.xlsx)", () => api.road.xlsxUrl(ws.pid, ws.did), "btn primary"),
      cor ? gated("Volumes CSV", () => ws.volumesCsvUrl(), "btn") : null,
      button("IP / curve table CSV", () => dl(`${ws.design.name}_alignment.csv`, ["IP,X,Y,R,Ls,deflection_deg,T,Lc,E,e_pct,TS,SC,CS,ST",
        ...(h?.table || []).map((r: any) => [r.label, r.x, r.y, r.radius, r.transition, num(r.deflection_deg, 4), num(r.tangent, 3), num(r.arc_length, 3), num(r.external, 3), r.superelevation_pct ?? "", r.ts ?? "", r.sc ?? "", r.cs ?? "", r.st ?? ""].join(","))].join(String.fromCharCode(10))), "btn small"),
      button("PVI table CSV", () => dl(`${ws.design.name}_profile.csv`, ["PVI,chainage,RL,L,grade_in,grade_out,A,K,kind,BVC,EVC",
        ...(v?.table || []).map((r: any) => [r.index, r.chainage, r.elevation, r.length, r.grade_in ?? "", r.grade_out ?? "", r.A ?? "", r.K ?? "", r.kind ?? "", r.bvc ?? "", r.evc ?? ""].join(","))].join(String.fromCharCode(10))), "btn small"),
      button("Design JSON", () => dl(`${ws.design.name}_design.json`, JSON.stringify({ design: ws.design, horizontal: h?.ips, start_chainage: h?.start_chainage, vertical: v?.pvis, templates: ws.data.templates, structures: ws.data.structures?.structures }, null, 1)), "btn small")),
    el("p", { class: "hint" }, "The workbook holds horizontal, superelevation, vertical, levels, sections, volumes (with live cut / fill factors), mass haul, section points, structures, checks and the standard's parameters.")));
  host.appendChild(el("div", { class: "card" }, el("h4", {}, "Design status"),
    el("dl", { class: "mini-kv" }, ...Object.entries(ws.data.overview?.stages || {}).flatMap(([k, s]: [string, any]) => [el("dt", {}, k), el("dd", {}, el("span", { class: `badge ${s.status === "done" ? "ok" : s.status === "stale" ? "warn" : ""}` }, s.status), " ", s.detail)]))));
}
