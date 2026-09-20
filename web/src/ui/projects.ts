import { api, ApiError, type Project } from "../api";
import { store, toast } from "../state";
import { button, el, field, select } from "./dom";

export async function renderProjects(root: HTMLElement, onOpen: (p: Project) => void): Promise<void> {
  root.innerHTML = "";
  const page = el("div", { class: "projects-page" });
  root.appendChild(page);
  const user = store.get("user");
  const guest = !!user?.guest;
  const head = el("div", { style: "display:flex;align-items:center;gap:12px;margin-bottom:20px;flex-wrap:wrap" },
    el("h1", { style: "color:var(--accent)" }, "▲ Picasso LandMesh"),
    el("span", { class: "muted" }, "terrain modelling · contours · alignments · sections"),
    el("span", { style: "flex:1" }),
    button("Library", () => { location.hash = "#/library"; }, "btn small"),
    button("Help", () => window.open("/help/index.html", "_blank"), "btn small"),
    user?.role === "admin" && user.authenticated ? button("Admin", () => { location.hash = "#/admin"; }, "btn small") : null,
    user?.authenticated ? el("span", { class: "muted" }, `${user.full_name || user.username} (${user.role}) `) : guest ? el("span", { class: "badge warn" }, "guest sandbox") : null,
    user?.authenticated ? button("Sign out", async () => { await api.auth.logout(); location.reload(); }, "btn small") : null,
    guest ? button("Sign in", () => store.emit("auth:login"), "btn small primary") : null,
  );
  page.appendChild(head);
  if (guest) {
    const qta = user?.quota;
    page.appendChild(el("div", { class: "guest-banner" },
      el("b", {}, "You are trying Picasso LandMesh as a guest. "),
      `Sandbox limits: ${qta?.max_points?.toLocaleString() ?? "5,000"} points per project, ${qta?.max_projects ?? 2} projects (${qta?.projects ?? 0} used), no exports, deleted after ${qta?.ttl_days ?? 7} days. `,
      "Accounts are created by the administrator; signing in with one keeps your sandbox projects.",
      el("span", { style: "flex:1" }),
      button("Sign in", () => store.emit("auth:login"), "btn small primary")));
  }

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
  const showArchived = el("input", { type: "checkbox" });
  page.appendChild(el("div", { style: "display:flex;align-items:center;gap:12px;margin-top:20px" }, el("h3", {}, "Projects"), el("span", { style: "flex:1" }),
    el("label", { class: "check" }, showArchived, "show archived")));
  page.appendChild(list);
  const projects = await api.projects.list();
  const renderList = () => {
    list.innerHTML = "";
    const visible = projects.filter((p) => showArchived.checked || p.status !== "archived");
    if (!visible.length) list.appendChild(el("p", { class: "muted" }, projects.length ? "Only archived projects - tick 'show archived'." : "No projects yet - create one above, then import survey points."));
    for (const p of visible) list.appendChild(card(p));
  };
  showArchived.addEventListener("change", renderList);
  const card = (p: Project) => {
    const s = p.summary;
    return el("div", { class: `project-card${p.status === "archived" ? " archived" : ""}`, onClick: () => onOpen(p) },
      el("h3", {}, p.name, p.my_role ? el("span", { class: `badge${p.my_role === "owner" ? " ok" : ""}` }, p.my_role) : null,
        p.status === "archived" ? el("span", { class: "badge warn" }, "archived") : null,
        (p.settings as any)?.visibility === "public" ? el("span", { class: "badge" }, "public") : (p.settings as any)?.visibility === "private" ? el("span", { class: "badge" }, "private") : null),
      el("div", { class: "meta" }, p.crs_info?.name || p.crs),
      el("div", { class: "meta" }, `${s?.points ?? 0} points · ${s?.tin_runs ?? 0} TIN · ${s?.contour_sets ?? 0} contour sets · ${s?.alignments ?? 0} alignments`),
      Object.keys(s?.designs || {}).length ? el("div", { class: "meta" }, Object.entries(s!.designs!).map(([m, n]) => `${n} ${m} design${n > 1 ? "s" : ""}`).join(" · ")) : null,
      el("div", { class: "meta" }, `updated ${new Date(p.updated).toLocaleString()}`),
      p.description ? el("div", { class: "meta", style: "margin-top:6px" }, p.description) : null,
    );
  };
  renderList();
}
