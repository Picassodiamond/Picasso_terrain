/**
 * Plan tools and pointer shapes: the palette switches tool, the cursor says what the tool will do,
 * select moves a node, delete removes one, zoom drags a box, and the profile's PVI handles show a
 * move cursor (ns-resize for the two that are pinned in chainage).
 *   node e2e/tools_check.mjs http://127.0.0.1:8000 <projectId> [designId]
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const base = process.argv[2] || "http://127.0.0.1:8000";
const pid = process.argv[3];
let did = process.argv[4] ? Number(process.argv[4]) : null;
if (!pid) { console.error("usage: node e2e/tools_check.mjs <baseUrl> <projectId> [designId]"); process.exit(2); }
const outDir = new URL("./screenshots/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
mkdirSync(outDir, { recursive: true });
const j = async (u) => { const r = await fetch(base + u); if (!r.ok) throw new Error(`${u}: ${r.status}`); return r.json(); };
if (!did) {
  const designs = (await j(`/api/projects/${pid}/designs`)).filter((d) => d.module === "road");
  if (!designs.length) { console.error("no road design in that project"); process.exit(2); }
  did = designs[designs.length - 1].id;
}

const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: 1600, height: 950 } });
await page.addInitScript(() => { window.__plmNoVisitorForm = true; });
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
page.on("dialog", (d) => d.accept());
const step = async (name, fn) => { process.stdout.write(`- ${name} ... `); await fn(); console.log("ok"); };
const cursor = () => page.evaluate(() => getComputedStyle(document.querySelector(".plan-canvas")).cursor);
const tool = () => page.evaluate(() => window.__plm.plan?.tool ?? window.__plm?.["plan"]?.tool);

/** Move the mouse to the screen position of IP number i, and to a point well away from any node. */
async function overHandle(i) {
  const pt = await page.evaluate((k) => {
    const ws = window.__plm, p = ws.plan;
    const h = p.handles[k];
    if (!h) return null;
    const [sx, sy] = p.toScreen(h.x, h.y);
    const r = p.canvas.getBoundingClientRect();
    return { x: r.left + sx, y: r.top + sy };
  }, i);
  if (!pt) throw new Error(`no handle ${i} (is the Alignment stage open?)`);
  await page.mouse.move(pt.x, pt.y);
  await page.waitForTimeout(150);
  return pt;
}
async function overEmpty() {
  const r = await page.locator(".plan-canvas").boundingBox();
  await page.mouse.move(r.x + 30, r.y + r.height - 30);
  await page.waitForTimeout(150);
}

await step("open the road workspace on the Alignment stage", async () => {
  await page.goto(`${base}/#/p/${pid}/road/${did}`, { waitUntil: "networkidle" });
  await page.waitForSelector(".road-workspace .plan-canvas", { timeout: 60000 });
  await page.waitForTimeout(1800);
  await page.locator(".stage", { hasText: "Alignment" }).first().dispatchEvent("click");
  await page.waitForTimeout(800);
  const n = await page.evaluate(() => window.__plm.plan.handles.length);
  if (n < 3) throw new Error(`need at least 3 IPs to test deletion, found ${n}`);
  console.log(`\n    ${n} IP handles`);
});

await step("the palette shows the four tools", async () => {
  const labels = await page.locator(".tool-palette button").allInnerTexts();
  const want = ["Select / move", "Pan", "Zoom", "Delete node"];
  for (const w of want) if (!labels.some((l) => l.includes(w))) throw new Error(`palette is missing "${w}": ${labels.join(" | ")}`);
  await page.screenshot({ path: `${outDir}tools-01-palette.png`, animations: "disabled" });
});

await step("select: move cursor over a node, default over empty space", async () => {
  await overHandle(1);
  const onNode = await cursor();
  await overEmpty();
  const onEmpty = await cursor();
  if (onNode !== "move") throw new Error(`over a node the cursor is "${onNode}", expected move`);
  if (onEmpty === "move") throw new Error("empty space still shows the move cursor");
  console.log(`\n    node: ${onNode}   empty: ${onEmpty}`);
});

await step("pan: grab, and grabbing while the button is down", async () => {
  await page.keyboard.press("p");
  await page.waitForTimeout(200);
  if ((await tool()) !== "pan") throw new Error(`P did not choose the pan tool (${await tool()})`);
  await overEmpty();
  const idle = await cursor();
  await page.mouse.down();
  await page.waitForTimeout(150);
  const down = await cursor();
  await page.mouse.up();
  if (idle !== "grab") throw new Error(`pan idle cursor is "${idle}"`);
  if (down !== "grabbing") throw new Error(`pan pressed cursor is "${down}"`);
  console.log(`\n    idle: ${idle}   pressed: ${down}`);
});

await step("pan drags the view instead of the node", async () => {
  const before = await page.evaluate(() => [window.__plm.plan.cx, window.__plm.plan.cy]);
  const p = await overHandle(1);            // start right on a node: pan must still pan
  await page.mouse.down();
  await page.mouse.move(p.x + 80, p.y + 40, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(200);
  const after = await page.evaluate(() => [window.__plm.plan.cx, window.__plm.plan.cy]);
  if (Math.abs(after[0] - before[0]) < 1e-6) throw new Error("the pan tool did not move the view");
  const dirty = await page.evaluate(() => window.__plm.dirty.size);
  if (dirty) throw new Error("the pan tool moved a node (the design went dirty)");
});

await step("zoom: zoom-in cursor, box drag zooms in", async () => {
  await page.keyboard.press("z");
  await page.waitForTimeout(200);
  await overEmpty();
  const c = await cursor();
  if (c !== "zoom-in") throw new Error(`zoom cursor is "${c}"`);
  const scale0 = await page.evaluate(() => window.__plm.plan.scale);
  const r = await page.locator(".plan-canvas").boundingBox();
  await page.mouse.move(r.x + r.width * 0.35, r.y + r.height * 0.35);
  await page.mouse.down();
  await page.mouse.move(r.x + r.width * 0.55, r.y + r.height * 0.55, { steps: 8 });
  await page.screenshot({ path: `${outDir}tools-02-zoom-box.png`, animations: "disabled" });
  await page.mouse.up();
  await page.waitForTimeout(300);
  const scale1 = await page.evaluate(() => window.__plm.plan.scale);
  if (!(scale1 > scale0 * 1.2)) throw new Error(`box zoom did not zoom in (${scale0} -> ${scale1})`);
  console.log(`\n    scale ${scale0.toFixed(3)} -> ${scale1.toFixed(3)} px/m`);
});

await step("select: dragging a node moves it and marks the design unsaved", async () => {
  await page.keyboard.press("s");
  await page.waitForTimeout(200);
  await page.evaluate(() => window.__plm.plan.fit(window.__plm.plan.layers.length ? [0, 0, 1, 1] : [0, 0, 1, 1]));
  await page.evaluate(() => window.__plm.fitAlignment?.());
  await page.waitForTimeout(500);
  const before = await page.evaluate(() => JSON.parse(JSON.stringify(window.__plm.editIps[1])));
  const p = await overHandle(1);
  await page.mouse.down();
  await page.mouse.move(p.x + 25, p.y - 18, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(400);
  const after = await page.evaluate(() => JSON.parse(JSON.stringify(window.__plm.editIps[1])));
  if (Math.abs(after.x - before.x) < 1e-6 && Math.abs(after.y - before.y) < 1e-6) throw new Error("the node did not move");
  const dirty = await page.evaluate(() => [...window.__plm.dirty]);
  if (!dirty.includes("horizontal")) throw new Error(`expected the alignment to be unsaved, dirty = ${dirty}`);
  console.log(`\n    IP 1 moved ${(after.x - before.x).toFixed(2)}, ${(after.y - before.y).toFixed(2)} m`);
});

await step("delete: not-allowed off a node, pointer on one, and the node goes", async () => {
  await page.keyboard.press("d");
  await page.waitForTimeout(200);
  if ((await tool()) !== "delete") throw new Error("D did not choose the delete tool");
  await overEmpty();
  const off = await cursor();
  await overHandle(1);
  const on = await cursor();
  if (off !== "not-allowed") throw new Error(`off a node the delete cursor is "${off}"`);
  if (on !== "pointer") throw new Error(`on a node the delete cursor is "${on}"`);
  const n0 = await page.evaluate(() => window.__plm.editIps.length);
  await page.mouse.down();
  await page.mouse.up();
  await page.waitForTimeout(600);
  const n1 = await page.evaluate(() => window.__plm.editIps.length);
  if (n1 !== n0 - 1) throw new Error(`delete removed ${n0 - n1} nodes, expected 1`);
  console.log(`\n    cursors ${off} / ${on};  IPs ${n0} -> ${n1}`);
});

await step("the profile's PVI handles are move / ns-resize", async () => {
  const kinds = await page.evaluate(() => {
    const hs = Array.from(document.querySelectorAll(".road-profile circle.pvi"));
    return hs.map((h) => h.style.cursor);
  });
  if (!kinds.length) { console.log("\n    no PVIs on this design - skipped"); return; }
  if (kinds[0] !== "ns-resize" || kinds[kinds.length - 1] !== "ns-resize") {
    throw new Error(`the end PVIs should be ns-resize, got ${kinds[0]} / ${kinds[kinds.length - 1]}`);
  }
  if (kinds.length > 2 && !kinds.slice(1, -1).every((c) => c === "move")) throw new Error(`middle PVIs: ${kinds.join(", ")}`);
  console.log(`\n    ${kinds.length} PVIs: ${kinds[0]} … ${kinds.slice(1, -1)[0] ?? "-"} … ${kinds[kinds.length - 1]}`);
});

await step("Escape comes back to Select, and the status line names the tool", async () => {
  await page.keyboard.press("d");
  await page.waitForTimeout(200);
  const armed = (await page.locator(".road-status").innerText()).trim();
  if (!armed.includes("Delete node")) throw new Error(`an armed tool is invisible in the status line: "${armed}"`);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  if ((await tool()) !== "select") throw new Error("Escape did not return to the Select tool");
  const back = (await page.locator(".road-status").innerText()).trim();
  if (!back.includes("Select / move")) throw new Error(`status line after Escape: "${back}"`);
  console.log(`\n    armed: ${armed.slice(0, 40)}  ->  ${back.slice(0, 40)}`);
});

await step("moving a node updates the cross-section", async () => {
  await page.evaluate(() => window.__plm.showSection(200));
  await page.waitForTimeout(1500);
  const snap = () => page.evaluate(() => {
    const svg = document.querySelector(".road-section-body svg");
    return {
      title: svg?.querySelector("text.chart-title")?.textContent || "",
      ground: svg?.querySelector("path.ground-line")?.getAttribute("d") || "",
      station: window.__plm.station,
    };
  });
  const before = await snap();
  const pt = await overHandle(1);
  await page.mouse.down();
  await page.mouse.move(pt.x + 55, pt.y - 40, { steps: 10 });
  await page.mouse.up();
  await page.waitForTimeout(2500);
  const after = await snap();
  if (after.station !== before.station) throw new Error("the station moved on its own");
  if (after.ground === before.ground) throw new Error("the cross-section still shows the old centre line");
  if (!after.title.includes("alignment unsaved")) throw new Error(`the section does not say the alignment is unsaved: "${after.title}"`);
  console.log(`\n    ground line redrawn at the same chainage, titled "...${after.title.slice(-42)}"`);
});

await step("a wall stands on the ground, not in mid-air", async () => {
  const check = await page.evaluate(async () => {
    const ws = window.__plm;
    const all = (ws.data.structures?.structures || []).filter((x) => (x.params || {}).height !== undefined);
    const wall = all.find((x) => x.kind === "retaining_wall" || x.kind === "breast_wall");
    if (!wall) return null;
    const ch = (Number(wall.from) + Number(wall.to)) / 2;
    const r = await fetch(`/api/projects/${ws.pid}/designs/${ws.did}/road/corridor/section?chainage=${ch}`);
    if (!r.ok) return null;
    const sec = await r.json();
    const it = (sec.structures || []).find((t) => t.group === "wall");
    if (!it) return null;
    const g = sec.ground;
    const face = it.points.reduce((a, b) => (Math.abs(b[0]) < Math.abs(a[0]) ? b : a));
    const gz = (o) => {
      let lo = g[0], hi = g[g.length - 1];
      for (let i = 1; i < g.length; i++) if (g[i - 1][0] <= o && o <= g[i][0]) { lo = g[i - 1]; hi = g[i]; break; }
      const t = (o - lo[0]) / ((hi[0] - lo[0]) || 1);
      return lo[1] + t * (hi[1] - lo[1]);
    };
    const base = Math.min(...it.points.map((q) => q[1]));
    return { chainage: sec.chainage, label: it.label, faceOffset: face[0], base, groundAtFace: gz(face[0]),
             hinge: (sec[it.side] || {}).hinge_z, top: Math.max(...it.points.map((q) => q[1])) };
  });
  if (!check) { console.log("\n    no wall on this design - skipped"); return; }
  const gap = Math.abs(check.base - check.groundAtFace);
  if (gap > 0.5) throw new Error(`the wall base is ${gap.toFixed(2)} m off the ground (base ${check.base.toFixed(2)}, ground ${check.groundAtFace.toFixed(2)})`);
  if (check.hinge && Math.abs(check.top - check.hinge) > 0.5) {
    throw new Error(`the wall top is ${Math.abs(check.top - check.hinge).toFixed(2)} m from the formation hinge`);
  }
  console.log(`\n    CH ${check.chainage}: ${check.label} - base RL ${check.base.toFixed(2)} on ground ${check.groundAtFace.toFixed(2)}, top at the hinge`);
});

await step("the shortcut card lists the tools", async () => {
  await page.keyboard.press("?");
  await page.waitForSelector(".keys-overlay", { timeout: 5000 });
  const t = await page.locator(".keys-dialog").innerText();
  for (const w of ["Select / move tool", "Pan tool", "Zoom tool", "Delete node tool"]) {
    if (!t.includes(w)) throw new Error(`the card does not list "${w}"`);
  }
  await page.keyboard.press("Escape");
});

await browser.close();
if (errors.length) { console.error("browser errors:\n" + errors.join("\n")); process.exit(1); }
console.log("PASS: plan tools and pointer shapes");
