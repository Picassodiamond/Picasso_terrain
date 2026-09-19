/** Longitudinal profile chart (D3, SVG). */
import * as d3 from "d3";
import type { ProfilePoint } from "../api";
import { fmtChainage } from "../ui/dom";

export interface ProfileChartOptions {
  vScale: number;                 // vertical exaggeration
  onHover?: (p: ProfilePoint | null) => void;
  designLine?: { chainage: number; z: number }[];
  markers?: { chainage: number; label: string }[];   // BC/EC etc.
  cursor?: number | null;         // chainage to highlight
}

export function renderProfile(container: HTMLElement, points: ProfilePoint[], opts: ProfileChartOptions): { setCursor: (ch: number | null) => void } {
  container.innerHTML = "";
  const width = Math.max(320, container.clientWidth);
  const height = Math.max(180, container.clientHeight);
  const margin = { top: 18, right: 24, bottom: 46, left: 62 };
  const w = width - margin.left - margin.right;
  const h = height - margin.top - margin.bottom;
  const pts = points.filter((p) => p.z !== null) as (ProfilePoint & { z: number })[];
  const svg = d3.select(container).append("svg").attr("width", width).attr("height", height).attr("class", "chart");
  if (!pts.length) {
    svg.append("text").attr("x", width / 2).attr("y", height / 2).attr("text-anchor", "middle").attr("fill", "#94a3b8").text("No ground data inside the TIN");
    return { setCursor: () => {} };
  }
  const x = d3.scaleLinear().domain(d3.extent(points, (p) => p.chainage) as [number, number]).range([0, w]);
  const zmin = d3.min(pts, (p) => p.z)!, zmax = d3.max(pts, (p) => p.z)!;
  // keep the requested exaggeration: metres of RL per metre of chainage = 1/vScale
  const chRange = (x.domain()[1] - x.domain()[0]) || 1;
  const pxPerM = w / chRange;
  const zSpanNeeded = h / (pxPerM * opts.vScale);
  const zMid = (zmin + zmax) / 2;
  const zSpan = Math.max(zSpanNeeded, (zmax - zmin) * 1.15, 1);
  const datum = Math.floor((zMid - zSpan / 2) / 1) * 1;
  const y = d3.scaleLinear().domain([datum, datum + zSpan]).range([h, 0]);

  const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);
  g.append("g").attr("class", "grid").call(d3.axisLeft(y).ticks(6).tickSize(-w).tickFormat(() => ""));
  g.append("g").attr("class", "grid").attr("transform", `translate(0,${h})`).call(d3.axisBottom(x).ticks(10).tickSize(-h).tickFormat(() => ""));
  g.append("g").attr("class", "axis").attr("transform", `translate(0,${h})`).call(d3.axisBottom(x).ticks(8).tickFormat((d) => fmtChainage(Number(d), 0)));
  g.append("g").attr("class", "axis").call(d3.axisLeft(y).ticks(6).tickFormat((d) => Number(d).toFixed(1)));
  g.append("text").attr("class", "axis-title").attr("x", w / 2).attr("y", h + 38).attr("text-anchor", "middle").text("Chainage");
  g.append("text").attr("class", "axis-title").attr("transform", "rotate(-90)").attr("x", -h / 2).attr("y", -48).attr("text-anchor", "middle").text(`RL (m)  ×${opts.vScale}`);

  const area = d3.area<ProfilePoint & { z: number }>().x((p) => x(p.chainage)).y0(h).y1((p) => y(p.z));
  const line = d3.line<ProfilePoint & { z: number }>().x((p) => x(p.chainage)).y((p) => y(p.z));
  g.append("path").datum(pts).attr("class", "ground-area").attr("d", area);
  g.append("path").datum(pts).attr("class", "ground-line").attr("d", line);
  if (opts.designLine && opts.designLine.length > 1) {
    const dl = d3.line<{ chainage: number; z: number }>().x((p) => x(p.chainage)).y((p) => y(p.z));
    g.append("path").datum(opts.designLine).attr("class", "design-line").attr("d", dl);
  }
  // stations
  g.selectAll(".station").data(pts.filter((p) => p.source === "station")).enter().append("circle").attr("class", "station")
    .attr("cx", (p) => x(p.chainage)).attr("cy", (p) => y(p.z)).attr("r", 2.2);
  for (const m of opts.markers || []) {
    if (m.chainage < x.domain()[0] || m.chainage > x.domain()[1]) continue;
    g.append("line").attr("class", "marker").attr("x1", x(m.chainage)).attr("x2", x(m.chainage)).attr("y1", 0).attr("y2", h);
    g.append("text").attr("class", "marker-label").attr("x", x(m.chainage) + 3).attr("y", 10).text(m.label);
  }
  // hover
  const cursor = g.append("g").attr("class", "cursor").style("display", "none");
  cursor.append("line").attr("y1", 0).attr("y2", h);
  cursor.append("circle").attr("r", 4);
  const tip = cursor.append("text").attr("class", "tip").attr("dy", -8);
  const bisect = d3.bisector<ProfilePoint & { z: number }, number>((p) => p.chainage).center;
  const setCursor = (ch: number | null) => {
    if (ch === null) { cursor.style("display", "none"); return; }
    const i = Math.min(pts.length - 1, Math.max(0, bisect(pts, ch)));
    const p = pts[i];
    cursor.style("display", null).attr("transform", `translate(${x(p.chainage)},0)`);
    cursor.select("circle").attr("cy", y(p.z));
    tip.attr("y", y(p.z)).attr("x", x(p.chainage) > w - 120 ? -6 : 6).attr("text-anchor", x(p.chainage) > w - 120 ? "end" : "start")
      .text(`${fmtChainage(p.chainage)}  RL ${p.z.toFixed(3)}`);
  };
  svg.append("rect").attr("x", margin.left).attr("y", margin.top).attr("width", w).attr("height", h).attr("fill", "transparent")
    .on("mousemove", (ev) => {
      const [mx] = d3.pointer(ev);
      const ch = x.invert(mx - margin.left);
      const i = Math.min(pts.length - 1, Math.max(0, bisect(pts, ch)));
      setCursor(pts[i].chainage);
      opts.onHover?.(pts[i]);
    })
    .on("mouseleave", () => { setCursor(null); opts.onHover?.(null); });
  if (opts.cursor != null) setCursor(opts.cursor);
  return { setCursor };
}
