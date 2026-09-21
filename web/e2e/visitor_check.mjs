/**
 * Visitor check: a first-time visitor is recognised by address, the introduction form comes up, the
 * details are stored, and the major actions that follow appear in the administration register.
 *   node e2e/visitor_check.mjs http://127.0.0.1:8000
 * A random X-Forwarded-For address is used so every run is a genuinely new visitor.
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const base = process.argv[2] || "http://127.0.0.1:8000";
const outDir = new URL("./screenshots/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
mkdirSync(outDir, { recursive: true });
const ip = `203.0.113.${1 + Math.floor(Math.random() * 250)}`;  // TEST-NET-3, never a real address
const name = `Check Visitor ${Math.floor(Math.random() * 10000)}`;

const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, extraHTTPHeaders: { "X-Forwarded-For": ip } });
const page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
page.on("dialog", (d) => d.accept());
const step = async (label, fn) => { process.stdout.write(`- ${label} ... `); await fn(); console.log("ok"); };
const shot = (f) => page.screenshot({ path: `${outDir}${f}`, animations: "disabled" });

await step(`first visit from ${ip}: the form comes up`, async () => {
  await page.goto(`${base}/`, { waitUntil: "networkidle" });
  await page.waitForSelector(".overlay .dialog", { timeout: 20000 });
  const text = await page.locator(".overlay .dialog").innerText();
  if (!text.includes("first visit")) throw new Error(`unexpected dialog: ${text.slice(0, 120)}`);
  if (!text.includes(ip)) throw new Error("the dialog does not show the address it recorded");
  await shot("visitor-01-form.png");
});

await step("the details are accepted and stored", async () => {
  const set = async (label, value) => page.locator(".overlay label.field", { hasText: label }).locator("input").first().fill(value);
  await set("Name", name);
  await set("Email", "check@example.np");
  await set("Phone", "9841000000");
  await set("Designation", "Divisional Engineer");
  await set("Organisation", "Department of Roads");
  await set("District", "Kaski");
  await page.locator(".overlay button", { hasText: "Continue" }).first().dispatchEvent("click");
  await page.waitForSelector(".overlay", { state: "detached", timeout: 15000 });
  const st = await page.evaluate(() => fetch("/api/visitor/me").then((r) => r.json()));
  if (!st.visitor?.registered || st.needs_intake) throw new Error(`not stored: ${JSON.stringify(st)}`);
  console.log(`\n    stored ${st.visitor.name}, ${st.visitor.designation}, ${st.visitor.ip}`);
});

await step("the form is not shown again on a reload", async () => {
  await page.goto(`${base}/`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  if (await page.locator(".overlay .dialog").count()) throw new Error("the form came up a second time");
});

let pid = "";
await step("a major action is written to the log", async () => {
  pid = await page.evaluate(() => fetch("/api/projects", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "Visitor check project", crs: "local" }) }).then((r) => r.json()).then((p) => p.id));
  const rows = await page.evaluate(() => fetch("/api/visitor/admin/usage?limit=20").then((r) => r.json()).then((d) => d.rows));
  const mine = rows.find((r) => r.project_id === pid && r.action === "project.create");
  if (!mine) throw new Error("the project creation was not logged");
  if (mine.ip !== ip || mine.visitor_name !== name) throw new Error(`logged against the wrong visitor: ${JSON.stringify(mine)}`);
  console.log(`\n    ${mine.label} by ${mine.visitor_name} (${mine.ip}) in ${mine.ms} ms`);
});

await step("the administration register shows the visitor and the activity", async () => {
  await page.goto(`${base}/#/admin`, { waitUntil: "networkidle" });
  await page.locator("button.mtab", { hasText: "Visitors" }).first().dispatchEvent("click");
  await page.waitForSelector("#admin-visitors table.data", { timeout: 20000 });
  await page.waitForTimeout(800);
  const body = await page.locator(".projects-page").innerText();
  for (const want of [name, "Divisional Engineer", "Created a project", ip]) {
    if (!body.includes(want)) throw new Error(`the register does not show "${want}"`);
  }
  await shot("visitor-02-register.png");
});

await page.evaluate((id) => fetch(`/api/projects/${id}`, { method: "DELETE" }), pid);  // tidy up
await browser.close();
if (errors.length) { console.error("browser errors:\n" + errors.join("\n")); process.exit(1); }
console.log("PASS: visitor register and usage log");
