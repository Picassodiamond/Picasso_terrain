/** Cross-section chart (D3, SVG): offset from centre line vs RL. */
import * as d3 from "d3";
import type { Section } from "../api";
import { fmtChainage } from "../ui/dom";

export interface SectionChartOptions {
  vScale: number;
  onHover?: (p: { offset: number; z: number; x: number; y: number } | null) => void;
  formationLevel?: number | null;  // optional horizontal design level line
  design?: { offset: number; z: number }[];   // corridor design polyline (daylight to daylight)
  title?: string;
}

export function renderSection(container: HTMLElement, s: Section, opts: SectionChartOptions): void {
  container.innerHTML = "";
  const width = Math.max(320, container.clientWidth);
  const height = Math.max(180, container.clientHeight);
  const margin = { top: 26, right: 24, bottom: 46, left: 62 };
  const w = width - margin.left - margin.right;
  const h = height - margin.top - margin.bottom;
  const pts = s.offset.map((o, i) => ({ offset: o, z: s.z[i], x: s.xy[i][0], y: s.xy[i][1], source: s.source[i] })).filter((p) => p.z !== null) as { offset: number; z: number; x: number; y: number; source: string }[];
  const design = opts.design || [];
  const svg = d3.select(container).append("svg").attr("width", width).attr("height", height).attr("class", "chart");
  svg.append("text").attr("class", "chart-title").attr("x", margin.left).attr("y", 16).text(opts.title ?? `Cross-section  CH ${fmtChainage(s.chainage)}`);
  if (!pts.length) {
    svg.append("text").attr("x", width / 2).attr("y", height / 2).attr("text-anchor", "middle").attr("fill", "#94a3b8").text("Section outside the TIN");
    return;
  }
  const offMin = Math.min(-s.left, ...design.map((p) => p.offset)), offMax = Math.max(s.right, ...design.map((p) => p.offset));
  const x = d3.scaleLinear().domain([offMin, offMax]).range([0, w]);
  const zmin = Math.min(d3.min(pts, (p) => p.z)!, ...design.map((p) => p.z)), zmax = Math.max(d3.max(pts, (p) => p.z)!, ...design.map((p) => p.z));
  const pxPerM = w / (offMax - offMin);
  const zSpan = Math.max(h / (pxPerM * opts.vScale), (zmax - zmin) * 1.2, 0.5);
  const zMid = (zmin + zmax) / 2;
  const y = d3.scaleLinear().domain([zMid - zSpan / 2, zMid + zSpan / 2]).range([h, 0]);
  const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);
  g.append("g").attr("class", "grid").call(d3.axisLeft(y).ticks(6).tickSize(-w).tickFormat(() => ""));
  g.append("g").attr("class", "grid").attr("transform", `translate(0,${h})`).call(d3.axisBottom(x).ticks(10).tickSize(-h).tickFormat(() => ""));
  g.append("g").attr("class", "axis").attr("transform", `translate(0,${h})`).call(d3.axisBottom(x).ticks(10));
  g.append("g").attr("class", "axis").call(d3.axisLeft(y).ticks(6).tickFormat((d) => Number(d).toFixed(1)));
  g.append("text").attr("class", "axis-title").attr("x", w / 2).attr("y", h + 38).attr("text-anchor", "middle").text("Offset from centre line (m)   ← left | right →");
  g.append("text").attr("class", "axis-title").attr("transform", "rotate(-90)").attr("x", -h / 2).attr("y", -48).attr("text-anchor", "middle").text(`RL (m)  ×${opts.vScale}`);
  // centre line
  g.append("line").attr("class", "centre-line").attr("x1", x(0)).attr("x2", x(0)).attr("y1", 0).attr("y2", h);
  g.append("text").attr("class", "marker-label").attr("x", x(0) + 4).attr("y", 12).text("CL");
  const area = d3.area<{ offset: number; z: number }>().x((p) => x(p.offset)).y0(h).y1((p) => y(p.z));
  const line = d3.line<{ offset: number; z: number }>().x((p) => x(p.offset)).y((p) => y(p.z));
  g.append("path").datum(pts).attr("class", "ground-area").attr("d", area);
  g.append("path").datum(pts).attr("class", "ground-line").attr("d", line);
  g.selectAll(".station").data(pts).enter().append("circle").attr("class", (p) => (p.source === "centre" ? "station centre" : "station"))
    .attr("cx", (p) => x(p.offset)).attr("cy", (p) => y(p.z)).attr("r", (p) => (p.source === "centre" ? 3.5 : 2));
  if (opts.formationLevel != null) {
    g.append("line").attr("class", "design-line").attr("x1", 0).attr("x2", w).attr("y1", y(opts.formationLevel)).attr("y2", y(opts.formationLevel));
  }
  if (design.length > 1) {
    // cut / fill band between design and ground, the design polyline, daylight markers
    const gz = (o: number) => {
      const i = d3.bisector<{ offset: number }, number>((p) => p.offset).left(pts, o);
      if (i <= 0) return pts[0].z; if (i >= pts.length) return pts[pts.length - 1].z;
      const a = pts[i - 1], b = pts[i]; const t = (o - a.offset) / ((b.offset - a.offset) || 1);
      return a.z + t * (b.z - a.z);
    };
    const band = d3.area<{ offset: number; z: number }>().x((p) => x(p.offset)).y0((p) => y(gz(p.offset))).y1((p) => y(p.z));
    g.append("path").datum(design).attr("class", "design-band").attr("d", band);
    g.append("path").datum(design).attr("class", "design-line").attr("d", d3.line<{ offset: number; z: number }>().x((p) => x(p.offset)).y((p) => y(p.z)));
    for (const p of [design[0], design[design.length - 1]]) g.append("rect").attr("class", "daylight").attr("x", x(p.offset) - 3).attr("y", y(p.z) - 3).attr("width", 6).attr("height", 6);
    const c0 = design.reduce((a, b) => (Math.abs(b.offset) < Math.abs(a.offset) ? b : a));
    g.append("text").attr("class", "tip design").attr("x", x(c0.offset) + 4).attr("y", y(c0.z) - 6).text(`design ${c0.z.toFixed(2)}`);
  }
  // RL labels at ends and centre
  const label = (p: { offset: number; z: number }, dx: number, anchor: string) =>
    g.append("text").attr("class", "tip").attr("x", x(p.offset) + dx).attr("y", y(p.z) - 6).attr("text-anchor", anchor).text(p.z.toFixed(2));
  label(pts[0], 0, "start");
  label(pts[pts.length - 1], 0, "end");
  const c = pts.find((p) => p.source === "centre");
  if (c) label(c, 0, "middle");
  // hover
  const cursor = g.append("g").attr("class", "cursor").style("display", "none");
  cursor.append("line").attr("y1", 0).attr("y2", h);
  cursor.append("circle").attr("r", 4);
  const tip = cursor.append("text").attr("class", "tip").attr("dy", -8).attr("x", 6);
  const bisect = d3.bisector<{ offset: number }, number>((p) => p.offset).center;
  svg.append("rect").attr("x", margin.left).attr("y", margin.top).attr("width", w).attr("height", h).attr("fill", "transparent")
    .on("mousemove", (ev) => {
      const [mx] = d3.pointer(ev);
      const off = x.invert(mx - margin.left);
      const i = Math.min(pts.length - 1, Math.max(0, bisect(pts, off)));
      const p = pts[i];
      cursor.style("display", null).attr("transform", `translate(${x(p.offset)},0)`);
      cursor.select("circle").attr("cy", y(p.z));
      tip.attr("y", y(p.z)).text(`${p.offset.toFixed(2)} m  RL ${p.z.toFixed(3)}`);
      opts.onHover?.(p);
    })
    .on("mouseleave", () => { cursor.style("display", "none"); opts.onHover?.(null); });
}
