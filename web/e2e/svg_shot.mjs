/** Render SVG files to PNG for a quick look.
 *   node e2e/svg_shot.mjs <dir> [zoom] [x y w h]      (clip in output pixels at the given zoom)
 * Zoom 1 renders an A3 sheet at 1680 px wide; the optional clip crops the screenshot. */
import { chromium } from "playwright";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const dir = process.argv[2];
const zoom = Number(process.argv[3] || 1);
const clip = process.argv.length >= 8 ? { x: Number(process.argv[4]), y: Number(process.argv[5]), width: Number(process.argv[6]), height: Number(process.argv[7]) } : null;
const W = Math.round(1680 * zoom), H = Math.round(1188 * zoom);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: Math.min(W, 16000), height: Math.min(H, 16000) } });
for (const f of readdirSync(dir).filter((f) => f.endsWith(".svg"))) {
  const svg = readFileSync(join(dir, f), "utf8");
  await page.setContent(`<html><body style="margin:0;background:#888"><div style="width:${W}px">${svg.replace(/width="[^"]+mm" height="[^"]+mm"/, `width="${W}" height="${H}"`)}</div></body></html>`);
  const out = join(dir, f.replace(".svg", clip ? "-zoom.png" : ".png"));
  await page.screenshot({ path: out, ...(clip ? { clip } : {}) });
  console.log("wrote", out);
}
await browser.close();
