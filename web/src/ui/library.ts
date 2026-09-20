/** Library: the asset catalogue - every terrain model and design the user may see, on a small
 *  lon/lat map plus a searchable list, with Open and Clone. Route #/library. */
import { api, ApiError, type CatalogueItem } from "../api";
import { store, toast } from "../state";
import { button, el, fmt, select } from "./dom";
import { designHash, terrainHash } from "../modules/registry";

export async function renderLibrary(root: HTMLElement): Promise<void> {
  root.innerHTML = "";
  const page = el("div", { class: "projects-page" });
  root.appendChild(page);
  const q = el("input", { type: "text", placeholder: "search name, description or tag", style: "max-width:320px" });
  const kind = select([{ value: "", label: "terrain and designs" }, { value: "tin", label: "terrain models" }, { value: "design", label: "designs" }], "");
  const count = el("span", { class: "muted" });
  page.appendChild(el("div", { style: "display:flex;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap" },
    el("h1", { style: "color:var(--accent);cursor:pointer", onClick: () => { location.hash = ""; } }, "▲ Picasso LandMesh"),
    el("span", { class: "muted" }, "Library · terrain models and designs collected over time"),
    el("span", { style: "flex:1" }), q, kind, count,
    button("Projects", () => { location.hash = ""; }, "btn small")));

  const mapHost = el("div", { class: "lib-map" });
  const canvas = document.createElement("canvas");
  mapHost.appendChild(canvas);
  const list = el("div", { class: "lib-list" });
  page.appendChild(el("div", { class: "lib-layout" }, mapHost, list));

  let items: CatalogueItem[] = [];
  let hover: string | null = null;

  const draw = () => {
    const w = mapHost.clientWidth, h = mapHost.clientHeight;
    if (!w || !h) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = w * dpr; canvas.height = h * dpr; canvas.style.width = `${w}px`; canvas.style.height = `${h}px`;
    const ctx = canvas.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#0b1220"; ctx.fillRect(0, 0, w, h);
    const geo = items.filter((i) => i.lon != null && i.lat != null);
    if (!geo.length) {
      ctx.fillStyle = "#64748b"; ctx.font = "13px system-ui"; ctx.textAlign = "center";
      ctx.fillText(items.length ? "No georeferenced assets to map (local-grid projects have no footprint)" : "Nothing in the library yet", w / 2, h / 2);
      return;
    }
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const i of geo) { x0 = Math.min(x0, i.lon!); x1 = Math.max(x1, i.lon!); y0 = Math.min(y0, i.lat!); y1 = Math.max(y1, i.lat!); }
    const padX = Math.max((x1 - x0) * 0.3, 0.02), padY = Math.max((y1 - y0) * 0.3, 0.02);
    x0 -= padX; x1 += padX; y0 -= padY; y1 += padY;
    const cosLat = Math.cos(((y0 + y1) / 2) * Math.PI / 180);
    const sx = w / ((x1 - x0) * cosLat), sy = h / (y1 - y0), s = Math.min(sx, sy) * 0.95;
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    const toS = (lon: number, lat: number): [number, number] => [w / 2 + (lon - cx) * cosLat * s, h / 2 - (lat - cy) * s];
    // graticule
    ctx.strokeStyle = "#162033"; ctx.lineWidth = 1; ctx.fillStyle = "#3b4a63"; ctx.font = "10px system-ui"; ctx.textAlign = "left";
    const step = (x1 - x0) > 2 ? 1 : (x1 - x0) > 0.5 ? 0.25 : 0.05;
    for (let lon = Math.ceil(x0 / step) * step; lon <= x1; lon += step) { const [px] = toS(lon, cy); ctx.beginPath(); ctx.moveTo(px, 0); ctx.lineTo(px, h); ctx.stroke(); ctx.fillText(`${lon.toFixed(2)}°E`, px + 3, h - 4); }
    for (let lat = Math.ceil(y0 / step) * step; lat <= y1; lat += step) { const [, py] = toS(cx, lat); ctx.beginPath(); ctx.moveTo(0, py); ctx.lineTo(w, py); ctx.stroke(); ctx.fillText(`${lat.toFixed(2)}°N`, 4, py - 3); }
    for (const i of geo) {
      const on = i.id === hover;
      const col = i.kind === "tin" ? (on ? "#fde68a" : "#f59e0b") : (on ? "#bae6fd" : "#38bdf8");
      if (i.footprint?.coordinates?.[0]) {
        ctx.beginPath();
        i.footprint.coordinates[0].forEach((c: number[], k: number) => { const [px, py] = toS(c[0], c[1]); if (k === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py); });
        ctx.closePath();
        ctx.fillStyle = col + "33"; ctx.fill(); ctx.strokeStyle = col; ctx.lineWidth = on ? 2.5 : 1.2; ctx.stroke();
      }
      const [px, py] = toS(i.lon!, i.lat!);
      ctx.fillStyle = col; ctx.beginPath(); ctx.arc(px, py, on ? 6 : 4, 0, Math.PI * 2); ctx.fill();
      if (on) { ctx.fillStyle = "#e5e7eb"; ctx.font = "11px system-ui"; ctx.fillText(i.name, px + 8, py - 6); }
    }
  };
  new ResizeObserver(draw).observe(mapHost);

  const render = () => {
    list.innerHTML = "";
    count.textContent = `${items.length} asset${items.length === 1 ? "" : "s"}`;
    if (!items.length) list.appendChild(el("p", { class: "muted" }, "Nothing matches. Terrain models appear here when a TIN is built; designs when they are created."));
    for (const i of items) {
      const card = el("div", { class: "card lib-card", onMouseenter: () => { hover = i.id; draw(); }, onMouseleave: () => { hover = null; draw(); } },
        el("div", { style: "display:flex;gap:8px;align-items:center;flex-wrap:wrap" },
          el("span", { class: "badge" }, i.kind === "tin" ? "terrain" : i.module || "design"), el("b", {}, i.name),
          i.project_status === "archived" ? el("span", { class: "badge warn" }, "archived") : null,
          el("span", { class: "spacer", style: "flex:1" }), el("span", { class: "muted" }, i.my_role || "")),
        el("div", { class: "meta" }, [
          i.crs ? `CRS ${i.crs}` : null,
          i.points ? `${i.points.toLocaleString()} points` : null,
          i.triangles ? `${i.triangles.toLocaleString()} triangles` : null,
          i.spacing ? `~${fmt(i.spacing, 1)} m spacing` : null,
          i.z_min != null ? `RL ${fmt(i.z_min, 0)}–${fmt(i.z_max, 0)}` : null,
          i.lon != null ? `${fmt(i.lat, 4)}°N ${fmt(i.lon, 4)}°E` : "local grid",
        ].filter(Boolean).join(" · ")),
        i.description ? el("div", { class: "meta" }, i.description) : null,
        i.tags?.length ? el("div", { class: "meta" }, ...i.tags.map((t) => el("span", { class: "badge" }, String(t)))) : null,
        el("div", { class: "meta" }, `updated ${new Date(i.updated).toLocaleString()}`),
        el("div", { class: "btn-row" },
          button("Open", () => { location.hash = i.kind === "design" ? designHash(i.project_id, { module: i.module, id: i.ref_id }) : terrainHash(i.project_id); }, "btn small primary"),
          button("Clone into new project", async () => {
            const name = prompt("Name for the copy", `${i.project_name} (copy)`);
            if (name === null) return;
            try {
              const p = await api.catalogue.clone(i.id, name);
              toast(`Cloned into "${p.name}"`, "ok");
              location.hash = terrainHash(p.id);
            } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
          }, "btn small")));
      list.appendChild(card);
    }
    draw();
  };

  const load = async () => {
    try {
      items = await api.catalogue.list({ q: q.value.trim() || undefined, kind: kind.value || undefined });
      render();
    } catch (e) { toast(e instanceof ApiError ? e.detail : String(e), "error"); }
  };
  let t: number | null = null;
  q.addEventListener("input", () => { if (t) window.clearTimeout(t); t = window.setTimeout(load, 250); });
  kind.addEventListener("change", load);
  void store; // the page reads the user from cookies via the API only
  await load();
}
