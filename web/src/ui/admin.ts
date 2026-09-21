/** Administration. Two tabs: **accounts** (invite-only - the admin creates each account here, sets
 *  role, organisation and details, resets passwords and disables accounts) and **visitors** (who has
 *  reached the server, the details they gave on their first visit, and the log of major activities).
 *  Route #/admin (admins only). */
import { api, ApiError, type AdminUser } from "../api";
import { store, toast } from "../state";
import { button, el, field, select } from "./dom";
import { renderVisitors } from "./visitorsAdmin";

export async function renderAdmin(root: HTMLElement): Promise<void> {
  root.innerHTML = "";
  const me = store.get("user");
  const page = el("div", { class: "projects-page" });
  root.appendChild(page);
  page.appendChild(el("div", { style: "display:flex;align-items:center;gap:12px;margin-bottom:14px" },
    el("h1", { style: "color:var(--accent);cursor:pointer", onClick: () => { location.hash = ""; } }, "▲ Picasso LandMesh"),
    el("span", { class: "muted" }, "Administration"),
    el("span", { style: "flex:1" }), button("Projects", () => { location.hash = ""; }, "btn small")));
  if (me?.role !== "admin") { page.appendChild(el("p", { class: "error" }, "Administrator role required.")); return; }

  // tabs: accounts | visitors and usage
  const body = el("div");
  const tabs = el("div", { style: "display:flex;gap:6px;margin-bottom:12px" });
  const accountsPane = el("div");
  const visitorsPane = el("div", { id: "admin-visitors", style: "display:none" });
  body.append(accountsPane, visitorsPane);
  const tabButtons: HTMLButtonElement[] = [];
  const show = (which: "accounts" | "visitors") => {
    accountsPane.style.display = which === "accounts" ? "" : "none";
    visitorsPane.style.display = which === "visitors" ? "" : "none";
    tabButtons.forEach((b, i) => { b.className = `mtab${(i === 0) === (which === "accounts") ? " active" : ""}`; });
    if (which === "visitors") void renderVisitors(visitorsPane);
  };
  tabButtons.push(button("Accounts", () => show("accounts"), "mtab active"),
                  button("Visitors & usage", () => show("visitors"), "mtab"));
  tabs.append(...tabButtons);
  page.append(tabs, body);

  const page2 = accountsPane;  // the account screens below live on the first tab

  // create
  const username = el("input", { type: "text", placeholder: "username (3+ chars, letters, digits . _ @ -)" });
  const password = el("input", { type: "text", placeholder: "initial password (8+ chars) - share it with the person" });
  const role = select([{ value: "editor", label: "editor" }, { value: "viewer", label: "viewer" }, { value: "admin", label: "admin" }], "editor");
  const org = el("input", { type: "text", placeholder: "organisation" });
  const fullName = el("input", { type: "text", placeholder: "full name" });
  const email = el("input", { type: "text", placeholder: "email" });
  const notes = el("input", { type: "text", placeholder: "notes (position, office, phone)" });
  const err = el("div", { class: "error" });
  const genPw = () => { const a = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"; password.value = Array.from({ length: 12 }, () => a[Math.floor(Math.random() * a.length)]).join(""); };
  genPw();
  page2.appendChild(el("div", { class: "card" }, el("h3", {}, "Create an account"),
    el("p", { class: "hint" }, "Registration is closed. You create every account and hand the username and password to the person; they can be changed here at any time."),
    el("div", { class: "row" }, field("Username", username), field("Password", password), field("Role", role)),
    el("div", { class: "row" }, field("Full name", fullName), field("Organisation", org)),
    el("div", { class: "row" }, field("Email", email), field("Notes", notes)),
    err,
    el("div", { class: "btn-row" }, button("Create account", async () => {
      err.textContent = "";
      try {
        await api.auth.createUser({ username: username.value.trim(), password: password.value, role: role.value, organisation: org.value.trim(),
          full_name: fullName.value.trim(), email: email.value.trim(), notes: notes.value.trim() });
        toast(`Account ${username.value.trim()} created - share the credentials`, "ok");
        username.value = fullName.value = email.value = notes.value = ""; genPw();
        await renderUsers();
      } catch (e) { err.textContent = e instanceof ApiError ? e.detail : String(e); }
    }, "btn primary"), button("New password", genPw, "btn small"))));

  // list
  const usersEl = el("div");
  page2.append(el("h3", { style: "margin-top:20px" }, "Accounts"), usersEl);
  async function renderUsers() {
    usersEl.innerHTML = "";
    const users: AdminUser[] = await api.auth.users();
    const tbl = el("table", { class: "data" }, el("tr", {}, el("th", {}, "Username"), el("th", {}, "Name"), el("th", {}, "Role"), el("th", {}, "Organisation"), el("th", {}, "Email"), el("th", {}, "Status"), el("th", {}, "Created"), el("th")));
    for (const u of users) {
      const roleSel = select([{ value: "viewer", label: "viewer" }, { value: "editor", label: "editor" }, { value: "admin", label: "admin" }], u.role, { class: "mini" });
      roleSel.addEventListener("change", async () => { try { await api.auth.patchUser(u.id, { role: roleSel.value }); toast("Role changed", "ok"); } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); roleSel.value = u.role; } });
      const status = u.disabled ? el("span", { class: "badge err" }, "disabled") : el("span", { class: "badge ok" }, "active");
      tbl.appendChild(el("tr", {},
        el("td", {}, el("b", {}, u.username), u.id === me?.id ? el("span", { class: "badge" }, "you") : null),
        el("td", {}, u.full_name || ""), el("td", {}, roleSel), el("td", {}, u.organisation || ""), el("td", {}, u.email || ""), el("td", {}, status),
        el("td", { class: "muted" }, new Date(u.created).toLocaleDateString()),
        el("td", {}, el("div", { class: "btn-row" },
          button("Reset password", async () => {
            const pw = prompt(`New password for ${u.username} (8+ characters)`, "");
            if (!pw) return;
            try { await api.auth.patchUser(u.id, { password: pw }); toast("Password set - tell the user", "ok"); } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
          }, "btn small"),
          button("Edit details", async () => {
            const full_name = prompt("Full name", u.full_name || ""); if (full_name === null) return;
            const organisation = prompt("Organisation", u.organisation || ""); if (organisation === null) return;
            const emailV = prompt("Email", u.email || ""); if (emailV === null) return;
            const notesV = prompt("Notes", u.notes || ""); if (notesV === null) return;
            try { await api.auth.patchUser(u.id, { full_name, organisation, email: emailV, notes: notesV }); await renderUsers(); } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
          }, "btn small"),
          u.id !== me?.id ? button(u.disabled ? "Enable" : "Disable", async () => {
            try { await api.auth.patchUser(u.id, { disabled: !u.disabled }); await renderUsers(); } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
          }, u.disabled ? "btn small" : "btn small danger") : null))));
    }
    usersEl.appendChild(el("div", { class: "card" }, tbl));
  }
  await renderUsers();
}
