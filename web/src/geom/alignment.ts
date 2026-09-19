/** Client-side helpers to draw an alignment returned by the API (elements + key points). */
import type { Alignment, FeatureCollection } from "../api";
import { fmtChainage } from "../ui/dom";

interface Element { kind: string; start_chainage: number; end_chainage: number; start: number[]; end: number[]; radius: number; centre: number[] | null; deflection: number; length: number }

export function pointAt(al: Alignment, ch: number): { x: number; y: number; dir: number } {
  const els = al.elements as Element[];
  if (!els.length) {
    const a = al.ips[0], b = al.ips[al.ips.length - 1];
    return { x: a.x, y: a.y, dir: Math.atan2(b.y - a.y, b.x - a.x) };
  }
  let el = els[els.length - 1];
  if (ch <= els[0].start_chainage) el = els[0];
  else for (const e of els) if (e.start_chainage - 1e-9 <= ch && ch <= e.end_chainage + 1e-9) { el = e; break; }
  if (el.kind === "tangent") {
    const dx = el.end[0] - el.start[0], dy = el.end[1] - el.start[1];
    const L = el.length || Math.hypot(dx, dy) || 1;
    const t = el.length > 0 ? (ch - el.start_chainage) / el.length : 0;
    return { x: el.start[0] + t * dx, y: el.start[1] + t * dy, dir: Math.atan2(dy, dx) };
  }
  const [cx, cy] = el.centre!;
  const r = el.radius;
  const a0 = Math.atan2(el.start[1] - cy, el.start[0] - cx);
  const s = Math.min(Math.max(ch - el.start_chainage, 0), el.length);
  const sgn = el.deflection > 0 ? 1 : -1;
  const a = a0 + (sgn * s) / r;
  return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a), dir: a + (sgn * Math.PI) / 2 };
}

export function densify(al: Alignment, maxSeg = 1.0, maxAngleDeg = 2): number[][] {
  const out: number[][] = [];
  for (const el of al.elements as Element[]) {
    let n: number;
    if (el.kind === "tangent") n = Math.max(1, Math.ceil(el.length / maxSeg));
    else n = Math.max(2, Math.ceil(el.length / Math.min(maxSeg * 4, (maxAngleDeg * Math.PI / 180) * el.radius)));
    for (let k = 0; k < n; k++) {
      const p = pointAt(al, el.start_chainage + (el.length * k) / n);
      out.push([p.x, p.y]);
    }
  }
  const e = pointAt(al, al.end_chainage);
  out.push([e.x, e.y]);
  return out;
}

/** Same shape as the server's geometry.geojson so MapLayers.setAlignment can consume either. */
export function alignmentGeoJSON(al: Alignment, chainageInterval = 20, tickLength = 5): FeatureCollection {
  const feats: any[] = [];
  if (al.elements.length) {
    feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: densify(al) }, properties: { kind: "centreline", name: al.name, length: al.length } });
  }
  for (const k of al.key_points) {
    feats.push({ type: "Feature", geometry: { type: "Point", coordinates: [k.x, k.y] },
      properties: { kind: k.kind, index: k.index, label: k.label ?? "", chainage: k.chainage, chainage_label: fmtChainage(k.chainage), radius: k.radius, valid: k.valid ?? true } });
  }
  if (chainageInterval > 0 && al.elements.length) {
    const s0 = al.start_chainage, s1 = al.end_chainage;
    const chs: number[] = [s0];
    for (let c = Math.ceil(s0 / chainageInterval) * chainageInterval; c < s1 - 1e-3; c += chainageInterval) if (c - s0 > 1e-3) chs.push(c);
    chs.push(s1);
    for (const ch of chs) {
      const p = pointAt(al, ch);
      const x2 = p.x + tickLength * Math.sin(p.dir), y2 = p.y - tickLength * Math.cos(p.dir);
      feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: [[p.x, p.y], [x2, y2]] },
        properties: { kind: "chainage", chainage: ch, label: fmtChainage(ch), angle: (Math.atan2(y2 - p.y, x2 - p.x) * 180) / Math.PI } });
    }
  }
  return { type: "FeatureCollection", features: feats };
}
