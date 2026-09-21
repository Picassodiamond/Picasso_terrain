/**
 * Module hand-off check: terrain workspace shows the module switcher, an alignment can be opened in
 * Road design, the road workspace renders plan / profile / cross-section from the terrain snapshot,
 * the switcher returns to the terrain workspace. Screenshots to e2e/screenshots/.
 *   node e2e/modules_check.mjs http://127.0.0.1:8000 <projectId>
 * The project needs a TIN run; an alignment is created through the API when none exists.
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const base = process.argv[2] || "http://127.0.0.1:8000";
const pid = process.argv[3];
if (!pid) { console.error("usage: node e2e/modules_check.mjs <baseUrl> <projectId>"); process.exit(2); }
const outDir = new URL("./screenshots/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
mkdirSync(outDir, { recursive: true });

const j = async (url, init) => { const r = await fetch(base + url, init); if (!r.ok) throw new Error(`${url}: ${r.status} ${await r.text()}`); return r.json(); };
const project = await j(`/api/projects/${pid}`);
let alignments = await j(`/api/projects/${pid}/alignments`);
if (!alignments.length) {
  const [x0, y0, x1, y1] = project.summary.bounds;
  const body = { name: "Test road", start_chainage: 0, ips: [
    { x: x0 + (x1 - x0) * 0.1, y: y0 + (y1 - y0) * 0.2, radius: 0, label: "0" },
    { x: x0 + (x1 - x0) * 0.5, y: y0 + (y1 - y0) * 0.7, radius: 60, label: "1" },
    { x: x0 + (x1 - x0) * 0.9, y: y0 + (y1 - y0) * 0.4, radius: 0, label: "2" } ] };
  await j(`/api/projects/${pid}/alignments`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  alignments = await j(`/api/projects/${pid}/alignments`);
}
console.log(`project "${project.name}", ${alignments.length} alignment(s)`);

const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
await page.addInitScript(() => { window.__plmNoVisitorForm = true; });  // the visitor form must not block the check
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
page.on("dialog", (d) => d.accept("Playwright road design"));
const step = async (name, fn) => { process.stdout.write(`- ${name} ... `); await fn(); console.log("ok"); };
const idle = () => page.waitForFunction(() => !document.querySelector(".busy") || getComputedStyle(document.querySelector(".busy")).display === "none", null, { timeout: 180000 });

await step("terrain workspace with module switcher", async () => {
  await page.goto(`${base}/#/p/${pid}`, { waitUntil: "networkidle" });
  await page.waitForSelector("#cesium canvas", { timeout: 60000 });
  await idle();
  await page.waitForSelector(".module-switcher .mtab.active", { timeout: 20000 });
  const active = await page.locator(".module-switcher .mtab.active").innerText();
  if (!/Terrain/.test(active)) throw new Error(`active tab is ${active}`);
  await page.locator(".newdesign summary").click();
  await page.waitForSelector(".newdesign .menu-item", { timeout: 5000 });
  const items = await page.locator(".newdesign .menu-item").count();
  if (items < 3) throw new Error("module menu incomplete");
  await page.screenshot({ path: `${outDir}modules-${pid}-terrain.png` });
  await page.keyboard.press("Escape");
  await page.mouse.click(700, 500);
});

await step("open alignment in Road design", async () => {
  await page.click('.tabs button:has-text("Alignment")');
  await page.waitForSelector(".card.clickable", { timeout: 10000 });
  await page.click(".card.clickable");
  await page.waitForSelector('button:has-text("Open in Road design")', { timeout: 10000 });
  await page.click('button:has-text("Open in Road design")');
  await page.waitForFunction(() => /#\/p\/[a-f0-9]+\/road\/\d+/.test(location.hash), null, { timeout: 20000 });
});

await step("road workspace renders", async () => {
  await page.waitForSelector(".road-workspace", { timeout: 30000 });
  await page.waitForSelector(".plan-canvas", { timeout: 30000 });
  await page.waitForSelector(".road-profile-body svg", { timeout: 60000 });
  await page.waitForSelector(".road-section-body svg", { timeout: 60000 });
  await page.waitForTimeout(1500);
  const stages = await page.locator(".stage").count();
  if (stages < 7) throw new Error(`expected 7 stages, got ${stages}`);
  const badge = await page.locator(".stage .badge").first().innerText();
  if (!/seeded/.test(badge)) throw new Error(`alignment stage badge: ${badge}`);
  const active = await page.locator(".module-switcher .mtab.active").innerText();
  if (/Terrain/.test(active)) throw new Error("switcher still shows terrain as active");
  // hover the plan: status shows chainage and offset
  const canvas = await page.$(".plan-canvas");
  const box = await canvas.boundingBox();
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await page.waitForTimeout(600);
  const status = await page.locator(".road-status").innerText();
  console.log(`\n    status: ${status.replace(/\s+/g, " ")}`);
  if (!/CH /.test(status)) throw new Error("status bar has no chainage");
  await page.click('.road-right button:has-text("▶")');
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${outDir}modules-${pid}-road.png` });
  // stage panel: profile stage shows the planned notice
  await page.locator(".stage").nth(1).click();
  await page.waitForTimeout(300);
});

await step("switch back to terrain", async () => {
  await page.locator(".module-switcher .mtab", { hasText: "Terrain" }).click();
  await page.waitForSelector("#cesium canvas", { timeout: 60000 });
  await idle();
  await page.waitForSelector(".module-switcher .mtab.active", { timeout: 20000 });
  const tabs = await page.locator(".module-switcher .mtab").count();
  if (tabs < 3) throw new Error("design tab missing after return");
});

await browser.close();
if (errors.length) { console.log("page errors:", errors); process.exit(1); }
console.log("PASS: module hand-off");
