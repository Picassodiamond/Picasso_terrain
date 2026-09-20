/**
 * Base map check: opens a project workspace and verifies that the base map selector is enabled,
 * that OpenStreetMap is selected, and that OSM tiles are actually fetched with HTTP 200.
 *   node e2e/basemap_check.mjs http://127.0.0.1:8000 <projectId>
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const base = process.argv[2] || "http://127.0.0.1:8000";
const pid = process.argv[3];
if (!pid) { console.error("usage: node e2e/basemap_check.mjs <baseUrl> <projectId>"); process.exit(2); }
const outDir = new URL("./screenshots/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
const tiles = { ok: 0, failed: 0, urls: new Set() };
page.on("response", (r) => {
  const u = r.url();
  if (u.includes("tile.openstreetmap.org")) { if (r.status() === 200) tiles.ok++; else tiles.failed++; tiles.urls.add(u); }
});
page.on("requestfailed", (r) => { if (r.url().includes("tile.openstreetmap.org")) tiles.failed++; });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));

await page.goto(`${base}/#/p/${pid}`, { waitUntil: "networkidle" });
await page.waitForSelector("#cesium canvas", { timeout: 60000 });
await page.waitForFunction(() => !document.querySelector(".busy") || getComputedStyle(document.querySelector(".busy")).display === "none", null, { timeout: 120000 });
await page.waitForTimeout(8000);

const sel = await page.$$eval("select", (els) => els.map((s) => ({ value: s.value, disabled: s.disabled, options: [...s.options].map((o) => o.value) })))
  .then((all) => all.find((s) => s.options.includes("osm")));
await page.screenshot({ path: `${outDir}basemap-${pid}.png` });

// height placement: report the selector, hover the TIN to read the RL, then switch to true elevation
const heightSel = await page.$('select[title^="On base map"]');
if (heightSel) {
  const canvas = await page.$("#cesium canvas");
  const box = await canvas.boundingBox();
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await page.waitForTimeout(500);
  console.log("height mode:", await heightSel.evaluate((s) => s.value), "| status RL readout:", await page.$eval(".statusbar .mono", (e) => e.textContent));
  await heightSel.selectOption("true");
  await page.waitForTimeout(6000);
  await page.screenshot({ path: `${outDir}basemap-${pid}-true-elevation.png` });
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await page.waitForTimeout(500);
  console.log("height mode:", await heightSel.evaluate((s) => s.value), "| status RL readout:", await page.$eval(".statusbar .mono", (e) => e.textContent));
  await heightSel.selectOption("ground");
  await page.waitForTimeout(3000);
} else console.log("height selector: NOT FOUND");

console.log("base map selector:", sel ? `value=${sel.value} disabled=${sel.disabled}` : "NOT FOUND");
console.log(`osm tiles: ${tiles.ok} ok, ${tiles.failed} failed, ${tiles.urls.size} distinct`);
if (errors.length) console.log("page errors:", errors);
await browser.close();

const pass = sel && !sel.disabled && sel.value === "osm" && tiles.ok > 0 && errors.length === 0;
console.log(pass ? "PASS: base map available" : "FAIL");
process.exit(pass ? 0 : 1);
