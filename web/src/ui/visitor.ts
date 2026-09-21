/** First-visit introduction form.
 *
 *  The server recognises a returning person by a long-lived cookie and, failing that, by the address
 *  they connect from, so this is shown once. Everything after it - importing a survey, building a
 *  surface, contouring, designing a road, exporting drawings - is written to the usage register
 *  against this record, which an administrator reads at #/admin.
 */
import { api, ApiError, type VisitorState } from "../api";
import { button, el, field } from "./dom";

const DESIGNATIONS = ["Engineer", "Sub-engineer", "Overseer", "Divisional Engineer", "Senior Divisional Engineer",
  "Project Manager", "Surveyor", "Design Consultant", "Contractor", "Lecturer / Researcher", "Student", "Other"];

const DISTRICTS_HINT = "e.g. Kaski, Sindhuli, Kathmandu";

export function shouldAsk(st: VisitorState | null): boolean {
  return !!st && st.tracking && st.intake_enabled && st.needs_intake;
}

/** Puts the form up over whatever is on screen; resolves when the visitor has answered or skipped. */
export function askVisitor(root: HTMLElement, st: VisitorState): Promise<void> {
  return new Promise((resolve) => {
    const pre = st.prefill || {};
    const name = el("input", { type: "text", autocomplete: "name", placeholder: "your full name", value: pre.name || "" });
    const email = el("input", { type: "email", autocomplete: "email", placeholder: "name@office.gov.np", value: pre.email || "" });
    const phone = el("input", { type: "tel", autocomplete: "tel", placeholder: "98XXXXXXXX" });
    const designation = el("input", { type: "text", list: "plm-designations", placeholder: "Engineer, Sub-engineer, ..." });
    const organisation = el("input", { type: "text", placeholder: "office, department or firm", value: pre.organisation || "" });
    const district = el("input", { type: "text", placeholder: DISTRICTS_HINT });
    const purpose = el("input", { type: "text", placeholder: "e.g. rural road alignment, canal survey" });
    const datalist = el("datalist", { id: "plm-designations" }, ...DESIGNATIONS.map((d) => el("option", { value: d })));
    const err = el("div", { class: "error" });

    const overlay = el("div", { class: "overlay" });
    const close = () => { overlay.remove(); resolve(); };

    const send = async () => {
      err.textContent = "";
      if (name.value.trim().length < 2) { err.textContent = "Please give your name."; name.focus(); return; }
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.value.trim())) { err.textContent = "Please give a working email address."; email.focus(); return; }
      try {
        await api.visitor.intake({
          name: name.value.trim(), email: email.value.trim(), phone: phone.value.trim(),
          designation: designation.value.trim(), organisation: organisation.value.trim(),
          district: district.value.trim(), purpose: purpose.value.trim(),
        });
        close();
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) { close(); return; }  // tracking switched off meanwhile
        err.textContent = e instanceof ApiError ? e.detail : String(e);
      }
    };

    const later = async () => {
      try { await api.visitor.skip(); } catch { /* the form is optional; never block on this */ }
      close();
    };

    [name, email, phone, designation, organisation, district, purpose].forEach((i) =>
      i.addEventListener("keydown", (ev) => { if (ev.key === "Enter") void send(); }));

    overlay.appendChild(el("div", { class: "dialog wide" },
      el("div", { class: "brand", style: "margin-bottom:6px;color:var(--accent);font-weight:700" }, "▲ Picasso LandMesh"),
      el("h2", {}, "Welcome - who are we working with?"),
      el("p", { class: "muted" },
        "This looks like your first visit from this connection. A few details let us support you, tell you when "
        + "standards or modules change, and keep a record of the work done on this server."),
      el("div", { class: "row" }, field("Name", name), field("Email", email)),
      el("div", { class: "row" }, field("Phone", phone), field("Designation", designation)),
      el("div", { class: "row" }, field("Organisation / office", organisation), field("District", district)),
      field("What will you use it for?", purpose),
      datalist,
      err,
      el("div", { class: "btn-row" }, button("Continue", () => void send(), "btn primary"), button("Not now", () => void later(), "btn")),
      el("p", { class: "hint" },
        `Your address ${st.visitor?.ip ? `(${st.visitor.ip}) ` : ""}is recorded with these details and with the major `
        + "actions you take. Name and email are required; the rest helps us understand who uses the software."
        + (st.intake_required ? " On this server the form must be filled before designing." : " You may answer later.")),
    ));
    root.appendChild(overlay);
    name.focus();
  });
}

/** Called at start-up: fetch the state and show the form when the server says this is a new visitor. */
export async function maybeAskVisitor(root: HTMLElement): Promise<void> {
  // the end-to-end checks and screenshot runs set this so the form does not sit over every picture
  if ((window as any).__plmNoVisitorForm) return;
  let st: VisitorState;
  try {
    st = await api.visitor.me();
  } catch {
    return;  // tracking is never allowed to stop the application from starting
  }
  if (shouldAsk(st)) await askVisitor(root, st);
}
