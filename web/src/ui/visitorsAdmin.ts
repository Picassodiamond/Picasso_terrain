/** Administration: the visitor register and the usage log.
 *
 *  Every person who reaches the server gets a record keyed on the address they connect from; those
 *  who filled the introduction form carry their name, email, phone and designation. The second table
 *  is the activity register - one row per major action (import, TIN, contours, alignment, sections,
 *  road design, corridor, export) with who did it, on which project and how long it took.
 */
import { api, ApiError, type UsageRow, type UsageSummary, type VisitorRow } from "../api";
import { toast } from "../state";
import { button, download, el, select, timeAgo } from "./dom";

function stat(label: string, value: string | number): HTMLElement {
  return el("div", { class: "card", style: "flex:1;min-width:130px;margin:0" },
    el("div", { style: "font-size:22px;font-weight:600" }, String(value)),
    el("div", { class: "muted", style: "font-size:11px" }, label));
}

export async function renderVisitors(container: HTMLElement): Promise<void> {
  container.innerHTML = "";
  let summary: UsageSummary;
  try {
    summary = await api.visitor.summary(30);
  } catch (e) {
    container.appendChild(el("p", { class: "error" }, e instanceof ApiError ? e.detail : String(e)));
    return;
  }

  container.appendChild(el("div", { style: "display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px" },
    stat("visitors seen", summary.visitors),
    stat("gave their details", summary.registered),
    stat("active today", summary.active_today),
    stat("actions logged", summary.logged_actions)));

  // ------------------------------------------------------------------ people
  const search = el("input", { type: "text", placeholder: "search address, name, email, office, designation", style: "width:300px" });
  const onlyRegistered = select([{ value: "", label: "everyone" }, { value: "1", label: "gave details" },
    { value: "0", label: "did not answer" }], "", { style: "width:150px" });
  const peopleEl = el("div");
  container.appendChild(el("div", { class: "card" },
    el("div", { style: "display:flex;gap:8px;align-items:center;margin-bottom:8px" },
      el("h3", { style: "margin:0" }, "Visitors"), el("span", { style: "flex:1" }), search, onlyRegistered,
      button("Download CSV", () => download(api.visitor.visitorsCsvUrl(), "plm-visitors.csv"), "btn small")),
    peopleEl));

  let selected = "";
  async function renderPeople(): Promise<void> {
    peopleEl.innerHTML = "";
    const params: Record<string, unknown> = { q: search.value.trim() };
    if (onlyRegistered.value) params.registered = onlyRegistered.value === "1";
    const rows: VisitorRow[] = await api.visitor.list(params);
    if (!rows.length) { peopleEl.appendChild(el("p", { class: "muted" }, "Nobody has used this server yet.")); return; }
    const tbl = el("table", { class: "data" }, el("tr", {},
      ...["Address", "Name", "Designation", "Organisation", "Email", "Phone", "District", "Visits", "Actions", "Last seen", ""]
        .map((h) => el("th", {}, h))));
    for (const v of rows) {
      const answered = Number(v.registered) === 1;
      tbl.appendChild(el("tr", { class: v.id === selected ? "selected" : "" },
        el("td", {}, el("code", {}, v.ip), v.user_id ? el("span", { class: "badge ok" }, "account") : null),
        el("td", {}, answered ? v.name : el("span", { class: "muted" }, "not given")),
        el("td", {}, v.designation || ""), el("td", {}, v.organisation || ""), el("td", {}, v.email || ""),
        el("td", {}, v.phone || ""), el("td", {}, v.district || ""),
        el("td", {}, String(v.visits)), el("td", {}, String(v.actions)),
        el("td", { class: "muted", title: v.last_seen }, timeAgo(v.last_seen)),
        el("td", {}, button(v.id === selected ? "Showing" : "Activity", () => {
          selected = v.id === selected ? "" : v.id;
          void renderPeople();
          void renderUsage();
        }, "btn small"))));
    }
    peopleEl.appendChild(tbl);
  }
  search.addEventListener("input", () => { void renderPeople(); });
  onlyRegistered.addEventListener("change", () => { void renderPeople(); });

  // ------------------------------------------------------------------ activity
  const actionSel = select([{ value: "", label: "all activities" },
    ...Object.entries(summary.labels).map(([value, label]) => ({ value, label }))], "", { style: "width:260px" });
  const usageEl = el("div");
  container.appendChild(el("div", { class: "card" },
    el("div", { style: "display:flex;gap:8px;align-items:center;margin-bottom:8px" },
      el("h3", { style: "margin:0" }, "Activity log"), el("span", { style: "flex:1" }), actionSel,
      button("Refresh", () => { void renderUsage(); }, "btn small"),
      button("Download CSV", () => download(api.visitor.usageCsvUrl({ visitor_id: selected, action: actionSel.value }),
        "plm-usage-log.csv"), "btn small")),
    usageEl));

  async function renderUsage(): Promise<void> {
    usageEl.innerHTML = "";
    const { rows } = await api.visitor.usage({ visitor_id: selected, action: actionSel.value, limit: 300 });
    if (selected) usageEl.appendChild(el("p", { class: "hint" }, "Filtered to one visitor - press Showing again to see everybody."));
    if (!rows.length) { usageEl.appendChild(el("p", { class: "muted" }, "No activity recorded yet.")); return; }
    const tbl = el("table", { class: "data" }, el("tr", {},
      ...["When", "Who", "Address", "Activity", "Module", "Project", "Took"].map((h) => el("th", {}, h))));
    for (const r of rows as UsageRow[]) {
      tbl.appendChild(el("tr", {},
        el("td", { class: "muted", title: r.created }, timeAgo(r.created)),
        el("td", {}, r.visitor_name || r.username || el("span", { class: "muted" }, "anonymous")),
        el("td", {}, el("code", {}, r.ip || "")),
        el("td", { title: `${r.method} ${r.path}` }, r.label || r.action),
        el("td", {}, r.module || ""),
        el("td", {}, r.project_id ? el("a", { href: `#/p/${r.project_id}` }, r.project_id.slice(0, 8)) : ""),
        el("td", { class: "muted" }, r.ms >= 1000 ? `${(r.ms / 1000).toFixed(1)} s` : `${r.ms} ms`)));
    }
    usageEl.appendChild(tbl);
  }
  actionSel.addEventListener("change", () => { void renderUsage(); });

  // ------------------------------------------------------------------ what people use
  if (summary.by_action.length) {
    const tbl = el("table", { class: "data" }, el("tr", {},
      el("th", {}, "Activity"), el("th", {}, "Times"), el("th", {}, "People"), el("th", {}, "Last")));
    for (const a of summary.by_action) {
      tbl.appendChild(el("tr", {}, el("td", {}, summary.labels[a.action] || a.action), el("td", {}, String(a.n)),
        el("td", {}, String(a.visitors)), el("td", { class: "muted" }, timeAgo(a.last))));
    }
    container.appendChild(el("div", { class: "card" }, el("h3", {}, `What was used in the last ${summary.days} days`), tbl));
  }

  try {
    await renderPeople();
    await renderUsage();
  } catch (e) {
    toast(e instanceof ApiError ? e.detail : String(e), "error");
  }
}
