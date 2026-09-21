/**
 * The triangulation as a layer: switching "Triangle edges" on builds one LINES primitive per mesh
 * part with two endpoints per triangle side, the edges stay visible when the shaded surface is
 * switched off, and switching them off again hides them.
 *   node e2e/tin_edges_check.mjs http://127.0.0.1:8000 <projectId>
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const base = process.argv[2] || "http://127.0.0.1:8000";
const pid = process.argv[3];
if (!pid) { console.error("usage: node e2e/tin_edges_check.mjs <baseUrl> <projectId>"); process.exit(2); }
const outDir = new URL("./screenshots/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
await page.addInitScript(() => { window.__plmNoVisitorForm = true; });
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
const step = async (name, fn) => { process.stdout.write(`- ${name} ... `); await fn(); console.log("ok"); };
const idle = () => page.waitForFunction(() => !document.querySelector(".busy") || getComputedStyle(document.querySelector(".busy")).display === "none", null, { timeout: 180000 });

/** What the map is actually holding: surface parts, wire parts, and the line count. */
const state = () => page.evaluate(() => {
  const L = window.__plm.layers;
  return {
    surfaces: L.tinParts.length,
    wires: L.tinWires.length,
    surfaceShown: L.tinParts.every((p) => p.show),
    wiresShown: L.tinWires.length > 0 && L.tinWires.every((p) => p.show),
    layers: { tin: window.__plmStore?.tin, },
  };
});

/** Toggle a layer through the layer tree, the way a user does. */
async function toggle(label, on) {
  const row = page.locator(".layer-item", { hasText: label }).first();
  const box = row.locator('input[type="checkbox"]').first();
  if ((await box.isChecked()) !== on) await box.click();
  await page.waitForTimeout(400);
  await idle();
  await page.waitForTimeout(1200);
}

await step("open the terrain workspace", async () => {
  await page.goto(`${base}/#/p/${pid}`, { waitUntil: "networkidle" });
  await page.waitForSelector("#cesium canvas", { timeout: 60000 });
  await idle();
  await page.waitForTimeout(3000);
  const s = await state();
  if (!s.surfaces) throw new Error("no TIN surface loaded - does this project have a TIN run?");
  if (s.wires) throw new Error("the triangulation was built before it was asked for");
  console.log(`\n    ${s.surfaces} surface part(s), ${s.wires} wire part(s) before switching on`);
});

await step("the layer tree offers Triangle edges", async () => {
  const row = page.locator(".layer-item", { hasText: "Triangle edges" }).first();
  if (!(await row.count())) throw new Error("no 'Triangle edges' entry in the layer tree");
  const count = (await row.innerText()).trim();
  console.log(`\n    ${count.replace(/\s+/g, " ")}`);
});

await step("switching it on builds the real triangulation", async () => {
  await toggle("Triangle edges", true);
  const s = await state();
  if (!s.wires) throw new Error("no wire primitive was built");
  if (!s.wiresShown) throw new Error("the wire primitive is hidden");
  // two endpoints per side, three sides per triangle, against the run's own triangle count
  const check = await page.evaluate(async () => {
    const ws = window.__plm;
    const run = ws.layers.tinParts.length;
    const runs = await fetch(`/api/projects/${ws.project.id}/tin`).then((r) => r.json());
    const cur = runs[runs.length - 1];
    return { parts: run, triangles: cur.n_triangles, lines: ws.layers.tinEdgeLines };
  });
  if (check.lines !== check.triangles * 3) {
    throw new Error(`the wireframe has ${check.lines} lines, expected ${check.triangles * 3} (3 per triangle)`);
  }
  console.log(`\n    ${check.triangles} triangles -> ${check.lines} edge lines across ${check.parts} part(s)`);
  await page.screenshot({ path: `${outDir}tin-edges-on.png`, animations: "disabled", timeout: 60000 });
});

await step("the edges remain when the shaded surface is switched off", async () => {
  await toggle("TIN surface", false);
  const s = await state();
  if (s.surfaceShown) throw new Error("the surface is still shown");
  if (!s.wiresShown) throw new Error("switching the surface off also hid the triangulation");
  await page.screenshot({ path: `${outDir}tin-edges-only.png`, animations: "disabled", timeout: 60000 });
  await toggle("TIN surface", true);
});

await step("switching the edges off hides them again", async () => {
  await toggle("Triangle edges", false);
  const s = await state();
  if (s.wiresShown) throw new Error("the triangulation is still shown");
  if (!s.surfaceShown) throw new Error("the surface disappeared with it");
});

await step("the surface style no longer offers the broken wire mode", async () => {
  const opts = await page.locator(".layer-sub select.mini").first().locator("option").allInnerTexts();
  if (opts.some((o) => o.trim() === "wire")) throw new Error(`the old wire style is still offered: ${opts.join(", ")}`);
  console.log(`\n    surface styles: ${opts.map((o) => o.trim()).join(", ")}`);
});

await browser.close();
if (errors.length) { console.error("browser errors:\n" + errors.join("\n")); process.exit(1); }
console.log("PASS: TIN triangulation layer");
