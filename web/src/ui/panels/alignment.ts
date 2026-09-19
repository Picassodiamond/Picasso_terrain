import { api, ApiError, type Alignment, type IPIn } from "../../api";
import { store, toast } from "../../state";
import { button, download, el, field, fmt, fmtChainage, numberInput, parseChainage, timeAgo } from "../dom";
import type { Workspace } from "../workspace";

interface Draft { id: number | null; name: string; start: number; ips: IPIn[]; chainageInterval: number }

let draft: Draft | null = null; // survives tab switches while editing

export function renderAlignmentPanel(ws: Workspace, host: HTMLElement): void {
  const pid = ws.project.id;
  const user = store.get("user");
  const authed = !!user?.authenticated;

  host.append(el("h3", {}, "Alignments"));
  const listEl = el("div");
  const editorEl = el("div");
  host.append(listEl, editorEl);

  const importFile = el("input", { type: "file", accept: ".csv,.swr,.geojson,.json,.dxf" });
  const startCh = el("input", { type: "text", value: "0+000", style: "width:110px" });
  host.append(el("div", { class: "card" }, el("h4", {}, "Import alignment"),
    el("div", { class: "row" }, field("File", importFile, "legacy *_aln.csv, GeoJSON LineString, DXF H_ALIGN polyline"), field("Start chainage", startCh)),
    el("div", { class: "btn-row" }, button("Import", async () => {
      const f = importFile.files?.[0];
      if (!f) return;
      try {
        const a = await api.alignments.importFile(pid, f, { start_chainage: String(parseChainage(startCh.value)) });
        toast(`Imported ${a.name} (${fmt(a.length, 1)} m)`, "ok");
        store.set("currentAlignment", a.id);
        await ws.refreshAlignments();
        renderList();
      } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
    }))));

  // ---------------------------------------------------------------- list
  function renderList() {
    listEl.innerHTML = "";
    const als = store.get("alignments");
    const cur = store.get("currentAlignment");
    listEl.appendChild(el("div", { class: "btn-row" }, button("＋ New alignment (draw on map)", () => startDraft(null), "btn primary")));
    if (!als.length) listEl.appendChild(el("p", { class: "muted" }, "No alignments yet. Draw one: click IPs on the map, double-click to finish, then set curve radii."));
    for (const a of als) {
      const lockedByOther = a.lock && a.lock.user_id !== user?.id;
      listEl.appendChild(el("div", { class: `card clickable${a.id === cur ? " selected" : ""}`, onClick: () => { store.set("currentAlignment", a.id); ws.drawAlignment(a); draft = null; renderList(); renderEditor(); } },
        el("div", { style: "display:flex;gap:6px;align-items:center;flex-wrap:wrap" }, el("b", {}, a.name),
          el("span", { class: a.valid ? "badge ok" : "badge err" }, a.valid ? "valid" : "check radii"),
          a.version ? el("span", { class: "badge" }, `v${a.version}`) : null,
          a.lock ? el("span", { class: lockedByOther ? "badge warn" : "badge ok" }, `✎ ${a.lock.username}`) : null),
        el("div", { class: "muted" }, `${a.ips.length} IPs · ${fmtChainage(a.start_chainage)} → ${fmtChainage(a.end_chainage)} · ${fmt(a.length, 1)} m`),
      ));
    }
  }

  // ---------------------------------------------------------------- editor
  function startDraft(a: Alignment | null) {
    draft = a ? { id: a.id, name: a.name, start: a.start_chainage, ips: a.ips.map((p) => ({ ...p })), chainageInterval: Number((a.style as any)?.chainage_interval ?? 20) }
      : { id: null, name: `Alignment ${store.get("alignments").length + 1}`, start: 0, ips: [], chainageInterval: 20 };
    installTool();
    renderEditor();
  }

  let previewTimer: number | null = null;
  let lastPreview: Alignment | null = null;
  function schedulePreview(immediate = false) {
    if (!draft) return;
    if (previewTimer) window.clearTimeout(previewTimer);
    const run = async () => {
      if (!draft) return;
      ws.drawAlignment(lastPreview && draft.ips.length >= 2 ? lastPreview : null, { editable: true, draftIps: draft.ips, chainageInterval: draft.chainageInterval });
      if (draft.ips.length < 2) { lastPreview = null; return; }
      try {
        lastPreview = await api.alignments.preview(pid, { name: draft.name, start_chainage: draft.start, ips: draft.ips });
        ws.drawAlignment(lastPreview, { editable: true, draftIps: draft.ips, chainageInterval: draft.chainageInterval });
        renderStats(lastPreview);
      } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
    };
    if (immediate) void run(); else previewTimer = window.setTimeout(run, 200);
  }

  function installTool() {
    ws.toolHandlers = {
      hint: "Alignment: click to add IPs · drag an IP to move it · double-click or Esc when done · set radii in the table",
      onClick: (p, picked) => {
        if (!draft || picked?.type === "ip") return;
        draft.ips.push({ x: Number(p.x.toFixed(3)), y: Number(p.y.toFixed(3)), radius: 0, label: String(draft.ips.length) });
        renderTable();
        schedulePreview(true);
      },
      onDouble: () => { ws.setTool("none"); ws.interaction.dragEnabled = true; installDragOnly(); },
      onDrag: (i, p, phase) => {
        if (!draft || !draft.ips[i]) return;
        draft.ips[i].x = Number(p.x.toFixed(3)); draft.ips[i].y = Number(p.y.toFixed(3));
        if (phase === "end") { renderTable(); schedulePreview(true); } else schedulePreview();
      },
    };
    ws.interaction.dragEnabled = true;
    ws.setTool("alignment");
  }
  function installDragOnly() {
    ws.toolHandlers = { onDrag: (i, p, phase) => { if (!draft?.ips[i]) return; draft.ips[i].x = Number(p.x.toFixed(3)); draft.ips[i].y = Number(p.y.toFixed(3)); if (phase === "end") { renderTable(); schedulePreview(true); } else schedulePreview(); } };
    ws.interaction.dragEnabled = true;
  }

  const statsEl = el("div", { class: "kv" });
  const tableEl = el("div");
  function renderStats(a: Alignment | null) {
    statsEl.innerHTML = "";
    if (!a) return;
    statsEl.append(el("dt", {}, "length"), el("dd", {}, `${fmt(a.length, 2)} m  (${fmtChainage(a.start_chainage)} → ${fmtChainage(a.end_chainage)})`));
    statsEl.append(el("dt", {}, "status"), el("dd", {}, a.valid ? "valid" : el("span", { class: "error" }, a.issues.map((i) => i.message).join("; "))));
    for (const g of a.geometry.filter((g: any) => g.curve_length > 0)) {
      statsEl.append(el("dt", {}, `IP ${g.label}`), el("dd", {}, `R ${g.radius}  Δ ${(Math.abs(g.deflection) * 180 / Math.PI).toFixed(2)}°  T ${fmt(g.tangent_length, 2)}  L ${fmt(g.curve_length, 2)}  BC ${fmtChainage(g.bc_chainage)}  EC ${fmtChainage(g.ec_chainage)}${g.valid ? "" : " ✗"}`));
    }
  }
  function renderTable() {
    tableEl.innerHTML = "";
    if (!draft) return;
    const tbl = el("table", { class: "data" }, el("tr", {}, el("th", {}, "#"), el("th", {}, "Label"), el("th", {}, "Easting"), el("th", {}, "Northing"), el("th", {}, "Radius"), el("th")));
    draft.ips.forEach((p, i) => {
      const lab = el("input", { type: "text", value: p.label, style: "width:56px" });
      const x = numberInput(p.x, { style: "width:96px", step: "0.01" });
      const y = numberInput(p.y, { style: "width:96px", step: "0.01" });
      const r = numberInput(p.radius, { style: "width:70px", min: "0", step: "5", disabled: i === 0 || i === draft!.ips.length - 1 });
      const upd = () => { p.label = lab.value; p.x = Number(x.value); p.y = Number(y.value); p.radius = Number(r.value); schedulePreview(true); };
      [lab, x, y, r].forEach((inp) => inp.addEventListener("change", upd));
      tbl.appendChild(el("tr", {}, el("td", {}, String(i)), el("td", {}, lab), el("td", {}, x), el("td", {}, y), el("td", {}, r),
        el("td", {}, button("✕", () => { draft!.ips.splice(i, 1); draft!.ips.forEach((q, k) => { if (/^\d+$/.test(q.label)) q.label = String(k); }); renderTable(); schedulePreview(true); }, "btn small danger"))));
    });
    tableEl.appendChild(tbl);
  }

  async function renderEditor() {
    editorEl.innerHTML = "";
    const cur = store.get("currentAlignment");
    const a = store.get("alignments").find((x) => x.id === cur) ?? null;
    if (!draft) {
      if (!a) return;
      // read-only view of the selected alignment + turn-taking controls
      const lock = a.lock;
      const mine = lock && lock.user_id === user?.id;
      const banner = lock ? el("div", { class: `lock-banner${mine ? " mine" : ""}` }, mine ? `You hold the editing turn until ${new Date(lock.expires).toLocaleTimeString()}` : `${lock.username} is editing (until ${new Date(lock.expires).toLocaleTimeString()})`) : null;
      const canEdit = !lock || mine || !authed;
      const versions = await api.alignments.versions(pid, a.id).catch(() => []);
      const vlist = el("div");
      for (const v of versions.slice(0, 8)) vlist.appendChild(el("div", { class: "activity" }, el("span", { class: "who" }, `v${v.version} ${v.username ?? ""}`), ` ${v.note}`, el("span", { class: "when" }, timeAgo(v.created)),
        v.version !== a.version ? button("restore", async () => { try { await api.alignments.restore(pid, a.id, v.version); toast(`Restored version ${v.version}`, "ok"); await ws.refreshAlignments(); renderList(); renderEditor(); } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); } }, "btn small") : null));
      renderStats(a);
      editorEl.append(el("div", { class: "card" },
        el("h4", {}, a.name), banner, statsEl,
        el("div", { class: "btn-row" },
          canEdit ? button(authed ? (mine ? "Continue editing" : "Take turn & edit") : "Edit", async () => {
            if (authed && !mine) { try { await api.alignments.lock(pid, a.id); } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); await ws.refreshAlignments(); renderList(); renderEditor(); return; } }
            startDraft(a);
          }, "btn primary") : null,
          mine ? button("Release turn", async () => { await api.alignments.unlock(pid, a.id); await ws.refreshAlignments(); renderList(); renderEditor(); }) : null,
          lock && !mine && (user?.role === "admin") ? button("Force release", async () => { await api.alignments.unlock(pid, a.id, true); await ws.refreshAlignments(); renderList(); renderEditor(); }, "btn danger") : null,
          button("Download CSV", () => download(api.alignments.csvUrl(pid, a.id))),
          button("Zoom", () => { const xs = a.ips.map((p) => p.x), ys = a.ips.map((p) => p.y); store.emit("map:flyTo", [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]); }),
          canEdit ? button("Delete", async () => { if (confirm(`Delete alignment "${a.name}"?`)) { try { await api.alignments.delete(pid, a.id); store.set("currentAlignment", null); await ws.refreshAlignments(); await ws.refreshSectionSets(); renderList(); renderEditor(); } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); } } }, "btn danger") : null,
        ),
        el("details", {}, el("summary", {}, `History (${versions.length})`), vlist),
        el("details", {}, el("summary", {}, "Comments"), commentsFor(a)),
      ));
      return;
    }
    // editing draft
    const name = el("input", { type: "text", value: draft.name });
    const start = el("input", { type: "text", value: fmtChainage(draft.start) });
    const chInt = numberInput(draft.chainageInterval, { min: "1", step: "5" });
    name.addEventListener("change", () => (draft!.name = name.value));
    start.addEventListener("change", () => { draft!.start = parseChainage(start.value); schedulePreview(true); });
    chInt.addEventListener("change", () => { draft!.chainageInterval = Number(chInt.value); schedulePreview(true); });
    const note = el("input", { type: "text", placeholder: "what changed (for the history)" });
    renderTable();
    editorEl.append(el("div", { class: "card" },
      el("h4", {}, draft.id ? `Editing ${draft.name}` : "New alignment"),
      el("div", { class: "row3" }, field("Name", name), field("Start chainage", start), field("Chainage labels every (m)", chInt)),
      el("p", { class: "hint" }, "Click on the map to add IPs, drag IPs to move them, type radii for curves (first/last IP cannot have a curve). Double-click / Esc to stop adding."),
      el("div", { class: "btn-row" }, button("Add IPs on map", () => installTool(), store.get("tool") === "alignment" ? "btn small active" : "btn small"), button("Stop adding", () => { ws.setTool("none"); installDragOnly(); }, "btn small")),
      tableEl,
      statsEl,
      field("Change note", note),
      el("div", { class: "btn-row" },
        button("Save", async () => {
          if (!draft || draft.ips.length < 2) { toast("An alignment needs at least two IPs", "error"); return; }
          const body = { name: draft.name, start_chainage: draft.start, ips: draft.ips, style: { chainage_interval: draft.chainageInterval } };
          try {
            const saved = draft.id ? await api.alignments.update(pid, draft.id, body, note.value) : await api.alignments.create(pid, body);
            toast(`Saved ${saved.name} (v${saved.version ?? 1})`, "ok");
            const keepLock = authed && draft.id !== null;
            draft = null;
            ws.setTool("none");
            ws.interaction.dragEnabled = false;
            store.set("currentAlignment", saved.id);
            await ws.refreshAlignments();
            if (!keepLock && authed) await api.alignments.unlock(pid, saved.id).catch(() => undefined);
            await ws.refreshAlignments();
            renderList(); renderEditor();
          } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
        }, "btn primary"),
        button("Cancel", async () => { const id = draft?.id; draft = null; ws.setTool("none"); ws.interaction.dragEnabled = false; if (id && authed) await api.alignments.unlock(pid, id).catch(() => undefined); await ws.refreshAlignments(); renderList(); renderEditor(); }),
      ),
    ));
    schedulePreview(true);
  }

  function commentsFor(a: Alignment): HTMLElement {
    const box = el("div");
    const list = () => {
      box.innerHTML = "";
      const cs = ws.comments.filter((c) => c.target_type === "alignment" && c.target_id === String(a.id) && !c.parent_id);
      if (!cs.length) box.appendChild(el("p", { class: "muted" }, "No feedback yet."));
      for (const c of cs) box.appendChild(el("div", { class: `comment${c.resolved ? " resolved" : ""}` }, el("div", { class: "who" }, `${c.username ?? "?"} · ${timeAgo(c.created)}${c.chainage != null ? " · CH " + fmtChainage(c.chainage) : ""}`), c.text));
      const txt = el("input", { type: "text", placeholder: "Add feedback on this alignment…" });
      box.appendChild(el("div", { class: "btn-row" }, txt, button("Post", async () => { if (!txt.value.trim()) return; await api.collab.addComment(pid, { text: txt.value.trim(), target_type: "alignment", target_id: String(a.id) }); await ws.refreshComments(); list(); }, "btn small primary")));
    };
    list();
    return box;
  }

  renderList();
  void renderEditor();
  if (draft) { installDragOnly(); schedulePreview(true); }
}
