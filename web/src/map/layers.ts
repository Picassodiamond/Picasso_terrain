/** Map layers: points, lines, TIN mesh, contours, alignment, sections, comment pins, hover marker. */
import * as Cesium from "cesium";
import type { Alignment, Comment, ContourStyle, FeatureCollection } from "../api";
import { fmtChainage } from "../ui/dom";
import type { Frame } from "./coords";

const FAR = Number.POSITIVE_INFINITY;
const LABEL_FONT = "12px 'Segoe UI', system-ui, sans-serif";

export type PickId =
  | { type: "point"; fid: number; id: string; z: number; remark: string }
  | { type: "ip"; index: number }
  | { type: "comment"; id: string }
  | { type: "section"; index: number }
  | { type: "line"; fid: number; kind: string };

const LINE_COLORS: Record<string, string> = { feature: "#38bdf8", boundary: "#e879f9", void: "#fb923c", contour: "#a3e635" };

/** Elevation colour ramp (terrain-like), t in 0..1 -> [r,g,b] 0..255 */
export function rampColor(t: number, ramp: "terrain" | "viridis" | "grey" = "terrain"): [number, number, number] {
  t = Math.min(1, Math.max(0, t));
  const stops: [number, [number, number, number]][] =
    ramp === "viridis"
      ? [[0, [68, 1, 84]], [0.25, [59, 82, 139]], [0.5, [33, 145, 140]], [0.75, [94, 201, 98]], [1, [253, 231, 37]]]
      : ramp === "grey"
        ? [[0, [40, 40, 40]], [1, [230, 230, 230]]]
        : [[0, [34, 102, 51]], [0.25, [122, 168, 84]], [0.5, [222, 208, 128]], [0.75, [166, 110, 62]], [1, [245, 245, 245]]];
  for (let i = 1; i < stops.length; i++) {
    if (t <= stops[i][0]) {
      const [t0, c0] = stops[i - 1];
      const [t1, c1] = stops[i];
      const u = (t - t0) / (t1 - t0 || 1);
      return [c0[0] + (c1[0] - c0[0]) * u, c0[1] + (c1[1] - c0[1]) * u, c0[2] + (c1[2] - c0[2]) * u];
    }
  }
  return stops[stops.length - 1][1];
}

export class MapLayers {
  private scene: Cesium.Scene;
  private frame: Frame;
  points = new Cesium.PointPrimitiveCollection();
  pointLabels = new Cesium.LabelCollection();
  lines = new Cesium.PolylineCollection();
  contours = new Cesium.PolylineCollection();
  contourLabels = new Cesium.LabelCollection();
  alignmentLines = new Cesium.PolylineCollection();
  alignmentPoints = new Cesium.PointPrimitiveCollection();
  alignmentLabels = new Cesium.LabelCollection();
  chainageLabels = new Cesium.LabelCollection();
  chainageTicks = new Cesium.PolylineCollection();
  keyPoints = new Cesium.PointPrimitiveCollection();
  keyPointLabels = new Cesium.LabelCollection();
  sections = new Cesium.PolylineCollection();
  markers = new Cesium.PointPrimitiveCollection();
  commentPins = new Cesium.PointPrimitiveCollection();
  commentLabels = new Cesium.LabelCollection();
  issuePins = new Cesium.PointPrimitiveCollection();
  tin: Cesium.Primitive | null = null;
  tinWire: Cesium.Primitive | null = null;
  hull: Cesium.Polyline | null = null;
  private hoverMarker: Cesium.PointPrimitive | null = null;
  private sectionPolys: Cesium.Polyline[] = [];
  private draftLine: Cesium.Polyline | null = null;

  constructor(scene: Cesium.Scene, frame: Frame) {
    this.scene = scene;
    this.frame = frame;
    for (const p of [this.lines, this.contours, this.alignmentLines, this.sections, this.points, this.alignmentPoints, this.markers, this.commentPins, this.issuePins, this.pointLabels, this.contourLabels, this.alignmentLabels, this.commentLabels, this.chainageTicks, this.chainageLabels, this.keyPoints, this.keyPointLabels]) {
      scene.primitives.add(p);
    }
  }

  setFrame(frame: Frame): void {
    this.frame = frame;
  }

  private c(x: number, y: number, z = 0): Cesium.Cartesian3 {
    return this.frame.toCartesian(x, y, z);
  }

  // ------------------------------------------------------------------ points
  setPoints(fc: FeatureCollection, showLabels: boolean): void {
    this.points.removeAll();
    this.pointLabels.removeAll();
    const color = Cesium.Color.fromCssColorString("#fde68a");
    for (const f of fc.features) {
      const [x, y, z] = f.geometry.coordinates;
      const p = f.properties;
      const pos = this.c(x, y, z ?? 0);
      this.points.add({ position: pos, color, pixelSize: 5, outlineColor: Cesium.Color.BLACK, outlineWidth: 1, disableDepthTestDistance: FAR,
        id: { type: "point", fid: p.fid, id: p.id, z: p.z, remark: p.remark } as PickId });
      if (showLabels) {
        this.pointLabels.add({ position: pos, text: `${p.id || ""}${p.id && p.z != null ? "\n" : ""}${p.z != null ? Number(p.z).toFixed(2) : ""}`.trim(),
          font: "11px system-ui", fillColor: Cesium.Color.fromCssColorString("#fef3c7"), outlineColor: Cesium.Color.BLACK, outlineWidth: 2,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE, pixelOffset: new Cesium.Cartesian2(6, -6), horizontalOrigin: Cesium.HorizontalOrigin.LEFT,
          disableDepthTestDistance: FAR, distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 600) });
      }
    }
    this.scene.requestRender();
  }

  // ------------------------------------------------------------------ lines
  setLines(fc: FeatureCollection, zAt?: (x: number, y: number) => number): void {
    this.lines.removeAll();
    this.hull = null;
    this.draftLine = null;
    for (const f of fc.features) {
      const coords: number[][] = f.geometry.coordinates;
      if (coords.length < 2) continue;
      const kind = f.properties.kind || "feature";
      // 2-D lines (boundary / void polygons, Z = 0) are draped on the TIN when it is available
      const positions = coords.map((c) => {
        const z = c[2] && c[2] !== 0 ? c[2] : zAt ? zAt(c[0], c[1]) : 0;
        return this.c(c[0], c[1], z + 0.15);
      });
      this.lines.add({ positions, width: kind === "boundary" ? 3 : 2,
        material: Cesium.Material.fromType("Color", { color: Cesium.Color.fromCssColorString(LINE_COLORS[kind] || "#38bdf8") }),
        id: { type: "line", fid: f.properties.fid, kind } as PickId });
    }
    this.scene.requestRender();
  }

  setDraftLine(coords: number[][] | null, color = "#f472b6"): void {
    if (this.draftLine) { this.lines.remove(this.draftLine); this.draftLine = null; }
    if (coords && coords.length >= 2) {
      this.draftLine = this.lines.add({ positions: coords.map((c) => this.c(c[0], c[1], (c[2] ?? 0) + 0.3)), width: 3,
        material: Cesium.Material.fromType("PolylineDash", { color: Cesium.Color.fromCssColorString(color) }) });
    }
    this.scene.requestRender();
  }

  // ------------------------------------------------------------------ TIN
  /** mesh.bin -> shaded primitive with per-vertex colours. */
  setTin(buf: ArrayBuffer | null, style: { mode: "ramp" | "flat" | "wire"; opacity: number }, zRangeOverride?: [number, number]): void {
    if (this.tin) { this.scene.primitives.remove(this.tin); this.tin = null; }
    if (this.tinWire) { this.scene.primitives.remove(this.tinWire); this.tinWire = null; }
    if (!buf) { this.scene.requestRender(); return; }
    const dv = new DataView(buf);
    if (String.fromCharCode(dv.getUint8(0), dv.getUint8(1), dv.getUint8(2), dv.getUint8(3)) !== "PLMM") throw new Error("bad mesh");
    const n = dv.getUint32(8, true);
    const m = dv.getUint32(12, true);
    const dtype = dv.getUint32(16, true);
    const ox = dv.getFloat64(20, true), oy = dv.getFloat64(28, true), oz = dv.getFloat64(36, true);
    let off = 44;
    const xyz = new Float64Array(n * 3);
    if (dtype === 0) {
      const f32 = new Float32Array(buf, off, n * 3);
      for (let i = 0; i < n; i++) { xyz[i * 3] = f32[i * 3] + ox; xyz[i * 3 + 1] = f32[i * 3 + 1] + oy; xyz[i * 3 + 2] = f32[i * 3 + 2] + oz; }
      off += n * 12;
    } else {
      const f64 = new Float64Array(buf.slice(off, off + n * 24));
      xyz.set(f64);
      off += n * 24;
    }
    const indices = new Uint32Array(buf.slice(off, off + m * 12));

    // positions in Cesium space
    const positions = new Float64Array(n * 3);
    let zmin = Infinity, zmax = -Infinity;
    for (let i = 0; i < n; i++) {
      const z = xyz[i * 3 + 2];
      if (z < zmin) zmin = z; if (z > zmax) zmax = z;
      const c = this.c(xyz[i * 3], xyz[i * 3 + 1], z);
      positions[i * 3] = c.x; positions[i * 3 + 1] = c.y; positions[i * 3 + 2] = c.z;
    }
    if (zRangeOverride) [zmin, zmax] = zRangeOverride;
    // vertex normals from faces (in project space: x,y,z) for baked hillshade
    const nx = new Float32Array(n), ny = new Float32Array(n), nz = new Float32Array(n);
    for (let t = 0; t < m; t++) {
      const a = indices[t * 3], b = indices[t * 3 + 1], c2 = indices[t * 3 + 2];
      const ax = xyz[a * 3], ay = xyz[a * 3 + 1], az = xyz[a * 3 + 2];
      const ux = xyz[b * 3] - ax, uy = xyz[b * 3 + 1] - ay, uz = xyz[b * 3 + 2] - az;
      const vx = xyz[c2 * 3] - ax, vy = xyz[c2 * 3 + 1] - ay, vz = xyz[c2 * 3 + 2] - az;
      const fx = uy * vz - uz * vy, fy = uz * vx - ux * vz, fz = ux * vy - uy * vx;
      for (const k of [a, b, c2]) { nx[k] += fx; ny[k] += fy; nz[k] += fz; }
    }
    const light = [-0.5, 0.35, 0.79]; // NW-ish sun
    const lightLen = Math.hypot(...light);
    const colors = new Uint8Array(n * 4);
    const alpha = Math.round(255 * Math.min(1, Math.max(0.05, style.opacity)));
    for (let i = 0; i < n; i++) {
      const len = Math.hypot(nx[i], ny[i], nz[i]) || 1;
      const shade = Math.max(0, (nx[i] * light[0] + ny[i] * light[1] + nz[i] * light[2]) / (len * lightLen));
      const s = 0.45 + 0.55 * shade;
      let base: [number, number, number] = [150, 150, 150];
      if (style.mode === "ramp") base = rampColor(zmax > zmin ? (xyz[i * 3 + 2] - zmin) / (zmax - zmin) : 0.5);
      colors[i * 4] = Math.round(base[0] * s); colors[i * 4 + 1] = Math.round(base[1] * s); colors[i * 4 + 2] = Math.round(base[2] * s); colors[i * 4 + 3] = alpha;
    }
    const attrs = new Cesium.GeometryAttributes() as any;
    attrs.position = new Cesium.GeometryAttribute({ componentDatatype: Cesium.ComponentDatatype.DOUBLE, componentsPerAttribute: 3, values: positions });
    attrs.color = new Cesium.GeometryAttribute({ componentDatatype: Cesium.ComponentDatatype.UNSIGNED_BYTE, componentsPerAttribute: 4, normalize: true, values: colors });
    const geometry = new Cesium.Geometry({
      attributes: attrs,
      indices,
      primitiveType: Cesium.PrimitiveType.TRIANGLES,
      boundingSphere: Cesium.BoundingSphere.fromVertices(Array.from(positions)),
    });
    const translucent = alpha < 255;
    this.tin = new Cesium.Primitive({
      geometryInstances: new Cesium.GeometryInstance({ geometry }),
      appearance: new Cesium.PerInstanceColorAppearance({ flat: true, translucent, closed: false }),
      asynchronous: false,
      allowPicking: true,
    });
    this.scene.primitives.add(this.tin);
    if (style.mode === "wire") {
      const wireIdx = new Uint32Array(m * 6);
      for (let t = 0; t < m; t++) {
        const a = indices[t * 3], b = indices[t * 3 + 1], c2 = indices[t * 3 + 2];
        wireIdx.set([a, b, b, c2, c2, a], t * 6);
      }
      const wc = new Uint8Array(n * 4).fill(255);
      for (let i = 0; i < n; i++) { wc[i * 4] = 250; wc[i * 4 + 1] = 204; wc[i * 4 + 2] = 21; }
      const wattrs = new Cesium.GeometryAttributes() as any;
      wattrs.position = new Cesium.GeometryAttribute({ componentDatatype: Cesium.ComponentDatatype.DOUBLE, componentsPerAttribute: 3, values: positions });
      wattrs.color = new Cesium.GeometryAttribute({ componentDatatype: Cesium.ComponentDatatype.UNSIGNED_BYTE, componentsPerAttribute: 4, normalize: true, values: wc });
      const wgeom = new Cesium.Geometry({
        attributes: wattrs,
        indices: wireIdx, primitiveType: Cesium.PrimitiveType.LINES, boundingSphere: geometry.boundingSphere,
      });
      this.tinWire = new Cesium.Primitive({ geometryInstances: new Cesium.GeometryInstance({ geometry: wgeom }),
        appearance: new Cesium.PerInstanceColorAppearance({ flat: true, translucent: false }), asynchronous: false, allowPicking: false });
      this.scene.primitives.add(this.tinWire);
    }
    this.scene.requestRender();
  }

  setHull(fc: FeatureCollection | null): void {
    if (this.hull) { this.lines.remove(this.hull); this.hull = null; }
    if (!fc || !fc.features.length) return;
    const ring: number[][] = fc.features[0].geometry.coordinates[0];
    this.hull = this.lines.add({ positions: ring.map((c) => this.c(c[0], c[1], 0.2)), width: 1.5,
      material: Cesium.Material.fromType("PolylineDash", { color: Cesium.Color.fromCssColorString("#94a3b8") }) });
    this.scene.requestRender();
  }

  setIssues(fc: FeatureCollection | null): void {
    this.issuePins.removeAll();
    if (!fc) return;
    for (const f of fc.features) {
      const [x, y] = f.geometry.coordinates;
      const crossing = f.properties.kind === "crossing_features";
      this.issuePins.add({ position: this.c(x, y, 0.5), pixelSize: crossing ? 10 : 6, color: Cesium.Color.fromCssColorString(crossing ? "#ef4444" : "#f97316"),
        outlineColor: Cesium.Color.WHITE, outlineWidth: 1, disableDepthTestDistance: FAR });
    }
    this.scene.requestRender();
  }

  // ------------------------------------------------------------------ contours
  setContours(sets: { fc: FeatureCollection; style: Partial<ContourStyle>; labels: FeatureCollection | null; zRange?: [number, number] }[], showLabels: boolean): void {
    this.contours.removeAll();
    this.contourLabels.removeAll();
    for (const { fc, style, labels, zRange } of sets) {
      const major = Cesium.Color.fromCssColorString(style.major_color || "#c2410c").withAlpha(style.opacity ?? 1);
      const minor = Cesium.Color.fromCssColorString(style.minor_color || "#f59e0b").withAlpha(style.opacity ?? 1);
      for (const f of fc.features) {
        const coords: number[][] = f.geometry.coordinates;
        if (coords.length < 2) continue;
        const isMajor = !!f.properties.major;
        let color = isMajor ? major : minor;
        if (style.ramp && zRange && zRange[1] > zRange[0]) {
          const [r, g, b] = rampColor((f.properties.level - zRange[0]) / (zRange[1] - zRange[0]), style.ramp as any);
          color = new Cesium.Color(r / 255, g / 255, b / 255, style.opacity ?? 1);
        }
        this.contours.add({ positions: coords.map((c) => this.c(c[0], c[1], (c[2] ?? f.properties.level) + 0.1)),
          width: isMajor ? (style.major_width ?? 2) : (style.minor_width ?? 1), material: Cesium.Material.fromType("Color", { color }) });
      }
      if (showLabels && labels && (style.show_labels ?? true)) {
        for (const f of labels.features) {
          const [x, y, z] = f.geometry.coordinates;
          this.contourLabels.add({ position: this.c(x, y, (z ?? 0) + 0.3), text: f.properties.text, font: LABEL_FONT,
            fillColor: Cesium.Color.fromCssColorString(style.major_color || "#fff"), outlineColor: Cesium.Color.BLACK, outlineWidth: 3,
            style: Cesium.LabelStyle.FILL_AND_OUTLINE, disableDepthTestDistance: FAR, scale: 0.9,
            distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 2500) });
        }
      }
    }
    this.scene.requestRender();
  }

  // ------------------------------------------------------------------ alignment
  setAlignment(geom: FeatureCollection | null, alignment: Alignment | null, opts: { editable: boolean; draftIps?: { x: number; y: number; radius: number; label: string }[]; zAt?: (x: number, y: number) => number }): void {
    this.alignmentLines.removeAll();
    this.alignmentPoints.removeAll();
    this.alignmentLabels.removeAll();
    this.chainageTicks.removeAll();
    this.chainageLabels.removeAll();
    this.keyPoints.removeAll();
    this.keyPointLabels.removeAll();
    const zAt = opts.zAt ?? (() => 0);
    const ips = opts.draftIps ?? alignment?.ips ?? [];
    // IP polygon (tangent lines between IPs)
    if (ips.length >= 2) {
      this.alignmentLines.add({ positions: ips.map((p) => this.c(p.x, p.y, zAt(p.x, p.y) + 0.4)), width: 1,
        material: Cesium.Material.fromType("PolylineDash", { color: Cesium.Color.fromCssColorString("#94a3b8").withAlpha(0.7) }) });
    }
    ips.forEach((p, i) => {
      const pos = this.c(p.x, p.y, zAt(p.x, p.y) + 0.6);
      this.alignmentPoints.add({ position: pos, pixelSize: opts.editable ? 11 : 8, color: Cesium.Color.fromCssColorString(p.radius > 0 ? "#22d3ee" : "#f8fafc"),
        outlineColor: Cesium.Color.fromCssColorString("#0f172a"), outlineWidth: 2, disableDepthTestDistance: FAR, id: { type: "ip", index: i } as PickId });
      this.alignmentLabels.add({ position: pos, text: `IP ${p.label || i}${p.radius > 0 ? `  R=${p.radius}` : ""}`, font: LABEL_FONT,
        fillColor: Cesium.Color.WHITE, outlineColor: Cesium.Color.BLACK, outlineWidth: 3, style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        pixelOffset: new Cesium.Cartesian2(0, -16), disableDepthTestDistance: FAR, scale: 0.9 });
    });
    if (geom) {
      for (const f of geom.features) {
        const k = f.properties.kind;
        if (!f.geometry) continue;
        if (k === "centreline") {
          const coords: number[][] = f.geometry.coordinates;
          this.alignmentLines.add({ positions: coords.map((c) => this.c(c[0], c[1], zAt(c[0], c[1]) + 0.5)), width: 4,
            material: Cesium.Material.fromType("Color", { color: Cesium.Color.fromCssColorString(alignment?.valid === false ? "#ef4444" : "#e11d48") }) });
        } else if (k === "chainage") {
          const [a, b] = f.geometry.coordinates;
          this.chainageTicks.add({ positions: [this.c(a[0], a[1], zAt(a[0], a[1]) + 0.5), this.c(b[0], b[1], zAt(b[0], b[1]) + 0.5)], width: 1.5,
            material: Cesium.Material.fromType("Color", { color: Cesium.Color.WHITE }) });
          this.chainageLabels.add({ position: this.c(b[0], b[1], zAt(b[0], b[1]) + 0.5), text: f.properties.label, font: "11px system-ui",
            fillColor: Cesium.Color.WHITE, outlineColor: Cesium.Color.BLACK, outlineWidth: 2, style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            horizontalOrigin: Cesium.HorizontalOrigin.LEFT, pixelOffset: new Cesium.Cartesian2(4, 0), disableDepthTestDistance: FAR, scale: 0.85,
            distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 3000) });
        } else if (k === "BC" || k === "EC" || k === "MC") {
          const [x, y] = f.geometry.coordinates;
          this.keyPoints.add({ position: this.c(x, y, zAt(x, y) + 0.6), pixelSize: 6, color: Cesium.Color.fromCssColorString("#a3e635"),
            outlineColor: Cesium.Color.BLACK, outlineWidth: 1, disableDepthTestDistance: FAR });
          if (k !== "MC") {
            this.keyPointLabels.add({ position: this.c(x, y, zAt(x, y) + 0.6), text: `${k} ${f.properties.chainage_label}`, font: "10px system-ui",
              fillColor: Cesium.Color.fromCssColorString("#d9f99d"), outlineColor: Cesium.Color.BLACK, outlineWidth: 2, style: Cesium.LabelStyle.FILL_AND_OUTLINE,
              pixelOffset: new Cesium.Cartesian2(0, 14), disableDepthTestDistance: FAR, scale: 0.8, distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 1500) });
          }
        }
      }
    }
    this.scene.requestRender();
  }

  // ------------------------------------------------------------------ sections
  setSections(fc: FeatureCollection | null, current: number): void {
    this.sections.removeAll();
    this.sectionPolys = [];
    if (!fc) return;
    fc.features.forEach((f, i) => {
      const coords: number[][] = f.geometry.coordinates;
      const pl = this.sections.add({ positions: coords.map((c) => this.c(c[0], c[1], (c[2] ?? 0) + 0.3)), width: i === current ? 4 : 1.5,
        material: Cesium.Material.fromType("Color", { color: Cesium.Color.fromCssColorString(i === current ? "#facc15" : f.properties.outside ? "#64748b" : "#60a5fa") }),
        id: { type: "section", index: i } as PickId });
      this.sectionPolys.push(pl);
    });
    this.scene.requestRender();
  }

  highlightSection(index: number): void {
    this.sectionPolys.forEach((pl, i) => {
      pl.width = i === index ? 4 : 1.5;
      pl.material = Cesium.Material.fromType("Color", { color: Cesium.Color.fromCssColorString(i === index ? "#facc15" : "#60a5fa") });
    });
    this.scene.requestRender();
  }

  // ------------------------------------------------------------------ markers / comments
  setHoverMarker(p: { x: number; y: number; z: number } | null): void {
    if (this.hoverMarker) { this.markers.remove(this.hoverMarker); this.hoverMarker = null; }
    if (p) {
      this.hoverMarker = this.markers.add({ position: this.c(p.x, p.y, (p.z || 0) + 0.5), pixelSize: 12, color: Cesium.Color.fromCssColorString("#facc15"),
        outlineColor: Cesium.Color.BLACK, outlineWidth: 2, disableDepthTestDistance: FAR });
    }
    this.scene.requestRender();
  }

  setComments(comments: Comment[], zAt: (x: number, y: number) => number): void {
    this.commentPins.removeAll();
    this.commentLabels.removeAll();
    for (const c of comments) {
      if (c.x == null || c.y == null || c.parent_id) continue;
      const pos = this.c(c.x, c.y, zAt(c.x, c.y) + 1);
      this.commentPins.add({ position: pos, pixelSize: 12, color: Cesium.Color.fromCssColorString(c.resolved ? "#64748b" : "#f472b6"),
        outlineColor: Cesium.Color.WHITE, outlineWidth: 2, disableDepthTestDistance: FAR, id: { type: "comment", id: c.id } as PickId });
      this.commentLabels.add({ position: pos, text: `${c.username ?? "?"}: ${c.text.slice(0, 40)}${c.text.length > 40 ? "…" : ""}`, font: "11px system-ui",
        fillColor: Cesium.Color.WHITE, outlineColor: Cesium.Color.BLACK, outlineWidth: 2, style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        pixelOffset: new Cesium.Cartesian2(10, -10), horizontalOrigin: Cesium.HorizontalOrigin.LEFT, showBackground: true,
        backgroundColor: Cesium.Color.fromCssColorString("#1e293b").withAlpha(0.8), disableDepthTestDistance: FAR, scale: 0.9,
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 2000) });
    }
    this.scene.requestRender();
  }

  setVisibility(v: import("../state").LayerVisibility): void {
    this.points.show = v.points;
    this.pointLabels.show = v.points && v.pointLabels;
    const kindShow: Record<string, boolean> = { feature: v.featureLines, boundary: v.boundary, void: v.voids, contour: v.digitisedContours };
    this.lines.show = true;
    for (let i = 0; i < this.lines.length; i++) {
      const pl = this.lines.get(i);
      const id = (pl as any).id as PickId | undefined;
      if (id && id.type === "line") pl.show = kindShow[id.kind] ?? true;
      else if (pl === this.hull) pl.show = v.tinHull;
    }
    if (this.tin) this.tin.show = v.tin;
    if (this.tinWire) this.tinWire.show = v.tin;
    this.issuePins.show = v.tinIssues;
    this.contours.show = v.contours;
    this.contourLabels.show = v.contours && v.contourLabels;
    this.alignmentLines.show = this.alignmentPoints.show = this.alignmentLabels.show = v.alignment;
    this.chainageTicks.show = this.chainageLabels.show = v.alignment && v.chainageLabels;
    this.keyPoints.show = this.keyPointLabels.show = v.alignment && v.keyPoints;
    this.sections.show = v.sections;
    this.commentPins.show = this.commentLabels.show = v.comments;
    this.scene.requestRender();
  }

  static chainageLabel(ch: number): string {
    return fmtChainage(ch);
  }
}
