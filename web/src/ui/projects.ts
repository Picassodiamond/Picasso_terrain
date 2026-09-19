import { api, ApiError, type Project } from "../api";
import { store, toast } from "../state";
import { button, el, field, select } from "./dom";

export async function renderProjects(root: HTMLElement, onOpen: (p: Project) => void): Promise<void> {
  root.innerHTML = "";
  const page = el("div", { class: "projects-page" });
  root.appendChild(page);
  const user = store.get("user");
  const head = el("div", { style: "display:flex;align-items:center;gap:12px;margin-bottom:20px" },
    el("h1", { style: "color:var(--accent)" }, "▲ Picasso LandMesh"),
    el("span", { class: "muted" }, "terrain modelling · contours · alignments · sections"),
    el("span", { style: "flex:1" }),
    user?.authenticated ? el("span", { class: "muted" }, `${user.username} (${user.role}) `) : null,
    user?.authenticated ? button("Sign out", async () => { await api.auth.logout(); location.reload(); }, "btn small") : null,
  );
  page.appendChild(head);

  const presets = await api.crs.presets();
  const name = el("input", { type: "text", placeholder: "e.g. Bhaktapur bypass survey 2026" });
  const crs = select(presets.map((p) => ({ value: p.key, label: p.name })), "local");
  const custom = el("input", { type: "text", placeholder: "EPSG:32645 or +proj=... (used when 'Custom' is chosen)" });
  const crsWrap = el("div", { class: "row" }, field("Coordinate system", crs, presets.find((p) => p.key === "local")?.description), field("Custom CRS", custom));
  crs.appendChild(el("option", { value: "__custom" }, "Custom (EPSG / PROJ string)"));
  const desc = el("input", { type: "text", placeholder: "description (optional)" });
  const err = el("div", { class: "error" });
  const create = button("Create project", async () => {
    err.textContent = "";
    const spec = crs.value === "__custom" ? custom.value.trim() : crs.value;
    if (!name.value.trim()) { err.textContent = "Give the project a name"; return; }
    try {
      const p = await api.projects.create({ name: name.value.trim(), crs: spec || "local", description: desc.value });
      toast(`Project "${p.name}" created`, "ok");
      onOpen(p);
    } catch (e) {
      err.textContent = e instanceof ApiError ? e.detail : String(e);
    }
  }, "btn primary");
  page.appendChild(el("div", { class: "card" },
    el("h3", {}, "New project"),
    el("div", { class: "row" }, field("Name", name), field("Description", desc)),
    crsWrap,
    el("p", { class: "hint" }, "Local grid =  Choose an MUTM/UTM zone to overlay OpenStreetMap."),
    err,
    el("div", { class: "btn-row" }, create),
  ));

  const list = el("div", { class: "project-grid" });
  page.appendChild(el("h3", { style: "margin-top:20px" }, "Projects"));
  page.appendChild(list);
  const projects = await api.projects.list();
  if (!projects.length) list.appendChild(el("p", { class: "muted" }, "No projects yet - create one above, then import survey points."));
  for (const p of projects) {
    const s = p.summary;
    list.appendChild(el("div", { class: "project-card", onClick: () => onOpen(p) },
      el("h3", {}, p.name),
      el("div", { class: "meta" }, p.crs_info?.name || p.crs),
      el("div", { class: "meta" }, `${s?.points ?? 0} points · ${s?.tin_runs ?? 0} TIN · ${s?.contour_sets ?? 0} contour sets · ${s?.alignments ?? 0} alignments`),
      el("div", { class: "meta" }, `updated ${new Date(p.updated).toLocaleString()}`),
      p.description ? el("div", { class: "meta", style: "margin-top:6px" }, p.description) : null,
    ));
  }
}
