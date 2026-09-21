/** Keyboard shortcuts.
 *
 *  Screens register a *layer* of bindings and get back a function that removes it. Layers stack:
 *  the newest one is asked first, and a layer marked `modal` (a full-screen viewer, say) stops the
 *  ones beneath it from seeing the key at all. Nothing fires while the user is typing in a field.
 *
 *  Every binding carries its own label, so the help card that `?` opens is built from what is
 *  actually active on the screen in front of you - it cannot drift out of date.
 */

export interface Binding {
  /** combinations that trigger it, e.g. ["ArrowLeft"], ["shift+ArrowLeft"], ["mod+s"], ["1"] */
  keys: string[];
  /** how it reads in the help card; defaults to a tidy rendering of `keys` */
  show?: string;
  label: string;
  group?: string;
  /** true for actions that should still work while a field has focus (rare: Save, Escape) */
  whileTyping?: boolean;
  run: (e: KeyboardEvent) => void;
}

interface Layer {
  id: number;
  name: string;
  modal: boolean;
  bindings: Binding[];
}

const layers: Layer[] = [];
let nextId = 1;
let attached = false;

const PRETTY: Record<string, string> = {
  arrowleft: "←", arrowright: "→", arrowup: "↑", arrowdown: "↓",
  pageup: "PgUp", pagedown: "PgDn", home: "Home", end: "End", escape: "Esc",
  mod: navigator.platform.toLowerCase().includes("mac") ? "⌘" : "Ctrl",
  shift: "Shift", alt: "Alt", " ": "Space",
};

function pretty(combination: string): string {
  return combination.split("+").map((p) => PRETTY[p.toLowerCase()] ?? (p.length === 1 ? p.toUpperCase() : p)).join(" ");
}

export function showKeys(b: Binding): string {
  return b.show ?? b.keys.map(pretty).join(" / ");
}

/** Is the user typing? Then the key belongs to the field, not to us. */
export function isTyping(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null;
  if (!t) return false;
  const tag = t.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t.isContentEditable === true;
}

/** Canonical name for what was pressed: "shift+arrowleft", "mod+s", "1", "?" */
function comboOf(e: KeyboardEvent, withShift: boolean): string {
  const parts: string[] = [];
  if (e.ctrlKey || e.metaKey) parts.push("mod");
  if (withShift && e.shiftKey) parts.push("shift");
  if (e.altKey) parts.push("alt");
  parts.push(e.key.toLowerCase());
  return parts.join("+");
}

function handle(e: KeyboardEvent): void {
  if (e.repeat && (e.ctrlKey || e.metaKey)) return;
  // Modifiers must match exactly, so "shift+ArrowRight" never falls through to "ArrowRight".
  // The one exception is a printable character that Shift itself produced ("?" from "/", "+" from
  // "="): there the shift is how the character was typed, not a modifier the binding asked for.
  const candidates = new Set([comboOf(e, true)]);
  if (e.key.length === 1) candidates.add(comboOf(e, false));
  const typing = isTyping(e);
  for (let i = layers.length - 1; i >= 0; i--) {
    for (const b of layers[i].bindings) {
      if (typing && !b.whileTyping) continue;
      if (!b.keys.some((k) => candidates.has(k.toLowerCase()))) continue;
      e.preventDefault();
      e.stopPropagation();
      b.run(e);
      return;
    }
    if (layers[i].modal) return;
  }
}

/** Add a layer of shortcuts; call the returned function to take it away again. */
export function registerShortcuts(name: string, bindings: Binding[], opts: { modal?: boolean } = {}): () => void {
  const layer: Layer = { id: nextId++, name, modal: !!opts.modal, bindings };
  layers.push(layer);
  if (!attached) {
    window.addEventListener("keydown", handle, true);
    attached = true;
  }
  return () => {
    const i = layers.findIndex((l) => l.id === layer.id);
    if (i >= 0) layers.splice(i, 1);
  };
}

/** Everything active right now, newest layer first, stopping at a modal one. */
export function activeBindings(): { layer: string; bindings: Binding[] }[] {
  const out: { layer: string; bindings: Binding[] }[] = [];
  for (let i = layers.length - 1; i >= 0; i--) {
    out.push({ layer: layers[i].name, bindings: layers[i].bindings.filter((b) => b.label) });
    if (layers[i].modal) break;
  }
  return out;
}

/** The help card, built from the bindings that are live on this screen. Bound to `?` everywhere. */
export function showShortcutHelp(): void {
  const existing = document.querySelector(".keys-overlay");
  if (existing) { existing.remove(); return; }

  const groups = new Map<string, Binding[]>();
  for (const { bindings } of activeBindings()) {
    for (const b of bindings) {
      if (!b.label) continue;
      const g = b.group || "General";
      if (!groups.has(g)) groups.set(g, []);
      // a binding from a higher layer wins: do not list the same action twice
      if (!groups.get(g)!.some((x) => x.label === b.label)) groups.get(g)!.push(b);
    }
  }

  const overlay = document.createElement("div");
  overlay.className = "overlay keys-overlay";
  const dialog = document.createElement("div");
  dialog.className = "dialog keys-dialog";
  const h = document.createElement("h2");
  h.textContent = "Keyboard shortcuts";
  dialog.appendChild(h);

  if (!groups.size) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "No shortcuts on this screen.";
    dialog.appendChild(p);
  }
  for (const [name, list] of groups) {
    const section = document.createElement("div");
    section.className = "keys-group";
    const t = document.createElement("h4");
    t.textContent = name;
    section.appendChild(t);
    const table = document.createElement("table");
    table.className = "data keys-table";
    for (const b of list) {
      const tr = document.createElement("tr");
      const kd = document.createElement("td");
      kd.className = "keys-combo";
      for (const part of showKeys(b).split(" / ")) {
        const kbd = document.createElement("kbd");
        kbd.textContent = part;
        kd.appendChild(kbd);
      }
      const ld = document.createElement("td");
      ld.textContent = b.label;
      tr.append(kd, ld);
      table.appendChild(tr);
    }
    section.appendChild(table);
    dialog.appendChild(section);
  }

  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent = "Shortcuts are ignored while you are typing in a box. Press Esc or ? to close.";
  dialog.appendChild(hint);

  const close = () => { overlay.remove(); window.removeEventListener("keydown", onKey, true); };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape" || e.key === "?") { e.preventDefault(); e.stopPropagation(); close(); }
  };
  window.addEventListener("keydown", onKey, true);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
}

/** The `?` binding itself - every screen includes it. */
export function helpBinding(): Binding {
  return { keys: ["?", "shift+?", "f1"], show: "?", label: "Show this list of shortcuts", group: "General", run: () => showShortcutHelp() };
}
