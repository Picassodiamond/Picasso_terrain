/**
 * Keyboard navigation check: cross-section stepping, chainage jumps, panel and stage switching, the
 * help card, and the rule that shortcuts stay quiet while the user is typing.
 *   node e2e/keys_check.mjs http://127.0.0.1:8000 <terrainProjectId> [roadProjectId] [roadDesignId]
 * The terrain project needs a generated section set; the road project needs a road design.
 */
import { chromium } from "playwright";

const base = process.argv[2] || "http://127.0.0.1:8000";
const tpid = process.argv[3];
const rpid = process.argv[4] || tpid;
let did = process.argv[5] ? Number(process.argv[5]) : null;
if (!tpid) { console.error("usage: node e2e/keys_check.mjs <baseUrl> <terrainProjectId> [roadProjectId] [roadDesignId]"); process.exit(2); }
const j = async (u) => { const r = await fetch(base + u); if (!r.ok) throw new Error(`${u}: ${r.status}`); return r.json(); };

const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: 1600, height: 950 } });
await page.addInitScript(() => { window.__plmNoVisitorForm = true; });
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
page.on("dialog", (d) => d.accept("0+100"));   // the "go to chainage" prompt
const step = async (name, fn) => { process.stdout.write(`- ${name} ... `); await fn(); console.log("ok"); };
const key = async (k) => { await page.keyboard.press(k); await page.waitForTimeout(350); };
const station = () => page.locator(".charts .chart-head .mono").first().innerText();

// ------------------------------------------------------------------ terrain workspace
await step("terrain: open and show the charts", async () => {
  await page.goto(`${base}/#/p/${tpid}`, { waitUntil: "networkidle" });
  await page.waitForSelector(".workspace", { timeout: 60000 });
  const sets = await j(`/api/projects/${tpid}/sections`);
  if (!sets.length) throw new Error("this project has no section set - generate one first");
  await page.evaluate((id) => window.__plm.showCharts(true) ?? id, sets[0].id);
  await page.waitForTimeout(1200);
  if (!(await station()).includes("CH")) throw new Error(`no station shown: "${await station()}"`);
});

let first = "";
await step("terrain: → and ← step one section", async () => {
  first = await station();
  await key("ArrowRight");
  const second = await station();
  if (second === first) throw new Error(`→ did not move: still ${first}`);
  await key("ArrowLeft");
  if ((await station()) !== first) throw new Error(`← did not come back: ${await station()} != ${first}`);
  console.log(`\n    ${first.trim()}  →  ${second.trim()}  →  back`);
});

await step("terrain: Home and End go to the first and last section", async () => {
  await key("End");
  const last = await station();
  await key("Home");
  const home = await station();
  if (!home.includes("(1/")) throw new Error(`Home did not reach section 1: ${home}`);
  if (last === home) throw new Error("End and Home gave the same section");
  console.log(`\n    Home ${home.trim()}   End ${last.trim()}`);
});

await step("terrain: Shift+→ jumps ten sections", async () => {
  await key("Home");
  await page.keyboard.press("Shift+ArrowRight");
  await page.waitForTimeout(400);
  const t = await station();
  if (!t.includes("(11/")) throw new Error(`Shift+→ should reach section 11, got ${t}`);
});

await step("terrain: g jumps to a typed chainage", async () => {
  await key("Home");
  await key("g");                       // the dialog handler answers "0+100"
  await page.waitForTimeout(600);
  const t = await station();
  if (t.includes("(1/")) throw new Error(`"g" did not move away from the first section: ${t}`);
  console.log(`\n    typed 0+100 -> ${t.trim()}`);
});

await step("terrain: digits switch side panels", async () => {
  await key("2");
  const tin = await page.locator(".tabs button.active").innerText();
  await key("5");
  const sec = await page.locator(".tabs button.active").innerText();
  if (tin.trim() !== "TIN" || sec.trim() !== "Sections") throw new Error(`digits chose ${tin} / ${sec}`);
});

await step("terrain: ? opens the help card and lists the section keys", async () => {
  await key("?");
  await page.waitForSelector(".keys-overlay", { timeout: 5000 });
  const text = await page.locator(".keys-dialog").innerText();
  for (const want of ["Next cross-section", "Go to chainage", "Show this list of shortcuts"]) {
    if (!text.includes(want)) throw new Error(`help card is missing "${want}"`);
  }
  const rows = await page.locator(".keys-table tr").count();
  await key("Escape");
  if (await page.locator(".keys-overlay").count()) throw new Error("Esc did not close the help card");
  console.log(`\n    ${rows} shortcuts listed`);
});

await step("terrain: typing in a box does not trigger shortcuts", async () => {
  await key("5");                                    // Sections panel has text inputs
  const box = page.locator('.panel input[type="text"]').first();
  await box.click();
  const before = await station();
  await box.type("1+250");
  await page.waitForTimeout(300);
  if ((await box.inputValue()) !== "1+250") throw new Error(`the box did not receive the text: "${await box.inputValue()}"`);
  if ((await station()) !== before) throw new Error("the section moved while typing");
  if (await page.locator(".keys-overlay").count()) throw new Error("the help card opened while typing");
});

// ------------------------------------------------------------------ road workspace
if (!did) {
  const designs = (await j(`/api/projects/${rpid}/designs`)).filter((d) => d.module === "road");
  did = designs.length ? designs[designs.length - 1].id : null;
}
if (did) {
  await step("road: open the design workspace", async () => {
    await page.goto(`${base}/#/p/${rpid}/road/${did}`, { waitUntil: "networkidle" });
    await page.waitForSelector(".road-workspace .plan-canvas", { timeout: 60000 });
    await page.waitForTimeout(1500);
  });

  const roadStation = () => page.locator(".road-right .chart-head .mono").first().innerText();

  await step("road: → and ← move one section interval", async () => {
    await key("Home");
    const a = await roadStation();
    await key("ArrowRight");
    const b = await roadStation();
    if (a === b) throw new Error(`→ did not move the station: still ${a}`);
    await key("ArrowLeft");
    if ((await roadStation()) !== a) throw new Error("← did not come back");
    console.log(`\n    ${a.trim()}  →  ${b.trim()}`);
  });

  await step("road: Home and End reach the ends of the alignment", async () => {
    await key("End");
    const end = await roadStation();
    await key("Home");
    const start = await roadStation();
    if (end === start) throw new Error("End and Home gave the same chainage");
    console.log(`\n    ${start.trim()} … ${end.trim()}`);
  });

  await step("road: digits switch design stages", async () => {
    await key("4");
    const s4 = await page.locator(".stage.active .label").innerText();
    await key("1");
    const s1 = await page.locator(".stage.active .label").innerText();
    if (s4 === s1) throw new Error(`digits did not change the stage (${s1})`);
    console.log(`\n    4 -> ${s4.trim()},  1 -> ${s1.trim()}`);
  });

  await step("road: the help card knows about the stages", async () => {
    await key("?");
    await page.waitForSelector(".keys-overlay", { timeout: 5000 });
    const text = await page.locator(".keys-dialog").innerText();
    if (!text.includes("Stage 1")) throw new Error("the help card does not list the stages");
    if (!text.includes("Save everything unsaved")) throw new Error("the help card does not list Ctrl+S");
    await key("Escape");
  });
} else console.log("- road: skipped (no road design in that project)");

await browser.close();
if (errors.length) { console.error("browser errors:\n" + errors.join("\n")); process.exit(1); }
console.log("PASS: keyboard navigation");
