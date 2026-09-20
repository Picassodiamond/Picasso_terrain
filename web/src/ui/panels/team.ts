import { api, ApiError, type Activity, type Comment } from "../../api";
import { store, toast } from "../../state";
import { button, el, fmt, fmtChainage, select, timeAgo } from "../dom";
import type { Workspace } from "../workspace";

export function renderTeamPanel(ws: Workspace, host: HTMLElement): void {
  const pid = ws.project.id;
  const user = store.get("user");
  const authed = !!user?.authenticated;

  // members and sharing (owner only)
  const isOwner = ws.project.my_role === "owner" || !authed;
  const membersEl = el("div");
  const uname = el("input", { type: "text", placeholder: "username (an account created by the admin)" });
  const role = select([{ value: "editor", label: "editor" }, { value: "viewer", label: "viewer" }], "editor");
  const err = (e: unknown) => toast(e instanceof ApiError ? e.detail : String(e), "error");
  async function renderMembers() {
    membersEl.innerHTML = "";
    if (!store.get("authEnabled")) { membersEl.appendChild(el("p", { class: "muted" }, "Authentication is disabled on this server - everyone can edit.")); return; }
    if (user?.guest) { membersEl.appendChild(el("p", { class: "muted" }, "Sign in to share projects with other people.")); return; }
    const ms = await api.collab.members(pid);
    const tbl = el("table", { class: "data" }, el("tr", {}, el("th", {}, "User"), el("th", {}, "Role"), el("th", {}, "Organisation"), el("th")));
    for (const m of ms) {
      let roleCell: HTMLElement;
      if (isOwner && m.role !== "owner") {
        const sel = select([{ value: "viewer", label: "viewer" }, { value: "editor", label: "editor" }], m.role, { class: "mini" });
        sel.addEventListener("change", async () => { try { await api.collab.setRole(pid, m.user_id, sel.value); toast("Role changed", "ok"); } catch (e) { err(e); renderMembers(); } });
        roleCell = sel;
      } else roleCell = el("span", { class: `badge${m.role === "owner" ? " ok" : ""}` }, m.role);
      tbl.appendChild(el("tr", {}, el("td", {}, m.username), el("td", {}, roleCell), el("td", {}, m.organisation || ""),
        el("td", {}, isOwner && m.role !== "owner" ? button("✕", async () => { try { await api.collab.removeMember(pid, m.user_id); renderMembers(); } catch (e) { err(e); } }, "btn small danger") : null)));
    }
    membersEl.appendChild(tbl);
    if (isOwner) {
      const vis = select([{ value: "private", label: "private - members only" }, { value: "org", label: "organisation - everyone in my organisation" }, { value: "public", label: "public - anyone can view" }],
        String((ws.project.settings as any)?.visibility || "org"));
      vis.addEventListener("change", async () => {
        try { ws.project = await api.projects.update(pid, { settings: { visibility: vis.value } }); toast(`Visibility: ${vis.value}`, "ok"); } catch (e) { err(e); }
      });
      membersEl.append(
        el("div", { class: "btn-row" }, uname, role, button("Add member", async () => {
          try { await api.collab.addMember(pid, uname.value.trim(), role.value); uname.value = ""; renderMembers(); toast("Member added", "ok"); } catch (e) { err(e); }
        }, "btn small primary")),
        el("div", { class: "field" }, el("span", { class: "field-label" }, "Who can see this project"), vis),
        el("div", { class: "btn-row" }, button("Transfer ownership…", async () => {
          const to = prompt("Username of the new owner (you stay as an editor)");
          if (!to) return;
          try { await api.collab.transfer(pid, to.trim()); toast(`Ownership transferred to ${to}`, "ok"); ws.project = await api.projects.get(pid); renderMembers(); } catch (e) { err(e); }
        }, "btn small")),
        el("p", { class: "hint" }, "You are the owner: only you (or an administrator) can add or remove members, change roles, change visibility, archive or delete this project. Editors change data and designs, viewers read and comment."));
    } else {
      membersEl.appendChild(el("p", { class: "hint" }, `You are ${ws.project.my_role || "a member"} here. The owner manages members and visibility.`));
    }
  }
  host.append(el("h3", {}, "Team & sharing"), el("div", { class: "card" }, membersEl));
  void renderMembers();

  // comments
  const commentsEl = el("div");
  const text = el("textarea", { rows: "2", placeholder: "Feedback for the team… (optionally pin it on the map)" });
  let pin: { x: number; y: number } | null = null;
  const pinInfo = el("span", { class: "muted" });
  const onlyOpen = el("input", { type: "checkbox", checked: true });
  onlyOpen.addEventListener("change", renderComments);
  const pinBtn = button("📍 Pin on map", () => {
    ws.toolHandlers = { hint: "Click on the map to place the comment pin", onClick: (p) => { pin = { x: p.x, y: p.y }; pinInfo.textContent = `pinned at E ${fmt(p.x, 1)} N ${fmt(p.y, 1)}`; ws.setTool("none"); } };
    ws.setTool("comment");
  }, "btn small");
  const post = button("Post comment", async () => {
    if (!text.value.trim()) return;
    try {
      await api.collab.addComment(pid, { text: text.value.trim(), target_type: pin ? "location" : "project", x: pin?.x, y: pin?.y });
      text.value = ""; pin = null; pinInfo.textContent = "";
      await ws.refreshComments();
      toast("Comment posted", "ok");
    } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
  }, "btn small primary");
  host.append(el("h3", {}, "Feedback & comments"),
    el("div", { class: "card" }, text, el("div", { class: "btn-row" }, pinBtn, post, pinInfo), el("label", { class: "check" }, onlyOpen, "Show unresolved only")),
    commentsEl);

  function commentNode(c: Comment, replies: Comment[]): HTMLElement {
    const canEdit = !authed || c.user_id === user?.id || user?.role === "admin";
    const where = c.x != null ? button("↗", () => store.emit("map:flyTo", [c.x! - 20, c.y! - 20, c.x! + 20, c.y! + 20]), "btn small") : null;
    const target = c.target_type !== "project" && c.target_type !== "location" ? el("span", { class: "badge" }, `${c.target_type} ${c.target_id}${c.chainage != null ? " · " + fmtChainage(c.chainage) : ""}`) : null;
    const replyIn = el("input", { type: "text", placeholder: "reply…", style: "display:none" });
    const node = el("div", { class: `comment${c.resolved ? " resolved" : ""}` },
      el("div", { class: "who" }, `${c.username ?? "?"} · ${timeAgo(c.created)} `, target, where),
      el("div", {}, c.text),
      el("div", { class: "btn-row", style: "margin:4px 0 0" },
        button(c.resolved ? "Reopen" : "Resolve", async () => { await api.collab.patchComment(pid, c.id, { resolved: !c.resolved }); await ws.refreshComments(); }, "btn small"),
        button("Reply", () => { replyIn.style.display = ""; replyIn.focus(); }, "btn small"),
        canEdit ? button("Delete", async () => { if (confirm("Delete comment?")) { await api.collab.deleteComment(pid, c.id); await ws.refreshComments(); } }, "btn small danger") : null),
      replyIn,
      ...replies.map((r) => el("div", { class: "comment reply" }, el("div", { class: "who" }, `${r.username ?? "?"} · ${timeAgo(r.created)}`), r.text)),
    );
    replyIn.addEventListener("keydown", async (e) => { if (e.key === "Enter" && replyIn.value.trim()) { await api.collab.addComment(pid, { text: replyIn.value.trim(), parent_id: c.id, target_type: c.target_type, target_id: c.target_id }); await ws.refreshComments(); } });
    return node;
  }
  function renderComments() {
    commentsEl.innerHTML = "";
    const all = ws.comments;
    const roots = all.filter((c) => !c.parent_id && (!onlyOpen.checked || !c.resolved));
    if (!roots.length) commentsEl.appendChild(el("p", { class: "muted" }, "No comments."));
    for (const c of [...roots].reverse()) commentsEl.appendChild(commentNode(c, all.filter((r) => r.parent_id === c.id)));
  }
  renderComments();
  const unsub = store.subscribe("refresh:comments", renderComments);

  // activity
  const actEl = el("div");
  host.append(el("h3", {}, "Activity"), el("div", { class: "card" }, actEl));
  const locksEl = el("div", { class: "muted", style: "margin-bottom:6px" });
  actEl.appendChild(locksEl);
  const feed = el("div");
  actEl.appendChild(feed);
  const describe = (a: Activity) => {
    const d = a.detail || {};
    switch (a.action) {
      case "import": return `imported ${d.points ?? 0} points`;
      case "tin_done": return `built TIN ${d.result_id ?? ""}`;
      case "contours_done": return `generated contour set ${d.result_id ?? ""}`;
      case "sections_done": return `generated sections ${d.result_id ?? ""}`;
      case "alignment_created": return `created alignment "${d.name}"`;
      case "alignment_edited": return `edited alignment ${a.target_id} (v${d.version})${d.note ? ": " + d.note : ""}`;
      case "comment": return `commented: ${d.text ?? ""}`;
      case "lock_acquired": return `took the editing turn on alignment ${a.target_id}`;
      case "lock_released": return `released alignment ${a.target_id}`;
      default: return a.action.replace(/_/g, " ");
    }
  };
  async function renderActivity(data?: { activity: Activity[]; locks: any[] }) {
    const r = data ?? (await api.collab.activity(pid));
    if (data && data.activity.length === 0 && feed.childElementCount) return;
    const full = await api.collab.activity(pid, 0);
    feed.innerHTML = "";
    for (const a of full.activity.slice(0, 40).filter((a) => !a.action.endsWith("_started"))) feed.appendChild(el("div", { class: "activity" }, el("span", { class: "who" }, a.username ?? "system"), ` ${describe(a)}`, el("span", { class: "when" }, timeAgo(a.created))));
    locksEl.textContent = full.locks.length ? `Editing now: ${full.locks.map((l) => `${l.username} → ${l.target_type} ${l.target_id}`).join(", ")}` : "";
    void r;
  }
  void renderActivity();
  const unsub2 = store.subscribe("activity", (r) => void renderActivity(r));
  // cleanup when the panel is replaced
  const obs = new MutationObserver(() => { if (!host.isConnected || !host.contains(actEl)) { unsub(); unsub2(); obs.disconnect(); } });
  obs.observe(host, { childList: true });
}
