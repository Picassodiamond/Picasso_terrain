/** Selection and properties: one contract for every element in the software.
 *
 *  Anything the user can point at - a survey point, a vertex of a breakline, an IP, a PVI, a wall,
 *  a drain, a culvert - describes itself as a `Selection`: a name, a list of `PropertyField`s and,
 *  when it can be changed, an `apply` that writes them. One inspector renders all of them, so a new
 *  element type means providing a Selection, not writing another bespoke panel.
 *
 *  The rule, in `CLAUDE.md`: if it can be picked, it has properties; if it has properties, they are
 *  shown and edited here, in the units the engineer works in.
 */
import { store, toast } from "../state";
import { button, el, numberInput, select as selectEl } from "./dom";

export type PropertyValue = string | number | boolean | null;

export interface PropertyField {
  key: string;
  label: string;
  value: PropertyValue;
  /** `readonly` is the default for anything the selection cannot write back */
  type?: "number" | "text" | "select" | "checkbox" | "readonly";
  /** shown after the box: m, %, m³, ... - always state the unit */
  unit?: string;
  step?: number;
  min?: number;
  max?: number;
  options?: { value: string; label: string }[];
  hint?: string;
}

export interface SelectionAction {
  label: string;
  danger?: boolean;
  run: () => void | Promise<void>;
}

export interface Selection {
  /** "point" | "vertex" | "ip" | "pvi" | "wall" | "drain" | "culvert" | ... */
  kind: string;
  id: string | number;
  /** what the header reads: "Retaining wall 3" */
  label: string;
  /** the one line of context underneath: "CH 0+040 – 0+120 · left · gabion" */
  subtitle?: string;
  fields: PropertyField[];
  /** write the edited values. Return a sentence for the toast, or throw with the reason. */
  apply?: (values: Record<string, PropertyValue>) => Promise<string | void> | string | void;
  actions?: SelectionAction[];
  /** called when this selection is replaced or cleared */
  onClear?: () => void;
}

let current: Selection | null = null;

/** Make this the selected element. Passing null clears. */
export function selectElement(sel: Selection | null): void {
  if (current && (!sel || current.kind !== sel.kind || current.id !== sel.id)) current.onClear?.();
  current = sel;
  store.set("selection", sel);
}

export function clearSelection(): void {
  selectElement(null);
}

export function selected(): Selection | null {
  return current;
}

/** Re-render the inspector for the same element, e.g. after a drag moved it. */
export function refreshSelection(sel: Selection): void {
  if (current && current.kind === sel.kind && current.id === sel.id) {
    current = sel;
    store.set("selection", sel);
  }
}

/**
 * The inspector: one floating card that renders whatever is selected. Mount it once per workspace.
 * Returns a function that takes it away again.
 */
export function mountInspector(host: HTMLElement, opts: { embedded?: boolean } = {}): () => void {
  const body = el("div", { class: "inspector-body" });
  const head = el("div", { class: "inspector-head" });
  // embedded: a pane inside another panel (the Layers card), so no floating frame and it stays
  // visible with a placeholder when nothing is picked
  const panel = el("div", { class: `inspector${opts.embedded ? " embedded" : ""}`, style: opts.embedded ? "" : "display:none" }, head, body);
  host.appendChild(panel);

  const render = (sel: Selection | null) => {
    head.innerHTML = "";
    body.innerHTML = "";
    if (!sel) {
      if (!opts.embedded) { panel.style.display = "none"; return; }
      body.appendChild(el("p", { class: "muted inspector-empty" },
        "Nothing selected. Click a survey point on the map, a vertex while editing a constraint line, an IP, or a row in the structures table."));
      return;
    }
    panel.style.display = "";

    head.append(
      el("span", { class: "inspector-kind" }, sel.kind),
      el("span", { class: "inspector-title", title: sel.label }, sel.label),
      el("span", { class: "spacer" }),
      button("✕", () => clearSelection(), "btn small"),
    );
    if (sel.subtitle) body.appendChild(el("div", { class: "muted inspector-sub" }, sel.subtitle));

    const inputs = new Map<string, HTMLInputElement | HTMLSelectElement>();
    const grid = el("div", { class: "inspector-grid" });
    for (const f of sel.fields) {
      const kind = f.type ?? (sel.apply ? "text" : "readonly");
      let control: HTMLElement;
      if (kind === "readonly") {
        control = el("span", { class: "inspector-ro mono" }, `${f.value ?? "–"}${f.unit ? ` ${f.unit}` : ""}`);
      } else if (kind === "select") {
        const s = selectEl(f.options || [], String(f.value ?? ""), { class: "mini" });
        inputs.set(f.key, s);
        control = s;
      } else if (kind === "checkbox") {
        const c = el("input", { type: "checkbox", checked: !!f.value });
        inputs.set(f.key, c);
        control = c;
      } else if (kind === "number") {
        const n = numberInput(Number(f.value ?? 0), { class: "num", step: String(f.step ?? "any"), ...(f.min !== undefined ? { min: String(f.min) } : {}), ...(f.max !== undefined ? { max: String(f.max) } : {}) });
        inputs.set(f.key, n);
        control = n;
      } else {
        const t = el("input", { type: "text", value: String(f.value ?? "") });
        inputs.set(f.key, t);
        control = t;
      }
      grid.append(
        el("label", { class: "inspector-label", title: f.hint || f.label }, f.label),
        el("div", { class: "inspector-value" }, control, f.unit && kind !== "readonly" ? el("span", { class: "inspector-unit" }, f.unit) : null),
      );
    }
    body.appendChild(grid);

    const hints = sel.fields.filter((f) => f.hint);
    if (hints.length === 1) body.appendChild(el("p", { class: "hint" }, hints[0].hint!));

    const row = el("div", { class: "btn-row" });
    if (sel.apply) {
      row.appendChild(button("Apply", async () => {
        const values: Record<string, PropertyValue> = {};
        for (const [k, inp] of inputs) {
          if (inp instanceof HTMLInputElement && inp.type === "checkbox") values[k] = inp.checked;
          else if (inp instanceof HTMLInputElement && inp.type === "number") values[k] = inp.value === "" ? null : Number(inp.value);
          else values[k] = inp.value;
        }
        try {
          const msg = await sel.apply!(values);
          if (msg) toast(msg, "ok");
        } catch (e) {
          toast(e instanceof Error ? e.message : String(e), "error");
        }
      }, "btn primary small"));
      row.appendChild(button("Revert", () => render(store.get("selection")), "btn small"));
    }
    for (const a of sel.actions || []) {
      row.appendChild(button(a.label, () => void a.run(), `btn small${a.danger ? " danger" : ""}`));
    }
    if (row.childElementCount) body.appendChild(row);
  };

  const off = store.on("selection", (v: Selection | null) => render(v));
  render(store.get("selection"));
  return () => { off(); panel.remove(); };
}

/** Shorthand for the common read-only row. */
export function ro(label: string, value: PropertyValue, unit?: string): PropertyField {
  return { key: label, label, value, type: "readonly", unit };
}
