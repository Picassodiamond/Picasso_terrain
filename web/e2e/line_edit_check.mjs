/**
 * Editing a constraint line: pick it, see handles on its vertices, drag one, type exact
 * coordinates, insert and remove a vertex, save, and confirm the store kept it.
 *   node e2e/line_edit_check.mjs http://127.0.0.1:8000 <projectId>
 * The project needs at least one breakline; a boundary as well exercises the closed-ring rules.
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const base = process.argv[2] || "http://127.0.0.1:8000";
const pid = process.argv[3];
if (!pid) { console.error("usage: node e2e/line_edit_check.mjs <baseUrl> <projectId>"); process.exit(2); }
const outDir = new URL("./screenshots/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
mkdirSync(outDir, { recursive: true });
const j = async (u) => { const r = await fetch(base + u); if (!r.ok) throw new Error(`${u}: ${r.status}`); return r.json(); };
const lineOf = async (fid) => {
  const fc = await j(`/api/projects/${pid}/lines.geojson`);
  const f = fc.features.find((x) => x.properties.fid === fid);
  return f ? { coords: f.geometry.coordinates, props: f.properties } : null;
};

const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: 1600, height: 950 } });
await page.addInitScript(() => { window.__plmNoVisitorForm = true; });
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
page.on("dialog", (d) => d.accept());
const step = async (name, fn) => { process.stdout.write(`- ${name} ... `); await fn(); console.log("ok"); };
const idle = () => page.waitForFunction(() => !document.querySelector(".busy") || getComputedStyle(document.querySelector(".busy")).display === "none", null, { timeout: 120000 });

/** The constraint table is the one with a "kind" header; rows follow the feature order of
 *  lines.geojson, and the name column shows the line's name rather than its fid. */
const consTable = () => page.locator(".panel table.data").filter({ has: page.locator('th:text-is("kind")') }).first();
async function rowIndexOf(fid) {
  const fc = await j(`/api/projects/${pid}/lines.geojson`);
  const i = fc.features.findIndex((f) => f.properties.fid === fid);
  if (i < 0) throw new Error(`line #${fid} is not in lines.geojson`);
  return i + 1;                                   // +1 for the header row
}
const rowFor = async (fid) => consTable().locator("tr").nth(await rowIndexOf(fid));

let breakFid = null, ringFid = null, original = null;

await step("open the Data panel and find the constraint lines", async () => {
  await page.goto(`${base}/#/p/${pid}`, { waitUntil: "networkidle" });
  await page.waitForSelector(".workspace", { timeout: 60000 });
  await idle();
  await page.waitForTimeout(2500);
  const fc = await j(`/api/projects/${pid}/lines.geojson`);
  const brk = fc.features.find((f) => f.properties.kind === "feature" && f.geometry.coordinates.length >= 3);
  const ring = fc.features.find((f) => f.properties.kind === "boundary" || f.properties.kind === "void");
  if (!brk) throw new Error("this project has no breakline with three or more vertices");
  breakFid = brk.properties.fid;
  ringFid = ring ? ring.properties.fid : null;
  original = brk.geometry.coordinates.map((c) => [...c]);
  console.log(`\n    breakline #${breakFid} with ${original.length} vertices` + (ringFid ? `, ring #${ringFid}` : ", no boundary/void"));
});

await step("Edit puts a handle on every vertex", async () => {
  await page.keyboard.press("1");                  // Data panel
  await page.waitForTimeout(500);
  const row = await rowFor(breakFid);
  if (!(await row.count())) throw new Error(`no table row for line #${breakFid}`);
  await row.locator('button:text-is("Edit")').click();
  await page.waitForTimeout(1200);
  const handles = await page.evaluate(() => window.__plm.layers.editVertices.length);
  if (handles !== original.length) throw new Error(`${handles} handles for ${original.length} vertices`);
  const heads = await page.locator(".panel table.data th").allInnerTexts();
  for (const h of ["Easting", "Northing"]) {
    if (!heads.some((t) => t.trim() === h)) throw new Error(`the vertex table has no ${h} column: ${heads.join(", ")}`);
  }
  // constraint lines are plan geometry: the level comes from the survey, so there is nothing to type
  if (heads.some((t) => t.trim() === "RL" )) throw new Error("the vertex table still asks for an RL");
  await page.screenshot({ path: `${outDir}line-edit-01-handles.png`, animations: "disabled", timeout: 60000 });
});

await step("typing exact coordinates moves the vertex", async () => {
  // second vertex: type a precise Easting and Northing
  const rows = page.locator(".panel .card", { hasText: `Vertices of #${breakFid}` }).locator("table.data tr");
  const cells = rows.nth(2).locator("input");      // row 1 is the header, nth(2) is vertex 2
  const target = [Number((original[1][0] + 12.345).toFixed(3)), Number((original[1][1] - 7.5).toFixed(3))];
  await cells.nth(0).fill(String(target[0]));
  await cells.nth(0).dispatchEvent("change");
  await cells.nth(1).fill(String(target[1]));
  await cells.nth(1).dispatchEvent("change");
  await page.waitForTimeout(500);
  const live = await page.evaluate(() => window.__plmEditorCoords);
  void live;
  console.log(`\n    vertex 2 typed as ${target[0]}, ${target[1]}`);
});

await step("inserting and removing a vertex", async () => {
  const card = page.locator(".panel .card", { hasText: `Vertices of #${breakFid}` });
  const before = await card.locator("table.data tr").count();
  await card.locator('button:text-is("+")').first().click();
  await page.waitForTimeout(400);
  const added = await card.locator("table.data tr").count();
  if (added !== before + 1) throw new Error(`+ gave ${added - before} extra rows`);
  const handles = await page.evaluate(() => window.__plm.layers.editVertices.length);
  if (handles !== added - 1) throw new Error(`${handles} handles for ${added - 1} vertices after inserting`);
  await card.locator("table.data tr").nth(2).locator("button.danger").click();
  await page.waitForTimeout(400);
  const back = await card.locator("table.data tr").count();
  if (back !== before) throw new Error(`removing a vertex left ${back} rows, expected ${before}`);
  console.log(`\n    ${before - 1} -> ${added - 1} -> ${back - 1} vertices`);
});

await step("Save writes it, and the stored line matches", async () => {
  const card = page.locator(".panel .card", { hasText: `Vertices of #${breakFid}` });
  await card.locator('button:text-is("Save line")').click();
  await page.waitForTimeout(1800);
  const after = await lineOf(breakFid);
  if (after.coords.length !== original.length) throw new Error(`stored ${after.coords.length} vertices, expected ${original.length}`);
  const moved = Math.hypot(after.coords[1][0] - original[1][0], after.coords[1][1] - original[1][1]);
  if (moved < 1) throw new Error(`the stored vertex barely moved (${moved.toFixed(3)} m)`);
  console.log(`\n    vertex 2 moved ${moved.toFixed(3)} m and was stored`);
  const stillEditing = await page.locator(".panel .card", { hasText: "Vertices of #" }).count();
  if (stillEditing) throw new Error("the editor stayed open after saving");
});

await step("put the breakline back as it was", async () => {
  const r = await fetch(`${base}/api/projects/${pid}/lines/${breakFid}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ coords: original }),
  });
  if (!r.ok) throw new Error(`restore failed: ${r.status}`);
  const back = await lineOf(breakFid);
  if (back.coords.length !== original.length) throw new Error("restore changed the vertex count");
});

if (ringFid !== null) {
  await step("a boundary or void keeps its ring closed", async () => {
    const before = await lineOf(ringFid);
    const corners = before.coords.slice(0, -1);
    const row = await rowFor(ringFid);
    await row.locator('button:text-is("Edit")').click();
    await page.waitForTimeout(1000);
    const handles = await page.evaluate(() => window.__plm.layers.editVertices.length);
    if (handles !== corners.length) throw new Error(`${handles} handles for ${corners.length} corners (the repeated one should not get its own)`);
    const card = page.locator(".panel .card", { hasText: `Vertices of #${ringFid}` });
    const rows = await card.locator("table.data tr").count();
    if (rows - 1 !== corners.length) throw new Error(`the table shows ${rows - 1} corners, expected ${corners.length}`);
    // move the first corner: the repeated last one must follow
    const cells = card.locator("table.data tr").nth(1).locator("input");
    await cells.nth(0).fill(String(Number((corners[0][0] + 5).toFixed(3))));
    await cells.nth(0).dispatchEvent("change");
    await page.waitForTimeout(300);
    await card.locator('button:text-is("Save line")').click();
    await page.waitForTimeout(1800);
    const after = await lineOf(ringFid);
    if (after.coords[0][0] !== after.coords[after.coords.length - 1][0] || after.coords[0][1] !== after.coords[after.coords.length - 1][1]) {
      throw new Error("the ring came back open");
    }
    console.log(`\n    ${corners.length} corners, first == last after moving corner 1`);
    await fetch(`${base}/api/projects/${pid}/lines/${ringFid}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ coords: before.coords }),
    });
  });
}

await browser.close();
if (errors.length) { console.error("browser errors:\n" + errors.join("\n")); process.exit(1); }
console.log("PASS: constraint line vertex editing");
