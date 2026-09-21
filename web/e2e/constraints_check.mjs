/**
 * Constraint workflow check: opens a project, runs "Detect constraints" (semi-automatic), accepts the
 * suggestions, builds the TIN, and verifies the run card reports a validated TIN with constraints and
 * rejected triangles; then switches the rejected-triangles layer on and screenshots.
 *   node e2e/constraints_check.mjs http://127.0.0.1:8000 <projectId>
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const base = process.argv[2] || "http://127.0.0.1:8000";
const pid = process.argv[3];
if (!pid) { console.error("usage: node e2e/constraints_check.mjs <baseUrl> <projectId>"); process.exit(2); }
const outDir = new URL("./screenshots/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
await page.addInitScript(() => { window.__plmNoVisitorForm = true; });  // the visitor form must not block the check
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
const step = async (name, fn) => { process.stdout.write(`- ${name} ... `); await fn(); console.log("ok"); };
const shot = async (name) => { await page.screenshot({ path: `${outDir}${name}.png` }); };
const waitIdle = () => page.waitForFunction(() => !document.querySelector(".busy") || getComputedStyle(document.querySelector(".busy")).display === "none", null, { timeout: 180000 });

await step("open workspace", async () => {
  await page.goto(`${base}/#/p/${pid}`, { waitUntil: "networkidle" });
  await page.waitForSelector("#cesium canvas", { timeout: 60000 });
  await waitIdle();
  await page.waitForTimeout(2000);
});

await step("TIN tab, semi-automatic detect", async () => {
  await page.click('.tabs button:has-text("TIN")');
  await page.waitForSelector("select");
  const modeSel = page.locator("select").filter({ has: page.locator('option[value="semi"]') }).first();
  await modeSel.selectOption("semi");
  await page.click('button:has-text("Detect constraints")');
  await waitIdle();
  await page.waitForSelector('button:has-text("Accept selected")', { timeout: 60000 });
  const n = await page.locator('label.check input[type=checkbox]').count();
  console.log(`\n    ${n} suggestion checkbox(es) shown`);
  await shot(`constraints-${pid}-suggestions`);
  await page.click('button:has-text("Accept selected")');
  await page.waitForTimeout(1500);
});

await step("build TIN", async () => {
  await page.click('button:has-text("Build TIN")');
  await waitIdle();
  await page.waitForSelector(".card.clickable", { timeout: 120000 });
  await page.waitForTimeout(1500);
  const text = await page.locator(".card.clickable").first().innerText();
  console.log("\n    run card:", text.replace(/\s+/g, " ").slice(0, 400));
  if (!/validated/.test(text)) throw new Error("run card does not say 'validated'");
  if (!/boundary/.test(text)) throw new Error("run card has no constraints line");
});

await step("rejected triangles layer", async () => {
  const row = page.locator(".layer-panel").getByText("Rejected triangles", { exact: false }).first();
  await row.waitFor({ timeout: 10000 });
  const cb = row.locator("xpath=ancestor::*[self::label or self::div][1]//input[@type='checkbox']").first();
  await cb.check();
  await page.waitForTimeout(3000);
  await shot(`constraints-${pid}-rejected`);
});

await step("Data tab constraint list", async () => {
  await page.click('.tabs button:has-text("Data")');
  await page.waitForTimeout(1000);
  const has = await page.getByText("Constraint lines").count();
  if (!has) throw new Error("Constraint lines section missing");
  await shot(`constraints-${pid}-data`);
});

await browser.close();
if (errors.length) { console.log("page errors:", errors); process.exit(1); }
console.log("PASS: constraint workflow");
