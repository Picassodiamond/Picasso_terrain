/** Top-bar module switcher: Terrain | <design tabs> | + New design. Shared by every workspace. */
import type { Design } from "../api";
import { MODULES, designHash, terrainHash } from "../modules/registry";
import { el } from "./dom";

export function renderModuleSwitcher(pid: string, designs: Design[], active: "terrain" | number, onNew: (moduleId: string) => void): HTMLElement {
  const wrap = el("div", { class: "module-switcher", title: "Switch between the terrain workspace and design workspaces of this project" });
  const tab = (label: string, on: boolean, hash: string, title?: string) =>
    el("button", { class: `mtab${on ? " active" : ""}`, title: title || label, onClick: () => { if (!on) location.hash = hash; } }, label);
  wrap.appendChild(tab("▲ Terrain", active === "terrain", terrainHash(pid), "Survey, constraints, TIN, contours, alignments, sections"));
  for (const d of designs) {
    const m = MODULES.find((x) => x.id === d.module);
    wrap.appendChild(tab(`${m?.icon ?? "◇"} ${d.name}`, active === d.id, designHash(pid, d), `${m?.label ?? d.module} · pinned to TIN run ${d.tin_run_id ?? "-"}`));
  }
  const menu = el("div", { class: "menu" });
  for (const m of MODULES) {
    const planned = m.status !== "available";
    menu.appendChild(el("button", { class: "menu-item", disabled: planned ? "true" : undefined, onClick: (ev: Event) => { ev.preventDefault(); (details as HTMLDetailsElement).open = false; if (!planned) onNew(m.id); } },
      el("span", { class: "icon" }, m.icon),
      el("span", {}, el("b", {}, m.label), planned ? el("span", { class: "badge" }, "planned") : null, el("div", { class: "muted", style: "font-size:11px" }, m.description))));
  }
  const details = el("details", { class: "newdesign" }, el("summary", { class: "mtab" }, "＋ New design"), menu);
  document.addEventListener("click", (ev) => { if (!details.contains(ev.target as Node)) details.open = false; });
  wrap.appendChild(details);
  return wrap;
}
