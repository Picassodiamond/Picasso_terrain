/**
 * Accounts check against a server started with PLM_AUTH_ENABLED=1 (guest sandbox on, invite-only):
 *   - the first registration (via API) creates the admin; the admin creates a user account
 *   - a visitor is a guest: guest banner, can create a sandbox project, sees "Sign in"
 *   - signing in as the created user claims the sandbox project (owner role)
 *   - the admin page lists accounts; the library page renders
 *   node e2e/auth_check.mjs http://127.0.0.1:8011
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const base = process.argv[2] || "http://127.0.0.1:8011";
const outDir = new URL("./screenshots/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
mkdirSync(outDir, { recursive: true });
const j = async (url, init) => { const r = await fetch(base + url, init); const t = await r.text(); if (!r.ok) throw new Error(`${url}: ${r.status} ${t}`); return t ? JSON.parse(t) : null; };
const post = (url, body, cookie) => j(url, { method: "POST", headers: { "Content-Type": "application/json", ...(cookie ? { cookie } : {}) }, body: JSON.stringify(body) });

// bootstrap through the API
const st = await j("/api/auth/status");
if (!st.auth_enabled) { console.error("server must run with PLM_AUTH_ENABLED=1"); process.exit(2); }
let adminCookie = null;
if (st.users === 0) {
  const r = await fetch(base + "/api/auth/register", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "admin", password: "adminpass1", organisation: "DoR" }) });
  adminCookie = r.headers.get("set-cookie");
} else {
  const r = await fetch(base + "/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "admin", password: "adminpass1" }) });
  adminCookie = r.headers.get("set-cookie");
}
const cookie = adminCookie.split(";")[0];
const users = await j("/api/auth/users", { headers: { cookie } });
if (!users.some((u) => u.username === "ram")) await post("/api/auth/users", { username: "ram", password: "rampass123", role: "editor", organisation: "DoR", full_name: "Ram Thapa" }, cookie);
console.log(`server ok: ${users.length + 1} account(s), guest sandbox ${st.guest_enabled ? "on" : "off"}`);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
await page.addInitScript(() => { window.__plmNoVisitorForm = true; });  // the visitor form must not block the check
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
page.on("dialog", (d) => d.accept());
const step = async (name, fn) => { process.stdout.write(`- ${name} ... `); await fn(); console.log("ok"); };

await step("guest sees the sandbox banner and Sign in", async () => {
  await page.goto(base + "/", { waitUntil: "networkidle" });
  await page.waitForSelector(".guest-banner", { timeout: 20000 });
  await page.waitForSelector('button:has-text("Sign in")');
  await page.screenshot({ path: `${outDir}auth-guest.png` });
});

const projName = `Guest trial ${Date.now()}`;
await step("guest creates a sandbox project", async () => {
  await page.fill('input[placeholder^="e.g."]', projName);
  await page.click('button:has-text("Create project")');
  await page.waitForSelector("#cesium canvas", { timeout: 60000 });
  await page.waitForSelector(".ws-banner", { timeout: 20000 });
  const banner = await page.locator(".ws-banner").first().innerText();
  if (!/Guest sandbox/.test(banner)) throw new Error(`banner: ${banner}`);
});

await step("sign in claims the sandbox project", async () => {
  await page.locator(".ws-banner button:has-text(\"Sign in\")").click();
  await page.waitForSelector(".dialog", { timeout: 10000 });
  const info = await page.locator(".dialog .muted").innerText().catch(() => "");
  if (!/administrator/.test(info)) throw new Error(`invite-only notice missing: ${info}`);
  await page.fill('input[placeholder="username"]', "ram");
  await page.fill('input[placeholder^="password"]', "rampass123");
  await page.click('button:has-text("Sign in")');
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(1500);
  await page.goto(base + "/", { waitUntil: "networkidle" });
  await page.waitForSelector(".project-card", { timeout: 20000 });
  const cards = await page.locator(".project-card").allInnerTexts();
  const mine = cards.find((c) => c.includes(projName));
  if (!mine || !/owner/.test(mine)) throw new Error(`claimed project not shown as owner: ${mine}`);
  if (!(await page.locator("text=Ram Thapa").count())) throw new Error("signed-in name missing");
  await page.screenshot({ path: `${outDir}auth-claimed.png` });
});

await step("library page", async () => {
  await page.goto(base + "/#/library", { waitUntil: "networkidle" });
  await page.waitForSelector(".lib-map canvas", { timeout: 20000 });
  await page.screenshot({ path: `${outDir}auth-library.png` });
});

await step("admin page lists accounts", async () => {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  await ctx.addCookies([{ name: "plm_session", value: cookie.split("=")[1], url: base }]);
  const p2 = await ctx.newPage();
  p2.on("pageerror", (e) => errors.push(`pageerror(admin): ${e.message}`));
  await p2.goto(base + "/#/admin", { waitUntil: "networkidle" });
  await p2.waitForSelector("table.data", { timeout: 20000 });
  const txt = await p2.locator("table.data").innerText();
  if (!/ram/.test(txt) || !/admin/.test(txt)) throw new Error("accounts table incomplete");
  await p2.screenshot({ path: `${outDir}auth-admin.png` });
  await ctx.close();
});

await browser.close();
if (errors.length) { console.log("page errors:", errors); process.exit(1); }
console.log("PASS: accounts, guest sandbox, claim, library, admin");
