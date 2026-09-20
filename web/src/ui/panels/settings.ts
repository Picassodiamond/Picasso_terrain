import { api, ApiError } from "../../api";
import { store, toast } from "../../state";
import { button, el, field, select } from "../dom";
import type { Workspace } from "../workspace";

export function renderSettingsPanel(ws: Workspace, host: HTMLElement): void {
  const p = ws.project;
  const name = el("input", { type: "text", value: p.name });
  const desc = el("input", { type: "text", value: p.description || "" });
  const isOwner = p.my_role === "owner" || !store.get("authEnabled");
  const tags = el("input", { type: "text", placeholder: "district, road name, year (comma separated)", value: ((p.settings as any)?.tags || []).join(", ") });
  const crsSel = select([], p.crs);
  const custom = el("input", { type: "text", placeholder: "EPSG:32645 or +proj=…", value: "" });
  const crsInfo = el("div", { class: "muted" });
  void api.crs.presets().then((ps) => {
    crsSel.innerHTML = "";
    for (const c of ps) crsSel.appendChild(el("option", { value: c.key, selected: c.key === p.crs }, c.name));
    crsSel.appendChild(el("option", { value: "__custom", selected: !ps.some((c) => c.key === p.crs) }, "Custom (EPSG / PROJ string)"));
    if (!ps.some((c) => c.key === p.crs)) custom.value = p.crs;
  });
  crsSel.addEventListener("change", () => (custom.parentElement as HTMLElement).style.display = crsSel.value === "__custom" ? "" : "none");
  crsInfo.textContent = p.crs_info?.is_local ? "Local grid: coordinates are plain metres, exactly like the legacy program. Map overlay disabled." : `${p.crs_info?.name}${p.crs_info?.epsg ? " (EPSG:" + p.crs_info.epsg + ")" : ""}`;

  host.append(el("h3", {}, "Project settings"), el("div", { class: "card" },
    field("Name", name), field("Description", desc), field("Tags", tags, "used by the Library to find this project's terrain models and designs"),
    field("Coordinate reference system", crsSel, "Changing the CRS re-labels the coordinates; it does not transform them. Reload after changing."),
    field("Custom CRS", custom),
    crsInfo,
    el("div", { class: "btn-row" }, button("Save", async () => {
      const spec = crsSel.value === "__custom" ? custom.value.trim() : crsSel.value;
      try {
        await api.projects.update(p.id, { name: name.value, description: desc.value, crs: spec,
          settings: { tags: tags.value.split(",").map((t) => t.trim()).filter(Boolean) } });
        toast("Saved. Reloading…", "ok");
        setTimeout(() => location.reload(), 600);
      } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
    }, "btn primary")),
  ));

  // archive / restore (owner)
  if (isOwner && store.get("authEnabled") && !store.get("user")?.guest) {
    const archived = p.status === "archived";
    host.append(el("h3", {}, "Archive"), el("div", { class: "card" },
      el("p", { class: "hint" }, archived
        ? "This project is archived: everyone with access can read it, nobody can change it. Restore it to continue working."
        : "Archiving writes a portable bundle (GeoPackage + metadata) to the server's archive store and makes the project read-only. Its terrain models and designs stay in the Library."),
      el("div", { class: "btn-row" },
        archived
          ? button("Restore project", async () => { try { await api.projects.restore(p.id); toast("Project restored. Reloading…", "ok"); setTimeout(() => location.reload(), 500); } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); } }, "btn primary")
          : button("Archive project", async () => { if (!confirm(`Archive "${p.name}"? It becomes read-only until restored.`)) return; try { await api.projects.archive(p.id); toast("Project archived. Reloading…", "ok"); setTimeout(() => location.reload(), 500); } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); } }, "btn"),
        button("Download archive (.zip)", () => { window.open(api.projects.archiveUrl(p.id), "_blank"); }, "btn"))));
  }

  // place a local grid on the map (approximate georeferencing by one known point)
  if (p.crs_info?.is_local) {
    const anchor = (p.settings as any)?.anchor || {};
    const b = p.summary?.bounds;
    const lon = el("input", { type: "number", step: "0.000001", value: anchor.lon ?? "", placeholder: "85.324" });
    const lat = el("input", { type: "number", step: "0.000001", value: anchor.lat ?? "", placeholder: "27.717" });
    const ax = el("input", { type: "number", step: "0.001", value: anchor.x ?? (b ? ((b[0] + b[2]) / 2).toFixed(3) : "") });
    const ay = el("input", { type: "number", step: "0.001", value: anchor.y ?? (b ? ((b[1] + b[3]) / 2).toFixed(3) : "") });
    host.append(el("h3", {}, "Place the local grid on the map"), el("div", { class: "card" },
      el("p", { class: "hint" }, "Legacy surveys in plain metres have no position on the Earth, so base maps are disabled. If you know the longitude/latitude of one survey point (from a GPS or Google Maps), enter it here together with that point's local Easting/Northing. Grid north is assumed to point to true north; placement accuracy is a few metres, good enough for orientation on OpenStreetMap."),
      el("div", { class: "row" }, field("Longitude (°E)", lon), field("Latitude (°N)", lat)),
      el("div", { class: "row" }, field("Local Easting of that point", ax), field("Local Northing of that point", ay)),
      el("div", { class: "btn-row" },
        button("Place on map", async () => {
          if (!lon.value || !lat.value) { toast("Enter longitude and latitude", "error"); return; }
          try {
            await api.projects.update(p.id, { settings: { anchor: { lon: Number(lon.value), lat: Number(lat.value), x: Number(ax.value), y: Number(ay.value) } } });
            toast("Grid placed. Reloading…", "ok");
            setTimeout(() => location.reload(), 600);
          } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
        }, "btn primary"),
        anchor.lon != null ? button("Remove placement", async () => { await api.projects.update(p.id, { settings: { anchor: null } }); location.reload(); }) : null,
      ),
    ));
  }

  host.append(el("h3", {}, "Nepal coordinate systems"), el("div", { class: "card" }, el("p", { class: "hint" },
    "MUTM 81 / 84 / 87 = Modified UTM on the Everest 1830 spheroid with k = 0.9999 (Survey Department of Nepal). The datum shift to WGS84 built into PLM is the commonly published approximation; confirm it against a known control point before relying on sub-metre map alignment. UTM 44N / 45N are WGS84 zones.")));

  host.append(el("h3", {}, "Danger zone"), el("div", { class: "card" },
    el("div", { class: "btn-row" }, button("Delete project", async () => {
      if (prompt(`Type the project name (${p.name}) to delete it permanently`) !== p.name) return;
      try { await api.projects.delete(p.id); location.hash = ""; location.reload(); } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
    }, "btn danger")),
  ));
  void store;
}
