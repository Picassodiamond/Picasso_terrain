/**
 * Screenshots for the user help pages (web/public/help/img). Re-run after UI changes.
 *   node e2e/help_shots.mjs http://127.0.0.1:8000 <terrainProjectId> <roadProjectId> [roadDesignId]
 * The terrain project should be georeferenced with points, constraint lines, a TIN, contours, an
 * alignment and a section set; the road project needs a road design with a built corridor.
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const base = process.argv[2] || "http://127.0.0.1:8000";
const tpid = process.argv[3];
const rpid = process.argv[4] || tpid;
let did = process.argv[5] ? Number(process.argv[5]) : null;
if (!tpid) { console.error("usage: node e2e/help_shots.mjs <baseUrl> <terrainProjectId> [roadProjectId] [roadDesignId]"); process.exit(2); }
const outDir = new URL("../public/help/img/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
await page.addInitScript(() => { window.__plmNoVisitorForm = true; });  // the visitor form must not block the check
page.on("dialog", (d) => d.accept());
const idle = () => page.waitForFunction(() => !document.querySelector(".busy") || getComputedStyle(document.querySelector(".busy")).display === "none", null, { timeout: 180000 });
const settle = (ms = 1200) => page.waitForTimeout(ms);
const shot = async (name, opts = {}) => {
  const path = `${outDir}${name}.jpg`;
  try {
    // animations off + long timeout: spinners and the 3-D map never look "stable" to Playwright otherwise
    if (opts.el) await page.locator(opts.el).first().screenshot({ path, type: "jpeg", quality: 84, animations: "disabled", timeout: 60000 });
    else await page.screenshot({ path, type: "jpeg", quality: 82, animations: "disabled", timeout: 60000, ...(opts.clip ? { clip: opts.clip } : {}) });
    console.log("wrote", name);
  } catch (e) { console.log("SKIP", name, "-", e.message.split("\n")[0]); }
};
// dispatchEvent bypasses Playwright's actionability wait (the 3-D map animates continuously)
const tab = async (label) => { await page.locator(`.tabs button:has-text("${label}")`).first().dispatchEvent("click"); await settle(900); };
const stage = async (label) => { await page.locator(".stage", { hasText: label }).first().dispatchEvent("click"); await settle(1000); };
const tryStep = async (name, fn) => { try { await fn(); } catch (e) { console.log("SKIP step", name, "-", e.message.split("\n")[0]); } };

// ---------------------------------------------------------------- projects and library
await page.goto(`${base}/#/`, { waitUntil: "networkidle" });
await page.waitForSelector(".project-card", { timeout: 60000 });
await settle(800);
await shot("projects");
await tryStep("library", async () => {
  await page.goto(`${base}/#/library`, { waitUntil: "networkidle" });
  await page.waitForSelector(".lib-layout", { timeout: 30000 });
  await settle(2500);
  await shot("library");
});

// ---------------------------------------------------------------- terrain workspace
await page.goto(`${base}/#/p/${tpid}`, { waitUntil: "networkidle" });
await page.waitForSelector("#cesium canvas", { timeout: 60000 });
await idle();
await settle(7000);                        // base map tiles
await shot("terrain-overview");
await shot("terrain-layers", { el: ".layer-panel" });
await tryStep("switcher", async () => {
  await page.locator(".newdesign summary").click();
  await page.waitForSelector(".newdesign .menu-item", { timeout: 5000 });
  await settle(400);
  await shot("module-switcher", { clip: { x: 0, y: 0, width: 1440, height: 300 } });
  await page.evaluate(() => document.querySelector("details.newdesign")?.removeAttribute("open"));
  await settle(300);
});
await tab("Data");
await shot("terrain-data", { el: ".sidebar" });
await tab("TIN");
await shot("terrain-tin", { el: ".sidebar" });
await tab("Contours");
await shot("terrain-contours", { el: ".sidebar" });
await tab("Alignment");
await tryStep("open alignment card", async () => { await page.locator(".sidebar .card.clickable").first().dispatchEvent("click"); await settle(1200); });
await shot("terrain-alignment", { el: ".sidebar" });
await shot("terrain-alignment-map");
await tab("Sections");
await tryStep("open section set", async () => {
  const c = page.locator(".sidebar .card.clickable").first();
  if (await c.count()) { await c.dispatchEvent("click"); await idle(); await settle(2000); }
});
await shot("terrain-sections");
await tab("Export");
await shot("terrain-export", { el: ".sidebar" });
await tab("Team");
await shot("terrain-team", { el: ".sidebar" });
await tab("Settings");
await shot("terrain-settings", { el: ".sidebar" });
await tryStep("rejected layer", async () => {
  await tab("TIN");
  const row = page.locator(".layer-panel").getByText("Rejected triangles", { exact: false }).first();
  await row.click();
  await settle(1500);
  await shot("terrain-rejected");
  await row.click();
});

// ---------------------------------------------------------------- road design workspace
if (!did) {
  const designs = await (await fetch(`${base}/api/projects/${rpid}/designs`)).json();
  const roads = designs.filter((d) => d.module === "road");
  did = roads.length ? roads[roads.length - 1].id : null;
}
if (did) {
  await page.goto(`${base}/#/p/${rpid}/road/${did}`, { waitUntil: "networkidle" });
  await page.waitForSelector(".road-workspace .plan-canvas", { timeout: 60000 });
  await idle();
  await settle(2500);
  await shot("road-alignment");
  await shot("road-side-alignment", { el: ".road-side" });
  await shot("road-checks", { el: ".checks" });
  await shot("road-section-panel", { el: ".road-right" });
  await shot("road-profile-chart", { el: ".road-profile" });
  await stage("Profile");
  await shot("road-profile");
  await stage("Templates");
  await shot("road-templates");
  await shot("road-side-templates", { el: ".road-side" });
  await stage("Earthworks");
  await settle(600);
  await shot("road-earthworks");
  await shot("road-side-earthworks", { el: ".road-side" });
  await stage("Structures");
  await tryStep("wall suggestions", async () => { await page.click('.road-side button:has-text("Suggest")'); await idle(); await settle(1500); });
  await shot("road-structures");
  await shot("road-side-structures", { el: ".road-side" });
  await tryStep("quantities", async () => {
    await page.evaluate(() => { const e = document.querySelector(".road-side"); if (e) e.scrollTop = e.scrollHeight; });
    await settle(500);
    await shot("road-side-quantities", { el: ".road-side" });
  });
  await stage("Drainage");
  await tryStep("drain suggestions", async () => { await page.click('.road-side button:has-text("Suggest")'); await idle(); await settle(1500); });
  await shot("road-drainage", { el: ".road-side" });
  await stage("Output");
  await tryStep("output sheets", async () => {
    await page.waitForSelector(".sheet-row .chip", { timeout: 120000 });
    await settle(500);
    await shot("road-output");
    await shot("road-side-output", { el: ".road-side" });
    await page.click('button:has-text("Preview drawings")');
    await page.waitForSelector(".sheet-preview svg", { timeout: 120000 });
    await settle(800);
    await shot("sheet-plan");
    // zoom into the plan
    const vp = await page.locator(".sheet-viewport").boundingBox();
    await page.mouse.move(vp.x + vp.width * 0.55, vp.y + vp.height * 0.5);
    for (let i = 0; i < 4; i++) { await page.mouse.wheel(0, -200); await settle(120); }
    await settle(500);
    await shot("sheet-plan-zoom");
    await page.locator(".sheet-item", { hasText: "RD-L-01" }).first().click();
    await page.waitForFunction(() => document.querySelector(".sheet-preview svg")?.getAttribute("data-kind") === "profile", null, { timeout: 120000 });
    await settle(600);
    await shot("sheet-profile");
    await page.locator(".sheet-item", { hasText: "RD-X-01" }).first().click();
    await page.waitForFunction(() => document.querySelector(".sheet-preview svg")?.getAttribute("data-kind") === "sections", null, { timeout: 120000 });
    await settle(600);
    await shot("sheet-sections");
    await page.keyboard.press("Escape");
  });
} else console.log("no road design found: road screenshots skipped");

// ---------------------------------------------------------------- visitor register
// a second context with an unused address, and without the flag above, so the introduction form
// really comes up for the picture
await tryStep("visitor form", async () => {
  const ip = `203.0.113.${1 + Math.floor(Math.random() * 250)}`;
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, extraHTTPHeaders: { "X-Forwarded-For": ip } });
  const vp = await ctx.newPage();
  vp.on("dialog", (d) => d.accept());
  await vp.goto(`${base}/`, { waitUntil: "networkidle" });
  await vp.waitForSelector(".overlay .dialog", { timeout: 30000 });
  const fill = async (label, value) => vp.locator(".overlay label.field", { hasText: label }).locator("input").first().fill(value);
  await fill("Name", "Anjana Poudel");
  await fill("Email", "anjana.poudel@dor.gov.np");
  await fill("Phone", "9841234567");
  await fill("Designation", "Divisional Engineer");
  await fill("Organisation", "Department of Roads, Pokhara");
  await fill("District", "Kaski");
  await fill("What will you use it for", "Rural road alignment and earthworks");
  await vp.waitForTimeout(500);
  await vp.screenshot({ path: `${outDir}visitor-form.jpg`, type: "jpeg", quality: 84, animations: "disabled" });
  console.log("wrote visitor-form");
  await vp.locator(".overlay button", { hasText: "Not now" }).first().dispatchEvent("click");
  await ctx.close();
});

await tryStep("visitor register", async () => {
  await page.goto(`${base}/#/admin`, { waitUntil: "networkidle" });
  await page.locator("button.mtab", { hasText: "Visitors" }).first().dispatchEvent("click");
  await page.waitForSelector("#admin-visitors table.data", { timeout: 30000 });
  await settle(1200);
  await shot("visitor-register");
});

await browser.close();
console.log("done ->", outDir);
