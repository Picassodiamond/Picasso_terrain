import { api, ApiError, type AuthStatus } from "../api";
import { store } from "../state";
import { button, el, field } from "./dom";

export function renderLogin(root: HTMLElement, status: AuthStatus, onDone: () => void): void {
  root.innerHTML = "";
  let mode: "login" | "register" = status.users === 0 ? "register" : "login";
  const err = el("div", { class: "error" });
  const user = el("input", { type: "text", autocomplete: "username", placeholder: "username" });
  const pass = el("input", { type: "password", autocomplete: "current-password", placeholder: "password (min 8 characters)" });
  const org = el("input", { type: "text", placeholder: "organisation (optional)" });
  const title = el("h2");
  const submit = button("", () => void go(), "btn primary");
  const toggle = button("", () => { mode = mode === "login" ? "register" : "login"; render(); });
  const info = el("p", { class: "muted" });

  const render = () => {
    title.textContent = mode === "login" ? "Sign in to Picasso LandMesh" : status.users === 0 ? "Create the first (admin) account" : "Create an account";
    submit.textContent = mode === "login" ? "Sign in" : "Create account";
    toggle.textContent = mode === "login" ? "Need an account?" : "Already have an account?";
    (org.parentElement as HTMLElement).style.display = mode === "register" ? "" : "none";
    toggle.style.display = status.open_registration || status.users === 0 ? "" : "none";
    info.textContent = status.open_registration || status.users === 0 ? "" : "Registration is closed - ask an administrator for an account.";
    err.textContent = "";
  };

  const go = async () => {
    err.textContent = "";
    try {
      const u = mode === "login" ? await api.auth.login(user.value.trim(), pass.value) : await api.auth.register(user.value.trim(), pass.value, org.value.trim());
      store.set("user", u);
      onDone();
    } catch (e) {
      err.textContent = e instanceof ApiError ? e.detail : String(e);
    }
  };
  [user, pass].forEach((i) => i.addEventListener("keydown", (ev) => { if (ev.key === "Enter") void go(); }));

  const dlg = el("div", { class: "dialog" },
    el("div", { class: "brand", style: "margin-bottom:10px;color:var(--accent);font-weight:700" }, "▲ Picasso LandMesh"),
    title,
    field("Username", user), field("Password", pass), field("Organisation", org),
    err,
    el("div", { class: "btn-row" }, submit, toggle),
    info,
  );
  root.appendChild(el("div", { class: "overlay" }, dlg));
  render();
  user.focus();
}
