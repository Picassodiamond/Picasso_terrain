import { api, ApiError } from "../../api";
import { store, toast } from "../../state";
import { button, download, el, field, fmt, select } from "../dom";
import type { Workspace } from "../workspace";

export function renderDataPanel(ws: Workspace, host: HTMLElement): void {
  const pid = ws.project.id;
  const file = el("input", { type: "file", accept: ".csv,.txt,.xyz,.pts,.prn,.tsv,.geojson,.json,.dxf,.xlsx" });
  const layer = el("input", { type: "text", placeholder: "Points" });
  const delim = select([{ value: "auto", label: "auto-detect" }, { value: ",", label: "comma" }, { value: "tab", label: "tab" }, { value: ";", label: "semicolon" }, { value: "space", label: "space" }], "auto");
  const header = select([{ value: "auto", label: "detect" }, { value: "true", label: "yes" }, { value: "false", label: "no" }], "auto");
  const mapping = el("input", { type: "text", placeholder: '{"id":0,"x":1,"y":2,"z":3,"remark":4}' });
  const pointLayers = el("input", { type: "text", placeholder: "Points-Blk, Points (DXF)" });
  const featureLayers = el("input", { type: "text", placeholder: "Features (DXF)" });
  const replace = el("input", { type: "checkbox" });
  const result = el("div", { class: "muted", style: "margin-top:6px" });
  const importBtn = button("Import", async () => {
    const f = file.files?.[0];
    if (!f) { toast("Choose a file first", "error"); return; }
    store.set("busy", `Importing ${f.name}`);
    try {
      const r = await api.data.import(pid, f, { layer: layer.value.trim(), delimiter: delim.value, has_header: header.value, mapping: mapping.value.trim(),
        point_layers: pointLayers.value.trim(), feature_layers: featureLayers.value.trim(), replace: replace.checked ? "true" : undefined });
      result.textContent = `Imported ${r.points_added} points` + (Object.keys(r.lines_added).length ? `, lines: ${JSON.stringify(r.lines_added)}` : "") + (r.warnings.length ? ` · ${r.warnings.join("; ")}` : "");
      toast(`Imported ${r.points_added} points`, "ok");
      await ws.refreshPoints();
      await ws.refreshLines();
      ws.zoomToData();
      renderSummary();
    } catch (e) {
      toast(e instanceof ApiError ? e.detail : String(e), "error");
    } finally {
      store.set("busy", null);
    }
  }, "btn primary");

  host.append(
    el("h3", {}, "Import survey data"),
    el("div", { class: "card" },
      field("File", file, "CSV/TXT/XYZ (x y z [remark] or id x y z remark, header optional), GeoJSON (points, feature lines, boundary/void polygons), DXF (POINTS blocks, POINT, polylines by layer), XLSX"),
      el("div", { class: "row" }, field("Layer name", layer, "for the imported points"), field("Delimiter", delim)),
      el("div", { class: "row" }, field("Header row", header), field("Column mapping (JSON)", mapping, "optional, 0-based columns")),
      el("details", {}, el("summary", { class: "muted" }, "DXF options"), el("div", { class: "row" }, field("Point layers", pointLayers), field("Feature-line layers", featureLayers))),
      el("label", { class: "check" }, replace, "Replace existing points and lines"),
      el("div", { class: "btn-row" }, importBtn, button("Download points CSV", () => download(api.data.pointsCsvUrl(pid)), "btn")),
      result,
    ),
  );

  // drawing tools
  const startLine = (kind: "feature" | "boundary" | "void") => {
    const coords: number[][] = [];
    ws.toolHandlers = {
      hint: `Drawing ${kind}: click vertices, double-click to finish, Esc to cancel`,
      onClick: (p) => { coords.push([p.x, p.y, p.z]); ws.layers.setDraftLine(coords); },
      onMove: (p) => { if (p && coords.length) ws.layers.setDraftLine([...coords, [p.x, p.y, p.z]]); },
      onDouble: async () => {
        if (coords.length < 2) { toast("Need at least two vertices", "error"); return; }
        let pts = coords;
        if (kind !== "feature") pts = [...coords, coords[0]];
        // take Z from the TIN when available
        const run = store.get("currentRun");
        if (run !== null && kind === "feature") {
          const zs = await Promise.all(pts.map((c) => api.tin.elevation(pid, run, c[0], c[1]).then((r) => r.z ?? c[2]).catch(() => c[2])));
          pts = pts.map((c, i) => [c[0], c[1], zs[i] ?? 0]);
        }
        try {
          await api.data.addLine(pid, { kind, coords: pts.map((c) => [c[0], c[1], kind === "feature" ? (c[2] ?? 0) : 0]) });
          toast(`${kind} line added`, "ok");
          await ws.refreshLines();
          renderSummary();
        } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
        ws.setTool("none");
      },
    };
    ws.setTool("line");
  };
  host.append(el("h3", {}, "Draw"), el("div", { class: "card" },
    el("p", { class: "hint" }, "Feature (break) lines are honoured by the triangulation; the boundary limits the TIN; voids are excluded. Feature-line Z is taken from the TIN if one exists."),
    el("div", { class: "btn-row" }, button("Breakline", () => startLine("feature")), button("Boundary", () => startLine("boundary")), button("Hole / void", () => startLine("void"))),
  ));

  // constraint lines: review, re-kind, delete (automatic suggestions are stored with source "auto")
  const consEl = el("div");
  host.append(el("h3", {}, "Constraint lines"), consEl);
  async function renderConstraints() {
    consEl.innerHTML = "";
    const fc = await api.data.lines(pid);
    const feats = fc.features as any[];
    if (!feats.length) { consEl.appendChild(el("p", { class: "muted" }, "No constraint lines yet. Draw them, import them, or let the TIN build detect the survey limit automatically.")); return; }
    const tbl = el("table", { class: "data" }, el("tr", {}, el("th", {}, "kind"), el("th", {}, "source"), el("th", {}, "name"), el("th", {}, "pts"), el("th")));
    for (const f of feats) {
      const p = f.properties;
      const coords: number[][] = f.geometry.coordinates;
      const kindSel = select([{ value: "feature", label: "breakline" }, { value: "boundary", label: "boundary" }, { value: "void", label: "hole" }, { value: "contour", label: "contour" }], p.kind, { class: "mini", title: "Change the constraint kind" });
      kindSel.addEventListener("change", async () => {
        try { await api.data.updateLine(pid, p.fid, { kind: kindSel.value }); await ws.refreshLines(); renderConstraints(); }
        catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
      });
      const fly = el("a", { href: "#", title: "Show on map", onClick: (ev: Event) => {
        ev.preventDefault();
        const xs = coords.map((c) => c[0]), ys = coords.map((c) => c[1]);
        store.emit("map:flyTo", [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]);
      } }, p.name ? String(p.name).slice(0, 40) : `#${p.fid}`);
      tbl.appendChild(el("tr", {}, el("td", {}, kindSel), el("td", { class: "muted", title: p.source }, p.source === "auto" ? "auto" : p.source === "accepted" ? "reviewed" : p.source === "drawn" ? "drawn" : "import"),
        el("td", {}, fly), el("td", {}, String(coords.length)),
        el("td", {}, button("✕", async () => { await api.data.deleteLines(pid, { fids: String(p.fid) }); await ws.refreshLines(); renderConstraints(); renderSummary(); }, "btn small danger"))));
    }
    const autoCount = feats.filter((f) => f.properties.source === "auto").length;
    consEl.append(el("div", { class: "card" }, tbl,
      el("p", { class: "hint" }, "Dashed lines on the map are automatic suggestions in use. Change a kind, delete a line, or draw a replacement; the next TIN build uses exactly this list."),
      autoCount ? el("div", { class: "btn-row" }, button(`Remove ${autoCount} automatic`, async () => { await api.constraints.deleteAuto(pid); await ws.refreshLines(); renderConstraints(); renderSummary(); }, "btn small")) : null));
  }
  void renderConstraints();
  store.subscribe("refresh:lines", () => void renderConstraints());

  const summary = el("div");
  host.append(el("h3", {}, "Contents"), summary);
  async function renderSummary() {
    summary.innerHTML = "";
    const [pl, ls] = await Promise.all([api.data.pointLayers(pid), api.data.lineSummary(pid)]);
    const tbl = el("table", { class: "data" }, el("tr", {}, el("th", {}, "Point layer"), el("th", {}, "n"), el("th", {}, "RL min"), el("th", {}, "RL max"), el("th")));
    for (const r of pl) tbl.appendChild(el("tr", {}, el("td", {}, r.layer || "(none)"), el("td", {}, String(r.n)), el("td", {}, fmt(r.zmin, 2)), el("td", {}, fmt(r.zmax, 2)),
      el("td", {}, button("✕", async () => { if (confirm(`Delete ${r.n} points on layer "${r.layer}"?`)) { await api.data.deletePoints(pid, { layer: r.layer }); await ws.refreshPoints(); renderSummary(); } }, "btn small danger"))));
    if (!pl.length) tbl.appendChild(el("tr", {}, el("td", { colspan: "5", class: "muted" }, "no points yet")));
    const tbl2 = el("table", { class: "data" }, el("tr", {}, el("th", {}, "Lines"), el("th", {}, "layer"), el("th", {}, "n"), el("th", {}, "vertices"), el("th")));
    for (const r of ls) tbl2.appendChild(el("tr", {}, el("td", {}, r.kind), el("td", {}, r.layer), el("td", {}, String(r.n)), el("td", {}, String(r.vertices)),
      el("td", {}, button("✕", async () => { if (confirm(`Delete all ${r.kind} lines?`)) { await api.data.deleteLines(pid, { kind: r.kind }); await ws.refreshLines(); renderSummary(); } }, "btn small danger"))));
    if (!ls.length) tbl2.appendChild(el("tr", {}, el("td", { colspan: "5", class: "muted" }, "no lines")));
    summary.append(el("div", { class: "card" }, tbl), el("div", { class: "card" }, tbl2));
  }
  void renderSummary();
}
