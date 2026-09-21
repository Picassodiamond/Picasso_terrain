/**
 * Road design check: opens a road design, verifies the editable IP table, saves an IP change (plan
 * geometry updates), fits a vertical alignment to the ground (PVIs appear in the profile), edits a
 * template assignment, builds the corridor (quantities and mass haul appear, section shows the
 * design line) and asks for structure suggestions. Screenshots to e2e/screenshots/.
 *   node e2e/road_check.mjs http://127.0.0.1:8000 <projectId> [designId]
 * Without a designId the newest road design of the project is used (or created from the first alignment).
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const base = process.argv[2] || "http://127.0.0.1:8000";
const pid = process.argv[3];
let did = process.argv[4] ? Number(process.argv[4]) : null;
if (!pid) { console.error("usage: node e2e/road_check.mjs <baseUrl> <projectId> [designId]"); process.exit(2); }
const outDir = new URL("./screenshots/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
mkdirSync(outDir, { recursive: true });
const j = async (url, init) => { const r = await fetch(base + url, init); if (!r.ok) throw new Error(`${url}: ${r.status} ${await r.text()}`); return r.json(); };

if (!did) {
  const designs = (await j(`/api/projects/${pid}/designs`)).filter((d) => d.module === "road");
  if (designs.length) did = designs[designs.length - 1].id;
  else {
    const als = await j(`/api/projects/${pid}/alignments`);
    if (!als.length) { console.error("project needs an alignment"); process.exit(2); }
    did = (await j(`/api/projects/${pid}/designs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ module: "road", name: "Road check", alignment_id: als[0].id }) })).id;
  }
}
console.log(`project ${pid}, road design ${did}`);

const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 1600, height: 950 } });
await page.addInitScript(() => { window.__plmNoVisitorForm = true; });  // the visitor form must not block the check
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
page.on("dialog", (d) => d.accept());
const step = async (name, fn) => { process.stdout.write(`- ${name} ... `); await fn(); console.log("ok"); };
const idle = () => page.waitForFunction(() => !document.querySelector(".busy") || getComputedStyle(document.querySelector(".busy")).display === "none", null, { timeout: 180000 });
const stage = async (label) => { await page.locator(".stage", { hasText: label }).first().click(); await page.waitForTimeout(500); };

await step("open road workspace", async () => {
  await page.goto(`${base}/#/p/${pid}/road/${did}`, { waitUntil: "networkidle" });
  await page.waitForSelector(".road-workspace .plan-canvas", { timeout: 60000 });
  await idle();
  await page.waitForSelector("table.data", { state: "attached", timeout: 30000 });
  await page.waitForTimeout(1500);
});

await step("alignment: IP table, checks and curve table", async () => {
  await stage("Alignment");
  const ipTable = page.locator(".card", { hasText: "Horizontal alignment" }).locator("table.data").first();
  const rows = await ipTable.locator("tr").count();
  if (rows < 3) throw new Error("IP table missing");
  const hasChecks = await page.locator(".check-row").count();
  console.log(`\n    ${hasChecks} check rows`);
  // change the middle IP radius through the table and save
  const rIn = ipTable.locator("tr").nth(2).locator("input").nth(2);
  await rIn.fill("250");
  await rIn.dispatchEvent("change");
  await page.waitForTimeout(800);
  await page.click('button:has-text("Save alignment")');
  await idle();
  await page.waitForTimeout(1000);
  const tblTxt = await page.locator("details.card", { hasText: "Curve table" }).locator("table.data").first().innerText().catch(() => "");
  if (!/250/.test(tblTxt)) throw new Error(`curve table does not show the new radius: ${tblTxt.slice(0, 120)}`);
  const follow = await page.locator('select[data-follow="vertical"]').inputValue();
  console.log(`\n    grade line follows the alignment: ${follow}`);
  if (!follow) throw new Error("follow selector missing");
  await page.screenshot({ path: `${outDir}road-${pid}-alignment.png` });
});

await step("profile: fit to ground and PVIs", async () => {
  await stage("Profile");
  await page.click('button:has-text("Fit to ground")');
  await idle();
  await page.waitForSelector(".road-profile-body .pvi", { timeout: 30000 });
  const n = await page.locator(".road-profile-body .pvi").count();
  if (n < 2) throw new Error("no PVIs drawn");
  const grades = await page.locator(".road-profile-body .grade-label").count();
  if (!grades) throw new Error("no grade labels");
  console.log(`\n    ${n} PVIs, ${grades} grade labels`);
  await page.screenshot({ path: `${outDir}road-${pid}-profile.png` });
});

await step("alignment change: stale grade line banner and one-click stretch", async () => {
  await stage("Alignment");
  const sel = page.locator('select[data-follow="vertical"]');
  await sel.selectOption("keep");
  await page.waitForTimeout(600);
  const ipTable = page.locator(".card", { hasText: "Horizontal alignment" }).locator("table.data").first();
  const rIn = ipTable.locator("tr").nth(2).locator("input").nth(2);
  await rIn.fill("240");
  await rIn.dispatchEvent("change");
  await page.waitForTimeout(600);
  await page.click('button:has-text("Save alignment")');
  await idle();
  await page.waitForTimeout(1000);
  await stage("Profile");
  await page.waitForSelector(".stale-banner", { timeout: 15000 });
  const reason = await page.locator(".stale-banner").innerText();
  console.log(`\n    banner: ${reason.replace(/\s+/g, " ").slice(0, 110)}`);
  await page.click('.stale-banner button:has-text("Stretch to new length")');
  await idle();
  await page.waitForSelector(".stale-banner", { state: "detached", timeout: 15000 });
  await page.screenshot({ path: `${outDir}road-${pid}-profile-followed.png` });
  await stage("Alignment");
  await page.locator('select[data-follow="vertical"]').selectOption("stretch");
  await page.waitForTimeout(600);
});

await step("templates: add an assignment and save", async () => {
  await stage("Templates");
  await page.click('button:has-text("Add range")');
  await page.click('button:has-text("Save templates")');
  await idle();
  await page.waitForTimeout(800);
});

await step("earthworks: build corridor", async () => {
  await stage("Earthworks");
  await page.click('button:has-text("Build corridor")');
  await idle();
  await page.waitForSelector(".masshaul svg", { timeout: 120000 });
  const q = await page.locator(".mini-kv").first().innerText();
  console.log(`\n    ${q.replace(/\s+/g, " ").slice(0, 160)}`);
  if (!/cut/.test(q) || !/fill/.test(q)) throw new Error("quantities missing");
  await page.waitForSelector(".road-section-body .design-line", { timeout: 30000 });
  await page.screenshot({ path: `${outDir}road-${pid}-earthworks.png` });
});

await step("structures: suggestions", async () => {
  await stage("Structures");
  await page.click('button:has-text("Suggest")');
  await page.waitForTimeout(2500);
  const txt = await page.locator(".road-side").innerText();
  if (!/Suggest/.test(txt)) throw new Error("structures stage not rendered");
  await stage("Drainage");
  await page.click('button:has-text("Suggest")');
  await page.waitForTimeout(2000);
  await stage("Output");
  await page.waitForSelector('button:has-text("PVI table CSV")');
});

await step("structures appear on the cross-section", async () => {
  // find a chainage that actually has a wall or a drain, then look for it in the section pane
  const items = await page.evaluate(async () => {
    const ws = window.__plm;
    const all = (ws.data.structures?.structures) || [];
    if (!all.length) return null;
    const mid = (Number(all[0].from) + Number(all[0].to)) / 2;
    await ws.showSection(mid);
    await new Promise((r) => setTimeout(r, 1500));
    const svg = document.querySelector(".road-section-body svg");
    return {
      chainage: ws.station,
      stored: all.length,
      walls: svg ? svg.querySelectorAll("path.structure-wall").length : -1,
      foundations: svg ? svg.querySelectorAll("path.structure-foundation").length : -1,
      drains: svg ? svg.querySelectorAll("path.structure-drain").length : -1,
      labels: svg ? Array.from(svg.querySelectorAll("text.structure-label")).map((t) => t.textContent) : [],
    };
  });
  if (!items) { console.log("\n    no structures stored - skipped"); return; }
  if (items.walls + items.drains < 1) {
    throw new Error(`nothing drawn on the section at CH ${items.chainage} (${items.stored} structures stored)`);
  }
  console.log(`\n    CH ${items.chainage}: ${items.walls} wall(s), ${items.foundations} foundation(s), ${items.drains} drain(s) - ${items.labels.join(", ")}`);
  await page.screenshot({ path: `${outDir}road-section-structures.png`, animations: "disabled" });
});

await step("output: drawing sheets, preview and settings", async () => {
  await stage("Output");
  await page.waitForSelector(".sheet-row .chip", { timeout: 90000 });
  const chips = await page.locator(".sheet-row .chip").count();
  if (chips < 3) throw new Error(`expected plan + profile + section sheets, got ${chips} chips`);
  console.log(`\n    ${chips} sheet chips`);
  await page.click('button:has-text("Preview drawings")');
  await page.waitForSelector(".sheet-preview svg", { timeout: 90000 });
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${outDir}road-${pid}-sheet-plan.png` });
  // longitudinal section and a cross-section sheet through the list
  await page.locator(".sheet-item", { hasText: "RD-L-01" }).first().click();
  await page.waitForFunction(() => document.querySelector(".sheet-preview svg")?.getAttribute("data-kind") === "profile", null, { timeout: 90000 });
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${outDir}road-${pid}-sheet-profile.png` });
  await page.locator(".sheet-item", { hasText: "RD-X-01" }).first().click();
  await page.waitForFunction(() => document.querySelector(".sheet-preview svg")?.getAttribute("data-kind") === "sections", null, { timeout: 90000 });
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${outDir}road-${pid}-sheet-sections.png` });
  await page.keyboard.press("Escape");
  await page.waitForSelector(".sheet-preview", { state: "detached", timeout: 5000 });
  // title block settings round-trip
  await page.click("details summary:has-text('Title block')");
  const org = page.locator(".road-side input[type=text]").first();
  await org.fill("Playwright Consult");
  await page.click('button:has-text("Save sheet settings")');
  await idle();
  await page.waitForTimeout(800);
  const val = await page.locator(".road-side input[type=text]").first().inputValue();
  if (val !== "Playwright Consult") throw new Error(`settings did not persist: ${val}`);
  await page.screenshot({ path: `${outDir}road-${pid}-output.png` });
});

await browser.close();
if (errors.length) { console.log("page errors:", errors); process.exit(1); }
console.log("PASS: road design workflow");
