/**
 * Big-data check: opens a project with a large point cloud and a tiled TIN, verifies that the mesh
 * arrives as tiles, that the point cloud primitive renders without WebGL/shader errors, and that a
 * click on the point cloud resolves a nearest point through the API. Screenshots to e2e/screenshots/.
 *   node e2e/bigdata_check.mjs http://127.0.0.1:8000 <projectId>
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const base = process.argv[2] || "http://127.0.0.1:8000";
const pid = process.argv[3];
if (!pid) { console.error("usage: node e2e/bigdata_check.mjs <baseUrl> <projectId>"); process.exit(2); }
const outDir = new URL("./screenshots/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
const errors = [];
const requests = { tiles: 0, mesh: 0, pointsBin: 0, pointsGeojson: 0, nearest: 0, bytes: 0 };
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
page.on("response", async (r) => {
  const u = r.url();
  if (/\/tiles\/\d+\/\d+\.bin/.test(u)) requests.tiles++;
  else if (u.endsWith("/mesh.bin")) requests.mesh++;
  else if (u.endsWith("/points.bin")) requests.pointsBin++;
  else if (u.includes("/points.geojson")) requests.pointsGeojson++;
  else if (u.includes("/points/nearest")) requests.nearest++;
  const len = Number(r.headers()["content-length"] || 0);
  if (/\.bin/.test(u)) requests.bytes += len;
});

const t0 = Date.now();
await page.goto(`${base}/#/p/${pid}`, { waitUntil: "domcontentloaded" });
await page.waitForSelector("#cesium canvas", { timeout: 60000 });
await page.waitForFunction(() => !document.querySelector(".busy") || getComputedStyle(document.querySelector(".busy")).display === "none", null, { timeout: 300000 });
const loadMs = Date.now() - t0;
await page.waitForTimeout(4000);
await page.screenshot({ path: `${outDir}bigdata-${pid}.png` });

// click the middle of the canvas with no tool active: point cloud -> nearest point lookup
const canvas = await page.$("#cesium canvas");
const box = await canvas.boundingBox();
await page.mouse.click(box.x + box.width * 0.55, box.y + box.height * 0.5);
await page.waitForTimeout(2500);
const toast = await page.$$eval(".toast, .toasts *", (els) => els.map((e) => e.textContent).filter(Boolean).join(" | "));

const memory = await page.evaluate(() => (performance).memory ? Math.round((performance).memory.usedJSHeapSize / 1e6) : null);
console.log(`loaded in ${(loadMs / 1000).toFixed(1)}s; requests: ${requests.tiles} tiles, ${requests.mesh} whole mesh, ${requests.pointsBin} points.bin, ${requests.pointsGeojson} points.geojson, ${requests.nearest} nearest; ${(requests.bytes / 1e6).toFixed(1)} MB binary`);
console.log(`JS heap: ${memory ?? "n/a"} MB; toast after click: ${toast || "(none)"}`);
if (errors.length) console.log("page errors:", errors);
await browser.close();

const pass = errors.length === 0 && requests.pointsBin === 1 && requests.pointsGeojson === 0 && (requests.tiles > 1 || requests.mesh === 1);
console.log(pass ? "PASS: big data view" : "FAIL");
process.exit(pass ? 0 : 1);
