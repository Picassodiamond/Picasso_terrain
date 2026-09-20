import "cesium/Build/Cesium/Widgets/widgets.css";
import "./styles.css";
import { api, ApiError, type Project } from "./api";
import { store } from "./state";
import { el } from "./ui/dom";
import { renderLogin } from "./ui/login";
import { renderProjects } from "./ui/projects";
import { Workspace } from "./ui/workspace";
import { moduleById, terrainHash, type ModuleInstance } from "./modules/registry";

const root = document.getElementById("app")!;
/** what is open: the terrain workspace of a project, or a design module workspace */
let current: { kind: "terrain"; ws: Workspace; pid: string } | { kind: "design"; inst: ModuleInstance; pid: string; designId: number } | null = null;

// toasts
const toasts = el("div", { class: "toasts" });
document.body.appendChild(toasts);
store.subscribe("toast", ({ text, kind }: { text: string; kind: string }) => {
  const t = el("div", { class: `toast ${kind}` }, text);
  toasts.appendChild(t);
  setTimeout(() => t.remove(), kind === "error" ? 8000 : 4000);
});

interface Route { pid: string; module?: string; designId?: number }

/** #/p/{project}            terrain workspace
 *  #/p/{project}/{module}/{designId}   design workspace (road, ...) */
function routeFromHash(): Route | null {
  const m = location.hash.match(/^#\/p\/([a-f0-9]+)(?:\/([a-z]+)\/(\d+))?/);
  return m ? { pid: m[1], module: m[2] || undefined, designId: m[3] ? Number(m[3]) : undefined } : null;
}

function destroyCurrent(): void {
  if (!current) return;
  if (current.kind === "terrain") current.ws.destroy(); else current.inst.destroy();
  current = null;
}

async function openRoute(r: Route): Promise<void> {
  if (current && current.pid === r.pid) {
    if (current.kind === "terrain" && !r.module) return;
    if (current.kind === "design" && r.module && current.designId === r.designId) return;
  }
  destroyCurrent();
  const p = await api.projects.get(r.pid);
  if (r.module && r.designId !== undefined) {
    const man = moduleById(r.module);
    if (!man?.open) {
      store.emit("toast", { text: `The ${man?.label ?? r.module} module is not available yet`, kind: "error" });
      location.hash = terrainHash(r.pid);
      return;
    }
    const design = await api.designs.get(r.pid, r.designId);
    const inst = await man.open({ root, project: p, design });
    current = { kind: "design", inst, pid: r.pid, designId: r.designId };
    (window as any).__plm = inst;
    return;
  }
  const ws = new Workspace(root, p, () => { destroyCurrent(); location.hash = ""; void showProjects(); });
  current = { kind: "terrain", ws, pid: r.pid };
  (window as any).__plm = ws; // debugging handle
  await ws.init();
}

async function openProject(p: Project): Promise<void> {
  if (location.hash !== terrainHash(p.id)) location.hash = terrainHash(p.id);
  await openRoute({ pid: p.id });
}

async function showProjects(): Promise<void> {
  await renderProjects(root, (p) => void openProject(p));
}

async function boot(): Promise<void> {
  const status = await api.auth.status();
  store.set("authEnabled", status.auth_enabled);
  try {
    store.set("user", await api.auth.me());
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      renderLogin(root, status, () => void boot());
      return;
    }
    throw e;
  }
  const r = routeFromHash();
  if (r) {
    try {
      await openRoute(r);
      return;
    } catch { /* fall through */ }
  }
  await showProjects();
}

window.addEventListener("hashchange", () => {
  const r = routeFromHash();
  if (!r) { if (current) { destroyCurrent(); void showProjects(); } return; }
  openRoute(r).catch((e) => { store.emit("toast", { text: e instanceof ApiError ? e.detail : String(e), kind: "error" }); destroyCurrent(); void showProjects(); });
});

boot().catch((e) => {
  root.innerHTML = "";
  root.appendChild(el("div", { class: "overlay" }, el("div", { class: "dialog" }, el("h2", {}, "Cannot reach the PLM server"), el("p", { class: "error" }, String(e)))));
});
