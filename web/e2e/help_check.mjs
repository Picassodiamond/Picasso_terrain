/** Help pages check: every page is served, every image loads, every in-page anchor exists.
 *   node e2e/help_check.mjs http://127.0.0.1:8000 */
import { chromium } from "playwright";

const base = process.argv[2] || "http://127.0.0.1:8000";
const pages = ["index", "terrain", "road", "standards", "accounts", "faq"];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
let bad = 0;
for (const p of pages) {
  const res = await page.goto(`${base}/help/${p}.html`, { waitUntil: "networkidle" });
  const status = res?.status();
  const title = await page.title();
  const imgs = await page.$$eval("img", (els) => els.map((i) => ({ src: i.getAttribute("src"), ok: i.complete && i.naturalWidth > 0 })));
  const broken = imgs.filter((i) => !i.ok);
  const anchors = await page.$$eval("a[href^='#']", (as) => as.map((a) => a.getAttribute("href").slice(1)));
  const ids = new Set(await page.$$eval("[id]", (els) => els.map((e) => e.id)));
  const missing = anchors.filter((a) => !ids.has(a));
  const links = await page.$$eval("a[href$='.html'], a[href*='.html#']", (as) => as.map((a) => a.getAttribute("href")));
  for (const l of new Set(links)) {
    const file = l.split("#")[0];
    if (!pages.includes(file.replace(".html", ""))) { console.log(`  ${p}: link to unknown page ${l}`); bad++; }
  }
  console.log(`${p}.html  ${status}  "${title}"  images ${imgs.length - broken.length}/${imgs.length}  toc anchors ${anchors.length - missing.length}/${anchors.length}`);
  for (const b of broken) { console.log("  broken image:", b.src); bad++; }
  for (const m of missing) { console.log("  missing anchor:", m); bad++; }
  if (status !== 200) bad++;
}
await page.goto(`${base}/help/road.html#output`, { waitUntil: "networkidle" });
await page.screenshot({ path: new URL("./screenshots/help-road.png", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1") });
await browser.close();
console.log(bad ? `FAIL: ${bad} problem(s)` : "PASS: help pages");
process.exit(bad ? 1 : 0);
