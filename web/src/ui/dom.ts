/** Tiny DOM helpers (no framework). */

type Attrs = Record<string, string | number | boolean | EventListener | undefined | null>;
type Child = Node | string | number | null | undefined | false | Child[];

export function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Attrs = {}, ...children: Child[]): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v as EventListener);
    else if (k === "class") node.className = String(v);
    else if (k === "html") node.innerHTML = String(v);
    else if (k === "value" && "value" in node) (node as any).value = String(v);
    else if (k === "checked" && "checked" in node) (node as any).checked = Boolean(v);
    else if (k === "disabled" && "disabled" in node) (node as any).disabled = Boolean(v);
    else if (k === "selected" && "selected" in node) (node as any).selected = Boolean(v);
    else node.setAttribute(k, String(v));
  }
  append(node, children);
  return node;
}

export function append(node: Node, children: Child[]): void {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(node, c);
    else if (c instanceof Node) node.appendChild(c);
    else node.appendChild(document.createTextNode(String(c)));
  }
}

export function clear(node: Element): void {
  while (node.firstChild) node.removeChild(node.firstChild);
}

export function fmt(n: number | null | undefined, digits = 3): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "–";
  return n.toFixed(digits);
}

export function fmtChainage(ch: number, decimals = 2): string {
  const sign = ch < 0 ? "-" : "";
  ch = Math.abs(ch);
  let km = Math.floor(ch / 1000);
  let m = ch - km * 1000;
  if (Number(m.toFixed(decimals)) >= 1000) { km += 1; m = 0; }
  const width = 3 + (decimals > 0 ? decimals + 1 : 0);
  return `${sign}${km}+${m.toFixed(decimals).padStart(width, "0")}`;
}

export function parseChainage(t: string): number {
  const s = t.replace(/\s/g, "");
  if (s.includes("+")) {
    const [km, m] = s.split("+");
    return Number(km || 0) * 1000 + Number(m || 0);
  }
  return Number(s);
}

export function field(label: string, input: HTMLElement, hint?: string): HTMLElement {
  return el("label", { class: "field" }, el("span", { class: "field-label" }, label), input, hint ? el("span", { class: "hint" }, hint) : null);
}

export function numberInput(value: number, attrs: Attrs = {}): HTMLInputElement {
  return el("input", { type: "number", value: String(value), step: "any", ...attrs });
}

export function select(options: { value: string; label: string }[], value?: string, attrs: Attrs = {}): HTMLSelectElement {
  const s = el("select", attrs);
  for (const o of options) s.appendChild(el("option", { value: o.value, selected: o.value === value }, o.label));
  return s;
}

export function button(label: string, onClick: (ev: MouseEvent) => void, cls = "btn"): HTMLButtonElement {
  return el("button", { class: cls, type: "button", onClick: onClick as EventListener }, label);
}

export function timeAgo(iso: string): string {
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

export function download(url: string, filename?: string): void {
  const a = document.createElement("a");
  a.href = url;
  if (filename) a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}
