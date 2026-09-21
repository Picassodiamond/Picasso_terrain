/**
 * Browser smoke test with Playwright (Chromium, software WebGL).
 *   node e2e/smoke.mjs http://127.0.0.1:8011 <projectId>
 * Loads the workspace, exercises every panel, the charts pane and the drawing tool, and fails on
 * page errors. Screenshots go to e2e/screenshots/.
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const base = process.argv[2] || "http://127.0.0.1:8011";
const pid = process.argv[3];
const outDir = new URL("./screenshots/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
await page.addInitScript(() => { window.__plmNoVisitorForm = true; });  // the visitor form must not block the check
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
page.on("requestfailed", (r) => { if (!r.url().includes("tile.")) errors.push(`request failed: ${r.url()} ${r.failure()?.errorText}`); });

const shot = async (name) => { await page.screenshot({ path: `${outDir}${name}.png` }); console.log("screenshot", name); };
const step = async (name, fn) => { process.stdout.write(`- ${name} ... `); await fn(); console.log("ok"); };

await step("projects page", async () => {
  await page.goto(base + "/", { waitUntil: "networkidle" });
  await page.waitForSelector(".project-card, .projects-page", { timeout: 30000 });
  await shot("01-projects");
});

await step("open workspace", async () => {
  await page.goto(`${base}/#/p/${pid}`, { waitUntil: "networkidle" });
  await page.waitForSelector("#cesium canvas", { timeout: 60000 });
  await page.waitForFunction(() => !document.querySelector(".busy") || getComputedStyle(document.querySelector(".busy")).display === "none", null, { timeout: 120000 });
  await page.waitForTimeout(4000);
  await shot("02-workspace-data");
});

const tab = async (label) => { await page.click(`.tabs button:has-text("${label}")`); await page.waitForTimeout(600); };

await step("TIN tab", async () => { await tab("TIN"); await page.waitForSelector(".card"); await shot("03-tin"); });
await step("Contours tab", async () => { await tab("Contours"); await shot("04-contours"); });
await step("Alignment tab", async () => {
  await tab("Alignment");
  await page.waitForSelector(".card.clickable", { timeout: 10000 });
  await page.click(".card.clickable");
  await page.waitForTimeout(1500);
  await shot("05-alignment");
});
await step("Sections tab + charts", async () => {
  await tab("Sections");
  await page.waitForSelector(".card.clickable", { timeout: 10000 });
  await page.click(".card.clickable");
  await page.waitForTimeout(1500);
  await page.waitForSelector(".chart-body svg", { timeout: 15000 });
  const n = await page.$$eval(".chart-body svg", (s) => s.length);
  if (n < 2) throw new Error("expected profile and section charts");
  await page.keyboard.press("ArrowRight");
  await page.waitForTimeout(500);
  await shot("06-sections-charts");
});
await step("Export tab", async () => { await tab("Export"); await shot("07-export"); });
await step("Team tab", async () => {
  await tab("Team");
  await page.fill("textarea", "Smoke-test comment from Playwright");
  await page.click('button:has-text("Post comment")', { noWaitAfter: true });
  await page.waitForSelector(".comment", { timeout: 10000 });
  await shot("08-team");
});
await step("Settings tab", async () => { await tab("Settings"); await shot("09-settings"); });
await step("draw alignment tool", async () => {
  await tab("Alignment");
  await page.click('button:has-text("New alignment")');
  await page.waitForSelector(".tool-hint");
  const canvas = await page.$("#cesium canvas");
  const box = await canvas.boundingBox();
  for (const [fx, fy] of [[0.3, 0.6], [0.5, 0.45], [0.7, 0.55]]) {
    await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
    await page.waitForTimeout(400);
  }
  await page.waitForTimeout(1200);
  const rows = await page.$$eval("table.data tr", (r) => r.length - 1);
  if (rows < 3) throw new Error(`expected 3 IPs in the table, got ${rows}`);
  await shot("10-draw-alignment");
  await page.click('button:has-text("Cancel")');
});
await step("2D mode + basemap", async () => {
  await page.selectOption(".topbar select >> nth=1", "2D");
  await page.waitForTimeout(3500);
  await shot("11-2d");
});

await browser.close();
const real = errors.filter((e) => !/favicon|ERR_ABORTED|net::ERR_FAILED.*tile/.test(e));
if (real.length) {
  console.error("\nERRORS:\n" + real.join("\n"));
  process.exit(1);
}
console.log("\nsmoke test passed with no page errors");
