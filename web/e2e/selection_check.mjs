/**
 * The selection contract: different kinds of element all report properties to the one inspector,
 * and editing there writes back.
 *   node e2e/selection_check.mjs http://127.0.0.1:8000 <terrainProjectId> <roadProjectId> [designId]
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const base = process.argv[2] || "http://127.0.0.1:8000";
const tpid = process.argv[3];
const rpid = process.argv[4] || tpid;
let did = process.argv[5] ? Number(process.argv[5]) : null;
if (!tpid) { console.error("usage: node e2e/selection_check.mjs <baseUrl> <terrainProjectId> [roadProjectId] [designId]"); process.exit(2); }
const outDir = new URL("./screenshots/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
mkdirSync(outDir, { recursive: true });
const j = async (u) => { const r = await fetch(base + u); if (!r.ok) throw new Error(`${u}: ${r.status}`); return r.json(); };

const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: 1600, height: 950 } });
await page.addInitScript(() => { window.__plmNoVisitorForm = true; });
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
page.on("dialog", (d) => d.accept());
const step = async (name, fn) => { process.stdout.write(`- ${name} ... `); await fn(); console.log("ok"); };
const idle = () => page.waitForFunction(() => !document.querySelector(".busy") || getComputedStyle(document.querySelector(".busy")).display === "none", null, { timeout: 120000 });

/** What the inspector is showing: its kind, title and the label/value of every row. */
const inspector = () => page.evaluate(() => {
  const p = document.querySelector(".inspector");
  if (!p || p.style.display === "none") return null;
  const labels = Array.from(p.querySelectorAll(".inspector-label")).map((e) => e.textContent.trim());
  const values = Array.from(p.querySelectorAll(".inspector-value")).map((e) => {
    const inp = e.querySelector("input, select");
    return inp ? String(inp.value) : e.textContent.trim();
  });
  return {
    kind: p.querySelector(".inspector-kind")?.textContent.trim(),
    title: p.querySelector(".inspector-title")?.textContent.trim(),
    subtitle: p.querySelector(".inspector-sub")?.textContent.trim() || "",
    fields: Object.fromEntries(labels.map((l, i) => [l, values[i]])),
    hasApply: !!Array.from(p.querySelectorAll("button")).find((b) => b.textContent.trim() === "Apply"),
  };
});

// ------------------------------------------------------------------ terrain: a constraint vertex
await step("terrain: a constraint vertex reports Easting and Northing", async () => {
  await page.goto(`${base}/#/p/${tpid}`, { waitUntil: "networkidle" });
  await page.waitForSelector(".workspace", { timeout: 60000 });
  await idle();
  await page.waitForTimeout(2500);
  const fc = await j(`/api/projects/${tpid}/lines.geojson`);
  const brk = fc.features.find((f) => f.properties.kind === "feature" && f.geometry.coordinates.length >= 3);
  if (!brk) throw new Error("this project has no breakline");
  await page.keyboard.press("1");
  await page.waitForTimeout(400);
  const table = page.locator(".panel table.data").filter({ has: page.locator('th:text-is("kind")') }).first();
  const idx = fc.features.findIndex((f) => f.properties.fid === brk.properties.fid) + 1;
  await table.locator("tr").nth(idx).locator('button:text-is("Edit")').click();
  await page.waitForTimeout(900);
  // focusing a coordinate box selects that vertex
  const card = page.locator(".panel .card", { hasText: `Vertices of #${brk.properties.fid}` });
  await card.locator("table.data tr").nth(2).locator("input").first().focus();
  await page.waitForTimeout(500);
  const ins = await inspector();
  if (!ins) throw new Error("nothing selected");
  if (ins.kind !== "vertex") throw new Error(`kind is "${ins.kind}"`);
  for (const f of ["Easting", "Northing"]) if (!(f in ins.fields)) throw new Error(`no ${f} field: ${Object.keys(ins.fields).join(", ")}`);
  if ("RL" in ins.fields) throw new Error("a constraint vertex should not ask for RL - it is plan geometry");
  if (!ins.hasApply) throw new Error("a vertex should be editable");
  console.log(`\n    ${ins.kind}: ${ins.title} {${Object.keys(ins.fields).join(", ")}}`);
  await page.screenshot({ path: `${outDir}selection-01-vertex.png`, animations: "disabled", timeout: 60000 });
});

await step("terrain: Apply moves the vertex", async () => {
  const before = await page.evaluate(() => document.querySelector(".inspector-value input").value);
  await page.evaluate(() => {
    const inp = document.querySelector(".inspector-value input");
    inp.value = String(Number(inp.value) + 7.25);
    inp.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await page.locator(".inspector button", { hasText: "Apply" }).click();
  await page.waitForTimeout(700);
  const after = await page.evaluate(() => document.querySelector(".inspector-value input").value);
  if (Math.abs(Number(after) - Number(before) - 7.25) > 0.01) throw new Error(`Easting went ${before} -> ${after}`);
  console.log(`\n    Easting ${before} -> ${after}`);
});

await step("terrain: Properties is a tab of the Layers panel", async () => {
  const inPanel = await page.evaluate(() => !!document.querySelector(".layer-panel .inspector.embedded"));
  if (!inPanel) throw new Error("the inspector is not inside the layer panel");
  const floating = await page.evaluate(() => document.querySelectorAll(".inspector:not(.embedded)").length);
  if (floating) throw new Error(`${floating} floating inspector card(s) still over the map`);
  const tabs = await page.locator(".layer-tabs button").allInnerTexts();
  if (tabs.length !== 2) throw new Error(`expected two tabs, got ${tabs.join(" | ")}`);
  console.log(`\n    tabs: ${tabs.map((t) => t.trim()).join(" | ")}`);
});

await step("terrain: the tabs switch, and clearing leaves a placeholder", async () => {
  await page.locator(".layer-tabs button", { hasText: "Layers" }).first().click();
  await page.waitForTimeout(300);
  if (await page.evaluate(() => getComputedStyle(document.querySelector(".layer-props")).display) !== "none") {
    throw new Error("the Properties pane is still shown on the Layers tab");
  }
  await page.locator(".layer-tabs button").nth(1).click();
  await page.waitForTimeout(300);
  if (!(await inspector())) throw new Error("the Properties tab shows nothing");
  await page.locator(".panel button", { hasText: "Cancel" }).first().click();
  await page.waitForTimeout(500);
  const empty = await page.evaluate(() => !!document.querySelector(".inspector-empty"));
  if (!empty) throw new Error("no placeholder after the selection was cleared");
});

// ------------------------------------------------------------------ road: an IP and a structure
if (!did) {
  const designs = (await j(`/api/projects/${rpid}/designs`)).filter((d) => d.module === "road");
  did = designs.length ? designs[designs.length - 1].id : null;
}
if (did) {
  await step("road: an IP reports its radius and transition", async () => {
    // a hash-only change does not re-create the module instance: reload so __plm is the road workspace
    await page.goto(`${base}/#/p/${rpid}/road/${did}`, { waitUntil: "networkidle" });
    await page.reload({ waitUntil: "networkidle" });
    await page.waitForSelector(".road-workspace .plan-canvas", { timeout: 60000 });
    await page.waitForTimeout(2000);
    await page.evaluate(() => window.__plm.selectIp(1));
    await page.waitForTimeout(500);
    const ins = await inspector();
    if (!ins || ins.kind !== "IP") throw new Error(`expected an IP selection, got ${JSON.stringify(ins)}`);
    for (const f of ["Easting", "Northing", "Radius R", "Transition Ls"]) {
      if (!(f in ins.fields)) throw new Error(`no "${f}" field: ${Object.keys(ins.fields).join(", ")}`);
    }
    console.log(`\n    ${ins.title}: R = ${ins.fields["Radius R"]} m`);
  });

  await step("road: editing the radius marks the alignment unsaved", async () => {
    const r0 = await page.evaluate(() => window.__plm.editIps[1].radius);
    await page.evaluate(() => {
      const rows = Array.from(document.querySelectorAll(".inspector-label"));
      const i = rows.findIndex((e) => e.textContent.trim() === "Radius R");
      const inp = document.querySelectorAll(".inspector-value")[i].querySelector("input");
      inp.value = String(Number(inp.value) + 30);
      inp.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await page.locator(".inspector button", { hasText: "Apply" }).click();
    await page.waitForTimeout(900);
    const r1 = await page.evaluate(() => window.__plm.editIps[1].radius);
    if (Math.abs(r1 - r0 - 30) > 0.01) throw new Error(`radius went ${r0} -> ${r1}`);
    const dirty = await page.evaluate(() => [...window.__plm.dirty]);
    if (!dirty.includes("horizontal")) throw new Error(`expected the alignment unsaved, dirty = ${dirty}`);
    console.log(`\n    R ${r0} -> ${r1} m, alignment marked unsaved`);
    await page.evaluate(() => window.__plm.revertHorizontal?.());
    await page.waitForTimeout(600);
  });

  await step("road: a structure reports its type, extent and parameters", async () => {
    const n = await page.evaluate(() => (window.__plm.data.structures?.structures || []).length);
    if (!n) { console.log("\n    no structures on this design - skipped"); return; }
    await page.locator(".stage", { hasText: "Structures" }).first().dispatchEvent("click");
    await page.waitForTimeout(900);
    const cell = page.locator(".road-side table.data tr.clickable td").nth(3);   // the "from" cell
    await cell.click();
    await page.waitForTimeout(700);
    const ins = await inspector();
    if (!ins) throw new Error("clicking a structure row selected nothing");
    if (!["wall", "drain", "culvert"].includes(ins.kind)) throw new Error(`kind is "${ins.kind}"`);
    for (const f of ["Type", "From", "To", "Length"]) {
      if (!(f in ins.fields)) throw new Error(`no "${f}" field: ${Object.keys(ins.fields).join(", ")}`);
    }
    if (!ins.hasApply) throw new Error("a structure should be editable");
    console.log(`\n    ${ins.kind}: ${ins.title} ${ins.subtitle} {${Object.keys(ins.fields).join(", ")}}`);
    await page.screenshot({ path: `${outDir}selection-02-structure.png`, animations: "disabled", timeout: 60000 });
  });
}

await browser.close();
if (errors.length) { console.error("browser errors:\n" + errors.join("\n")); process.exit(1); }
console.log("PASS: selection and properties");
