import "cesium/Build/Cesium/Widgets/widgets.css";
import "./styles.css";
import { api, ApiError, type Project } from "./api";
import { store } from "./state";
import { el } from "./ui/dom";
import { renderLogin } from "./ui/login";
import { renderProjects } from "./ui/projects";
import { Workspace } from "./ui/workspace";

const root = document.getElementById("app")!;
let workspace: Workspace | null = null;

// toasts
const toasts = el("div", { class: "toasts" });
document.body.appendChild(toasts);
store.subscribe("toast", ({ text, kind }: { text: string; kind: string }) => {
  const t = el("div", { class: `toast ${kind}` }, text);
  toasts.appendChild(t);
  setTimeout(() => t.remove(), kind === "error" ? 8000 : 4000);
});

function projectIdFromHash(): string | null {
  const m = location.hash.match(/^#\/p\/([a-f0-9]+)/);
  return m ? m[1] : null;
}

async function openProject(p: Project): Promise<void> {
  location.hash = `#/p/${p.id}`;
  workspace?.destroy();
  workspace = new Workspace(root, p, () => { workspace?.destroy(); workspace = null; location.hash = ""; void showProjects(); });
  (window as any).__plm = workspace; // debugging handle
  await workspace.init();
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
  const pid = projectIdFromHash();
  if (pid) {
    try {
      await openProject(await api.projects.get(pid));
      return;
    } catch { /* fall through */ }
  }
  await showProjects();
}

window.addEventListener("hashchange", () => {
  const pid = projectIdFromHash();
  if (!pid && workspace) { workspace.destroy(); workspace = null; void showProjects(); return; }
  if (pid && (!workspace || workspace.project.id !== pid)) {
    api.projects.get(pid).then((p) => openProject(p)).catch(() => void showProjects());
  }
});

boot().catch((e) => {
  root.innerHTML = "";
  root.appendChild(el("div", { class: "overlay" }, el("div", { class: "dialog" }, el("h2", {}, "Cannot reach the PLM server"), el("p", { class: "error" }, String(e)))));
});
