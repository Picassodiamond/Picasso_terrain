import { api } from "../../api";
import { store } from "../../state";
import { button, download, el, field, numberInput } from "../dom";
import type { Workspace } from "../workspace";

export function renderExportPanel(ws: Workspace, host: HTMLElement): void {
  const pid = ws.project.id;
  const sets = store.get("contourSets");
  const als = store.get("alignments");
  const secs = store.get("sectionSets");
  const chk = (label: string, checked = true) => { const c = el("input", { type: "checkbox", checked }); return { c, row: el("label", { class: "check" }, c, label) }; };
  const points = chk("Survey points (POINTS blocks with PTNUM/DESC/ELEV + POINT)");
  const tin = chk("TIN triangles (3DFACE on layer Triangle)", false);
  const features = chk("Feature lines & boundary");
  const contours = chk("Contours (LWPOLYLINE at elevation, Contour / Index_Contour)");
  const labels = chk("Contour labels (TEXT on Cont_Annotation)");
  const arc = chk("Round contours with arcs (legacy look, bulges)", false);
  const setBoxes = sets.map((s) => ({ s, ...chk(`${s.name || "Set " + s.id} (${s.params.interval} m)`, store.get("visibleContourSets").includes(s.id)) }));
  const alBoxes = als.map((a) => ({ a, ...chk(`${a.name}`, a.id === store.get("currentAlignment")) }));
  const secSel = el("select", {}, el("option", { value: "" }, "none"), ...secs.map((s) => el("option", { value: String(s.id), selected: s.id === store.get("currentSectionSet") }, `Section set ${s.id} (${s.summary.sections} sections)`)));
  const chInt = numberInput(20, { min: "1", step: "5" });
  const textH = numberInput(1, { min: "0.1", step: "0.1" });

  const dxfUrl = () => api.exportUrl.dxf(pid, {
    points: points.c.checked, tin: tin.c.checked, features: features.c.checked, boundary: features.c.checked, contours: contours.c.checked, labels: labels.c.checked,
    arc_smoothing: arc.c.checked, contour_sets: setBoxes.filter((b) => b.c.checked).map((b) => b.s.id).join(","),
    alignments: alBoxes.filter((b) => b.c.checked).map((b) => b.a.id).join(","), section_set: secSel.value || undefined, chainage_interval: Number(chInt.value), text_height: Number(textH.value),
  });

  host.append(el("h3", {}, "Export for AutoCAD (DXF)"),
    el("div", { class: "card" },
      points.row, tin.row, features.row, contours.row,
      sets.length ? el("div", { style: "margin-left:22px" }, ...setBoxes.map((b) => b.row)) : null,
      labels.row, arc.row,
      el("h4", { style: "margin-top:8px" }, "Alignments (H_ALIGN with arcs, IP labels, chainage ticks)"),
      als.length ? el("div", {}, ...alBoxes.map((b) => b.row)) : el("p", { class: "muted" }, "none"),
      field("Cross-section lines", secSel),
      el("div", { class: "row" }, field("Chainage labels every (m)", chInt), field("Text height (drawing units)", textH)),
      el("div", { class: "btn-row" }, button("Download DXF", () => download(dxfUrl()), "btn primary")),
      el("p", { class: "hint" }, "Open in AutoCAD with OPEN or insert with the Attach/Insert commands."),
    ),
    el("h3", {}, "Other formats"),
    el("div", { class: "card" },
      el("div", { class: "btn-row" },
        button("GeoPackage (.gpkg)", () => download(api.exportUrl.gpkg(pid))),
        button("GeoJSON", () => download(api.exportUrl.geojson(pid))),
        button("Points CSV", () => download(api.data.pointsCsvUrl(pid))),
      ),
      el("p", { class: "hint" }, "GeoPackage opens directly in QGIS (points, lines, contours, alignments, section lines as layers). Profile.csv / Cross.csv are in the Sections tab."),
    ),
  );
}
