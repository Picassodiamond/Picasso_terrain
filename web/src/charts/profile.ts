/** Longitudinal profile chart (D3, SVG): ground line, optional design grade line with draggable PVIs,
 *  grade and vertical-curve labels, chainage markers and a hover cursor. */
import * as d3 from "d3";
import type { ProfilePoint } from "../api";
import { fmtChainage } from "../ui/dom";

export interface ProfilePVI { index: number; chainage: number; z: number; length: number; label?: string; K?: number | null; kind?: string }

export interface ProfileChartOptions {
  vScale: number;                 // vertical exaggeration
  onHover?: (p: ProfilePoint | null) => void;
  designLine?: { chainage: number; z: number }[];
  pvis?: ProfilePVI[];
  onPviDrag?: (index: number, chainage: number, z: number, phase: "move" | "end") => void;
  markers?: { chainage: number; label: string }[];   // BC/EC etc.
  cursor?: number | null;         // chainage to highlight
  domain?: [number, number];      // chainage range to show (default: the ground)
}

export function renderProfile(container: HTMLElement, points: ProfilePoint[], opts: ProfileChartOptions): { setCursor: (ch: number | null) => void } {
  container.innerHTML = "";
  const width = Math.max(320, container.clientWidth);
  const height = Math.max(180, container.clientHeight);
  const margin = { top: 18, right: 24, bottom: 46, left: 62 };
  const w = width - margin.left - margin.right;
  const h = height - margin.top - margin.bottom;
  const pts = points.filter((p) => p.z !== null) as (ProfilePoint & { z: number })[];
  const design = opts.designLine || [];
  const svg = d3.select(container).append("svg").attr("width", width).attr("height", height).attr("class", "chart");
  if (!pts.length && design.length < 2) {
    svg.append("text").attr("x", width / 2).attr("y", height / 2).attr("text-anchor", "middle").attr("fill", "#94a3b8").text("No ground data inside the TIN");
    return { setCursor: () => {} };
  }
  const chAll = [...pts.map((p) => p.chainage), ...design.map((p) => p.chainage), ...(opts.pvis || []).map((p) => p.chainage)];
  const zAll = [...pts.map((p) => p.z), ...design.map((p) => p.z), ...(opts.pvis || []).map((p) => p.z)];
  const x = d3.scaleLinear().domain(opts.domain ?? (d3.extent(chAll) as [number, number])).range([0, w]);
  const zmin = d3.min(zAll)!, zmax = d3.max(zAll)!;
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

  if (pts.length) {
    const area = d3.area<ProfilePoint & { z: number }>().x((p) => x(p.chainage)).y0(h).y1((p) => y(p.z));
    const line = d3.line<ProfilePoint & { z: number }>().x((p) => x(p.chainage)).y((p) => y(p.z));
    g.append("path").datum(pts).attr("class", "ground-area").attr("d", area);
    g.append("path").datum(pts).attr("class", "ground-line").attr("d", line);
    g.selectAll(".station").data(pts.filter((p) => p.source === "station")).enter().append("circle").attr("class", "station")
      .attr("cx", (p) => x(p.chainage)).attr("cy", (p) => y(p.z)).attr("r", 2.2);
  }
  // cut / fill shading between design and ground, then the design line
  if (design.length > 1) {
    if (pts.length) {
      const gz = (ch: number) => {
        const i = d3.bisector<{ chainage: number }, number>((p) => p.chainage).left(pts, ch);
        if (i <= 0) return pts[0].z; if (i >= pts.length) return pts[pts.length - 1].z;
        const a = pts[i - 1], b = pts[i]; const t = (ch - a.chainage) / ((b.chainage - a.chainage) || 1);
        return a.z + t * (b.z - a.z);
      };
      const band = d3.area<{ chainage: number; z: number }>().x((p) => x(p.chainage)).y0((p) => y(gz(p.chainage))).y1((p) => y(p.z));
      g.append("path").datum(design).attr("class", "design-band").attr("d", band);
    }
    const dl = d3.line<{ chainage: number; z: number }>().x((p) => x(p.chainage)).y((p) => y(p.z));
    g.append("path").datum(design).attr("class", "design-line").attr("d", dl);
  }
  // PVIs: tangents, labels and drag handles
  const pvis = opts.pvis || [];
  if (pvis.length) {
    const tangent = g.append("path").attr("class", "pvi-tangent").datum(pvis).attr("d", d3.line<ProfilePVI>().x((p) => x(p.chainage)).y((p) => y(p.z)));
    const labels = g.append("g").attr("class", "pvi-labels");
    const drawLabels = () => {
      labels.selectAll("*").remove();
      for (let i = 0; i + 1 < pvis.length; i++) {
        const a = pvis[i], b = pvis[i + 1];
        const grade = (b.z - a.z) / ((b.chainage - a.chainage) || 1) * 100;
        labels.append("text").attr("class", "grade-label").attr("x", x((a.chainage + b.chainage) / 2)).attr("y", y((a.z + b.z) / 2) - 8).attr("text-anchor", "middle")
          .text(`${grade >= 0 ? "+" : ""}${grade.toFixed(2)} %`);
      }
      for (const p of pvis) {
        if (p.length > 0) labels.append("text").attr("class", "pvi-label").attr("x", x(p.chainage)).attr("y", y(p.z) + (p.kind === "crest" ? 16 : -12)).attr("text-anchor", "middle")
          .text(`L ${p.length.toFixed(0)}${p.K ? `  K ${p.K.toFixed(1)}` : ""}`);
      }
    };
    drawLabels();
    const handles = g.selectAll(".pvi").data(pvis).enter().append("circle").attr("class", "pvi").attr("r", 6)
      .attr("cx", (p) => x(p.chainage)).attr("cy", (p) => y(p.z));
    if (opts.onPviDrag) {
      // the first and last PVI are pinned in chainage and only move in level, so they say so
      const rest = (p: ProfilePVI) => (p.index === 0 || p.index === pvis.length - 1 ? "ns-resize" : "move");
      handles.classed("pvi-handle", true).style("cursor", rest).call(d3.drag<SVGCircleElement, ProfilePVI>()
        .on("start", (ev) => { d3.select(ev.sourceEvent.target as SVGCircleElement).style("cursor", "grabbing"); })
        .on("drag", (ev, p) => {
          const first = p.index === 0, last = p.index === pvis.length - 1;
          const ch = first || last ? p.chainage : Math.min(Math.max(x.invert(ev.x), pvis[p.index - 1].chainage + 1), pvis[p.index + 1].chainage - 1);
          const z = y.invert(ev.y);
          p.chainage = ch; p.z = z;
          d3.select(ev.sourceEvent.target).attr("cx", x(ch)).attr("cy", y(z));
          tangent.attr("d", d3.line<ProfilePVI>().x((q) => x(q.chainage)).y((q) => y(q.z)));
          drawLabels();
          opts.onPviDrag?.(p.index, ch, z, "move");
        })
        .on("end", (ev, p) => {
          d3.select(ev.sourceEvent.target as SVGCircleElement).style("cursor", rest(p));
          opts.onPviDrag?.(p.index, p.chainage, p.z, "end");
        }));
    }
  }
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
    if (ch === null || !pts.length) { cursor.style("display", "none"); return; }
    const i = Math.min(pts.length - 1, Math.max(0, bisect(pts, ch)));
    const p = pts[i];
    cursor.style("display", null).attr("transform", `translate(${x(p.chainage)},0)`);
    cursor.select("circle").attr("cy", y(p.z));
    tip.attr("y", y(p.z)).attr("x", x(p.chainage) > w - 120 ? -6 : 6).attr("text-anchor", x(p.chainage) > w - 120 ? "end" : "start")
      .text(`${fmtChainage(p.chainage)}  RL ${p.z.toFixed(3)}`);
  };
  svg.insert("rect", ":first-child").attr("x", margin.left).attr("y", margin.top).attr("width", w).attr("height", h).attr("fill", "transparent")
    .on("mousemove", (ev) => {
      if (!pts.length) return;
      const [mx] = d3.pointer(ev);
      const ch = x.invert(mx - margin.left);
      const i = Math.min(pts.length - 1, Math.max(0, bisect(pts, ch)));
      setCursor(pts[i].chainage);
      opts.onHover?.(pts[i]);
    })
    .on("mouseleave", () => { setCursor(null); opts.onHover?.(null); });
  // the hit rectangle sits under the drawing so PVI handles stay draggable; move it to receive events
  svg.select("rect").raise().lower();
  g.raise();
  svg.select("rect").attr("pointer-events", "all");
  g.selectAll("path, .grid, .axis, text").attr("pointer-events", "none");
  g.selectAll(".pvi").attr("pointer-events", "all");
  if (opts.cursor != null) setCursor(opts.cursor);
  return { setCursor };
}
