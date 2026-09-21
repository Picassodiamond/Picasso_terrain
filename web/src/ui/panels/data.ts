import { api, ApiError } from "../../api";
import { store, toast } from "../../state";
import { clearSelection, ro, selectElement } from "../selection";
import { button, download, el, field, fmt, numberInput, select } from "../dom";
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
    if (!feats.length) { consEl.appendChild(el("p", { class: "muted" }, "No constraint lines yet. Draw them, import them, or let the TIN build detect the survey limit automatically.")); consEl.appendChild(editEl); return; }
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
      const editing = editor?.fid === p.fid;
      tbl.appendChild(el("tr", { class: editing ? "selected" : "" }, el("td", {}, kindSel),
        el("td", { class: "muted", title: p.source }, p.source === "auto" ? "auto" : p.source === "accepted" ? "reviewed" : p.source === "drawn" ? "drawn" : "import"),
        el("td", {}, fly), el("td", {}, String(coords.length)),
        el("td", {}, el("div", { class: "btn-row" },
          button(editing ? "Editing" : "Edit", () => (editing ? stopEditing() : startEditing(p.fid, p.kind, coords)), editing ? "btn small active" : "btn small"),
          button("✕", async () => { if (editing) stopEditing(); await api.data.deleteLines(pid, { fids: String(p.fid) }); await ws.refreshLines(); renderConstraints(); renderSummary(); }, "btn small danger")))));
    }
    const autoCount = feats.filter((f) => f.properties.source === "auto").length;
    // editEl is re-appended below: renderConstraints() clears consEl, which detaches it
    consEl.append(el("div", { class: "card" }, tbl,
      el("p", { class: "hint" }, "Dashed lines on the map are automatic suggestions in use. Change a kind, delete a line, or draw a replacement; the next TIN build uses exactly this list."),
      autoCount ? el("div", { class: "btn-row" }, button(`Remove ${autoCount} automatic`, async () => { await api.constraints.deleteAuto(pid); await ws.refreshLines(); renderConstraints(); renderSummary(); }, "btn small")) : null));
    consEl.appendChild(editEl);
  }
  // ---------------------------------------------------------------- vertex editor
  /** The line being edited, as a working copy: nothing is written until Save. */
  let editor: { fid: number; kind: string; coords: number[][]; selected: number } | null = null;
  const editEl = el("div");
  consEl.appendChild(editEl);

  const isRing = (kind: string) => kind === "boundary" || kind === "void";

  function drawEditor(): void {
    if (editor) ws.layers.setEditLine(editor.coords, editor.kind, ws.zAt, editor.selected);
  }

  function startEditing(fid: number, kind: string, coords: number[][]): void {
    editor = { fid, kind, coords: coords.map((c) => [c[0], c[1], c[2] ?? 0]), selected: -1 };
    ws.setTool("none");
    ws.interaction.dragEnabled = true;
    ws.toolHandlers = {
      hint: "Drag a vertex to move it, or type exact coordinates in the table.",
      onDragVertex: (i, pt, phase) => {
        const e = editor;
        if (!e?.coords[i]) return;
        e.coords[i][0] = Number(pt.x.toFixed(3));
        e.coords[i][1] = Number(pt.y.toFixed(3));
        // a closed ring repeats its first corner last: move both or the ring tears open
        if (isRing(e.kind) && i === 0) e.coords[e.coords.length - 1] = [...e.coords[0]];
        e.selected = i;
        drawEditor();
        selectVertex(i);
        if (phase === "end") renderEditor();
      },
    };
    drawEditor();
    renderConstraints();
    renderEditor();
  }

  /** The picked vertex, with its plan coordinates as editable properties. */
  function selectVertex(i: number): void {
    const e = editor;
    if (!e?.coords[i]) return;
    const v = e.coords[i];
    const ring = isRing(e.kind);
    selectElement({
      kind: "vertex", id: `${e.fid}:${i}`, label: `Vertex ${i + 1} of line #${e.fid}`,
      subtitle: `${e.kind === "feature" ? "breakline" : e.kind} \· plan geometry, the level comes from the survey`,
      fields: [
        { key: "x", label: "Easting", value: Number(v[0].toFixed(3)), type: "number", unit: "m", step: 0.001 },
        { key: "y", label: "Northing", value: Number(v[1].toFixed(3)), type: "number", unit: "m", step: 0.001 },
      ],
      apply: (vals) => {
        const x = Number(vals.x), y = Number(vals.y);
        if (!Number.isFinite(x) || !Number.isFinite(y)) throw new Error("Easting and Northing must be numbers");
        v[0] = x; v[1] = y;
        if (ring && i === 0) e.coords[e.coords.length - 1] = [...e.coords[0]];
        drawEditor();
        renderEditor();
        selectVertex(i);
        return `Vertex ${i + 1} moved \— Save line to keep it`;
      },
      actions: [{
        label: "Remove", danger: true, run: () => {
          const min = ring ? 4 : 2;
          if (e.coords.length <= min) { toast(ring ? "A boundary or void needs three corners" : "A line needs two vertices", "error"); return; }
          e.coords.splice(i, 1);
          if (ring && i === 0) e.coords[e.coords.length - 1] = [...e.coords[0]];
          e.selected = -1;
          clearSelection();
          drawEditor(); renderEditor();
        },
      }],
    });
  }

  function stopEditing(): void {
    editor = null;
    clearSelection();
    ws.toolHandlers = {};
    ws.interaction.dragEnabled = false;
    ws.layers.setEditLine(null, "", ws.zAt);
    renderConstraints();
    renderEditor();
  }

  function renderEditor(): void {
    editEl.innerHTML = "";
    const e = editor;
    if (!e) return;
    const ring = isRing(e.kind);
    const rows = ring && e.coords.length > 2 ? e.coords.length - 1 : e.coords.length;
    const tbl = el("table", { class: "data" }, el("tr", {}, el("th", {}, "#"), el("th", {}, "Easting"), el("th", {}, "Northing"), el("th")));
    for (let i = 0; i < rows; i++) {
      const v = e.coords[i];
      const num = (val: number, set: (x: number) => void) => {
        const inp = numberInput(Number(val.toFixed(3)), { class: "num" });
        inp.addEventListener("change", () => {
          const x = Number(inp.value);
          if (!Number.isFinite(x)) { inp.value = val.toFixed(3); return; }
          set(x);
          if (ring && i === 0) e.coords[e.coords.length - 1] = [...e.coords[0]];
          drawEditor();
        });
        inp.addEventListener("focus", () => { e.selected = i; drawEditor(); selectVertex(i); });
        return inp;
      };
      tbl.appendChild(el("tr", { class: i === e.selected ? "selected" : "" },
        el("td", { class: "mono" }, String(i + 1)),
        el("td", {}, num(v[0], (x) => (v[0] = x))),
        el("td", {}, num(v[1], (x) => (v[1] = x))),
        el("td", {}, el("div", { class: "btn-row" },
          button("+", () => {
            const nxt = e.coords[i + 1] ?? e.coords[0];
            e.coords.splice(i + 1, 0, [(v[0] + nxt[0]) / 2, (v[1] + nxt[1]) / 2, ((v[2] ?? 0) + (nxt[2] ?? 0)) / 2]);
            e.selected = i + 1;
            drawEditor(); renderEditor();
          }, "btn small"),
          button("✕", () => {
            const min = ring ? 4 : 2;            // a ring also carries its repeated last vertex
            if (e.coords.length <= min) { toast(ring ? "A boundary or void needs three corners" : "A line needs two vertices", "error"); return; }
            e.coords.splice(i, 1);
            if (ring && i === 0) e.coords[e.coords.length - 1] = [...e.coords[0]];
            e.selected = -1;
            drawEditor(); renderEditor();
          }, "btn small danger")))));
    }
    editEl.appendChild(el("div", { class: "card" },
      el("h4", {}, `Vertices of #${e.fid}`),
      el("p", { class: "hint" }, ring
        ? "A boundary or a void encloses an area: the ring is closed for you, so the first corner is also the last."
        : "Drag a vertex on the map, or type the exact Easting and Northing. + inserts a vertex after this one."),
      el("div", { style: "max-height:260px;overflow:auto" }, tbl),
      el("div", { class: "btn-row" },
        button("Save line", async () => {
          try {
            const res = await api.data.editLine(pid, e.fid, { coords: e.coords.map((c) => [c[0], c[1]]) });
            toast(`Line #${e.fid} saved with ${res.line.n_vertices} vertices — rebuild the TIN to use it`, "ok");
            stopEditing();
            await ws.refreshLines();
            renderConstraints();
            renderSummary();
          } catch (err) { toast(err instanceof ApiError ? err.detail : String(err), "error"); }
        }, "btn primary"),
        button("Cancel", () => stopEditing(), "btn")),
      el("p", { class: "hint" }, "Constraint lines are plan geometry: the level of a vertex comes from the survey surface, so there is no RL to type. "
        + "A breakline imported with surveyed levels keeps them on every vertex you do not move."),
      el("p", { class: "hint" }, "The terrain is a snapshot: the TIN runs you already have keep the old line. Build the TIN again to use the edit."),
    ));
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
