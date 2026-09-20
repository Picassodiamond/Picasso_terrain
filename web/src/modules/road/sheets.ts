/** Drawing sheet preview: a full-screen overlay showing the server-rendered SVG of one sheet
 *  (the same primitives the DXF export writes), with a sheet list, prev / next, zoom and pan. */
import { api } from "../../api";
import { store, toast } from "../../state";
import { button, download, el } from "../../ui/dom";

export interface SheetInfo { kind: string; index: number; title: string; number: string; paper: number[]; start?: number; end?: number; scale?: string; sections?: number }

const KIND_LABEL: Record<string, string> = { plan: "Plan", profile: "Longitudinal section", sections: "Cross-sections" };

export function kindLabel(kind: string): string { return KIND_LABEL[kind] || kind; }

export function openSheetPreview(pid: string, did: number, sheets: SheetInfo[], start = 0, designName = "design"): void {
  if (!sheets.length) { toast("No sheets to preview yet", "error"); return; }
  const guest = !!store.get("user")?.guest;
  let cur = Math.min(Math.max(start, 0), sheets.length - 1);
  let scale = 1, tx = 0, ty = 0, fitScale = 1;
  const title = el("b", {});
  const counter = el("span", { class: "muted mono" });
  const inner = el("div", { class: "sheet-inner" });
  const viewport = el("div", { class: "sheet-viewport" }, inner);
  const list = el("div", { class: "sheet-list" });
  const dxfBtn = button("DXF (this kind)", () => download(api.road.sheetsDxfUrl(pid, did, sheets[cur].kind)), "btn small");
  const allBtn = button("DXF (all sheets)", () => download(api.road.sheetsDxfUrl(pid, did)), "btn small");
  if (guest) { for (const b of [dxfBtn, allBtn]) { b.disabled = true; b.title = "Sign in to download drawings"; } }

  const apply = () => { inner.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`; };
  const fit = () => {
    const svg = inner.querySelector("svg");
    if (!svg) return;
    const [W, H] = sheets[cur].paper;
    const vw = viewport.clientWidth - 24, vh = viewport.clientHeight - 24;
    fitScale = Math.min(vw / W, vh / H);
    svg.setAttribute("width", String(W)); svg.setAttribute("height", String(H));
    scale = fitScale; tx = (viewport.clientWidth - W * scale) / 2; ty = (viewport.clientHeight - H * scale) / 2;
    apply();
  };
  const zoomAt = (factor: number, cx: number, cy: number) => {
    const ns = Math.min(Math.max(scale * factor, fitScale * 0.5), fitScale * 40);
    tx = cx - (cx - tx) * (ns / scale); ty = cy - (cy - ty) * (ns / scale); scale = ns; apply();
  };
  const load = async () => {
    const s = sheets[cur];
    title.textContent = `${s.number ? s.number + "  " : ""}${s.title}`;
    counter.textContent = `${cur + 1} / ${sheets.length}${s.scale ? `  ·  ${s.scale}` : ""}  ·  ${s.paper[0]} × ${s.paper[1]} mm`;
    inner.innerHTML = `<div class="sheet-loading">Rendering ${kindLabel(s.kind)} sheet ${s.index + 1}…</div>`;
    for (const c of Array.from(list.children)) c.classList.toggle("active", Number((c as HTMLElement).dataset.i) === cur);
    try {
      const res = await fetch(api.road.sheetSvgUrl(pid, did, s.kind, s.index + 1), { credentials: "same-origin" });
      if (!res.ok) { let d = res.statusText; try { d = (await res.json()).detail || d; } catch { /* ignore */ } throw new Error(d); }
      inner.innerHTML = await res.text();
      fit();
    } catch (e) {
      inner.innerHTML = `<p class="error" style="padding:20px">${e instanceof Error ? e.message : String(e)}</p>`;
    }
  };
  const go = (i: number) => { cur = (i + sheets.length) % sheets.length; void load(); };

  // sheet list grouped by kind
  let lastKind = "";
  sheets.forEach((s, i) => {
    if (s.kind !== lastKind) { list.appendChild(el("div", { class: "sheet-group" }, kindLabel(s.kind))); lastKind = s.kind; }
    list.appendChild(el("div", { class: "sheet-item", "data-i": String(i), onClick: () => go(i) }, el("span", { class: "mono" }, s.number || `${s.index + 1}`), " ", el("span", { class: "muted" }, s.title.replace(/^(PLAN|LONGITUDINAL SECTION|CROSS SECTIONS)\s*/, ""))));
  });

  const close = () => { overlay.remove(); window.removeEventListener("keydown", onKey); window.removeEventListener("resize", fit); };
  const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); else if (e.key === "ArrowRight") go(cur + 1); else if (e.key === "ArrowLeft") go(cur - 1); };
  const head = el("div", { class: "sheet-head" },
    button("‹", () => go(cur - 1), "btn small"), button("›", () => go(cur + 1), "btn small"), title, counter, el("span", { class: "spacer" }),
    button("−", () => zoomAt(1 / 1.3, viewport.clientWidth / 2, viewport.clientHeight / 2), "btn small"), button("Fit", fit, "btn small"),
    button("+", () => zoomAt(1.3, viewport.clientWidth / 2, viewport.clientHeight / 2), "btn small"),
    button("Open SVG", () => window.open(api.road.sheetSvgUrl(pid, did, sheets[cur].kind, sheets[cur].index + 1), "_blank"), "btn small"),
    dxfBtn, allBtn, button("✕ Close", close, "btn small"));
  const overlay = el("div", { class: "overlay sheet-preview" }, el("div", { class: "sheet-dialog" }, head, el("div", { class: "sheet-body" }, list, viewport)));
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  // pan / zoom
  let drag: { x: number; y: number; tx: number; ty: number } | null = null;
  viewport.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, tx, ty }; viewport.setPointerCapture(e.pointerId); });
  viewport.addEventListener("pointermove", (e) => { if (!drag) return; tx = drag.tx + e.clientX - drag.x; ty = drag.ty + e.clientY - drag.y; apply(); });
  viewport.addEventListener("pointerup", () => { drag = null; });
  viewport.addEventListener("wheel", (e) => { e.preventDefault(); const r = viewport.getBoundingClientRect(); zoomAt(e.deltaY < 0 ? 1.2 : 1 / 1.2, e.clientX - r.left, e.clientY - r.top); }, { passive: false });
  viewport.addEventListener("dblclick", fit);
  window.addEventListener("keydown", onKey);
  window.addEventListener("resize", fit);
  document.body.appendChild(overlay);
  void load();
  void designName;
}
