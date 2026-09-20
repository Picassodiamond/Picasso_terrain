/** 2-D plan view in project coordinates (metres, north up) on a canvas: pan, zoom, layers,
 *  scale bar, north arrow and draggable handles (IPs). Design work happens in plan and profile,
 *  not on a globe, so this view is CRS-agnostic and exact. */

export interface PlanLayer { id: string; label: string; visible: boolean; draw: (ctx: CanvasRenderingContext2D, v: PlanView) => void }
export interface PlanHandle { id: string; x: number; y: number; radius?: number; label?: string; color?: string }

export class PlanView {
  canvas: HTMLCanvasElement;
  layers: PlanLayer[] = [];
  handles: PlanHandle[] = [];
  cx = 0;
  cy = 0;
  scale = 1; // pixels per metre
  onMove: ((x: number, y: number) => void) | null = null;
  onLeave: (() => void) | null = null;
  onClick: ((x: number, y: number) => void) | null = null;
  onDrag: ((id: string, x: number, y: number, phase: "start" | "move" | "end") => void) | null = null;
  private ro: ResizeObserver;
  private raf = 0;
  private dragging: { px: number; py: number; cx: number; cy: number } | null = null;
  private dragHandle: string | null = null;
  private moved = false;

  constructor(private host: HTMLElement) {
    this.canvas = document.createElement("canvas");
    this.canvas.className = "plan-canvas";
    host.appendChild(this.canvas);
    this.ro = new ResizeObserver(() => { this.resize(); this.requestRender(); });
    this.ro.observe(host);
    this.resize();
    this.canvas.addEventListener("pointerdown", this.down);
    this.canvas.addEventListener("pointermove", this.move);
    this.canvas.addEventListener("pointerup", this.up);
    this.canvas.addEventListener("pointerleave", () => this.onLeave?.());
    this.canvas.addEventListener("wheel", this.wheel, { passive: false });
  }

  destroy(): void {
    this.ro.disconnect();
    cancelAnimationFrame(this.raf);
    this.canvas.remove();
  }

  // ------------------------------------------------------------------ coordinates
  get width(): number { return this.canvas.clientWidth; }
  get height(): number { return this.canvas.clientHeight; }
  toScreen(x: number, y: number): [number, number] { return [(x - this.cx) * this.scale + this.width / 2, this.height / 2 - (y - this.cy) * this.scale]; }
  toWorld(px: number, py: number): [number, number] { return [(px - this.width / 2) / this.scale + this.cx, this.cy - (py - this.height / 2) / this.scale]; }

  fit(bounds: number[], pad = 0.08): void {
    const [x0, y0, x1, y1] = bounds;
    const w = Math.max(x1 - x0, 1), h = Math.max(y1 - y0, 1);
    this.cx = (x0 + x1) / 2;
    this.cy = (y0 + y1) / 2;
    this.scale = Math.min(this.width / (w * (1 + 2 * pad)), this.height / (h * (1 + 2 * pad)));
    this.requestRender();
  }

  centreOn(x: number, y: number): void { this.cx = x; this.cy = y; this.requestRender(); }

  /** Stroke a polyline given in world coordinates. */
  path(ctx: CanvasRenderingContext2D, coords: ArrayLike<number>[] | number[][], close = false): void {
    if (coords.length < 2) return;
    ctx.beginPath();
    for (let i = 0; i < coords.length; i++) {
      const [sx, sy] = this.toScreen(coords[i][0], coords[i][1]);
      if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
    }
    if (close) ctx.closePath();
  }

  hitHandle(px: number, py: number): PlanHandle | null {
    let best: PlanHandle | null = null, bd = Infinity;
    for (const h of this.handles) {
      const [sx, sy] = this.toScreen(h.x, h.y);
      const d = Math.hypot(sx - px, sy - py);
      if (d <= (h.radius ?? 8) && d < bd) { best = h; bd = d; }
    }
    return best;
  }

  // ------------------------------------------------------------------ rendering
  requestRender(): void {
    if (this.raf) return;
    this.raf = requestAnimationFrame(() => { this.raf = 0; this.render(); });
  }

  private resize(): void {
    const dpr = window.devicePixelRatio || 1;
    const w = this.host.clientWidth, h = this.host.clientHeight;
    if (!w || !h) return;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.canvas.style.width = `${w}px`;
    this.canvas.style.height = `${h}px`;
  }

  private render(): void {
    const ctx = this.canvas.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, this.width, this.height);
    ctx.fillStyle = "#0b1220";
    ctx.fillRect(0, 0, this.width, this.height);
    this.drawGrid(ctx);
    for (const l of this.layers) if (l.visible) { ctx.save(); l.draw(ctx, this); ctx.restore(); }
    for (const h of this.handles) {
      const [sx, sy] = this.toScreen(h.x, h.y);
      ctx.fillStyle = h.color || "#fb7185";
      ctx.strokeStyle = "#0b1220"; ctx.lineWidth = 1.5;
      ctx.fillRect(sx - 5, sy - 5, 10, 10); ctx.strokeRect(sx - 5, sy - 5, 10, 10);
      if (h.label) { ctx.fillStyle = "#fecdd3"; ctx.font = "11px system-ui"; ctx.fillText(h.label, sx + 7, sy - 7); }
    }
    this.drawScaleBar(ctx);
    this.drawNorth(ctx);
  }

  private niceStep(targetPx: number): number {
    const raw = targetPx / this.scale;
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    for (const m of [1, 2, 5, 10]) if (m * p >= raw) return m * p;
    return 10 * p;
  }

  private drawGrid(ctx: CanvasRenderingContext2D): void {
    const step = this.niceStep(110);
    const [wx0, wy1] = this.toWorld(0, 0);
    const [wx1, wy0] = this.toWorld(this.width, this.height);
    ctx.strokeStyle = "#162033";
    ctx.lineWidth = 1;
    ctx.fillStyle = "#3b4a63";
    ctx.font = "10px system-ui";
    for (let x = Math.ceil(wx0 / step) * step; x <= wx1; x += step) {
      const [sx] = this.toScreen(x, 0);
      ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, this.height); ctx.stroke();
      ctx.fillText(x.toFixed(0), sx + 3, this.height - 4);
    }
    for (let y = Math.ceil(wy0 / step) * step; y <= wy1; y += step) {
      const [, sy] = this.toScreen(0, y);
      ctx.beginPath(); ctx.moveTo(0, sy); ctx.lineTo(this.width, sy); ctx.stroke();
      ctx.fillText(y.toFixed(0), 4, sy - 3);
    }
  }

  private drawScaleBar(ctx: CanvasRenderingContext2D): void {
    const step = this.niceStep(120);
    const px = step * this.scale;
    const x = this.width - px - 24, y = this.height - 18;
    ctx.strokeStyle = "#e5e7eb"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + px, y); ctx.moveTo(x, y - 5); ctx.lineTo(x, y + 5); ctx.moveTo(x + px, y - 5); ctx.lineTo(x + px, y + 5); ctx.stroke();
    ctx.fillStyle = "#e5e7eb"; ctx.font = "11px system-ui"; ctx.textAlign = "center";
    ctx.fillText(step >= 1000 ? `${step / 1000} km` : `${step} m`, x + px / 2, y - 8);
    ctx.textAlign = "left";
  }

  private drawNorth(ctx: CanvasRenderingContext2D): void {
    const x = this.width - 22, y = 34;
    ctx.fillStyle = "#e5e7eb";
    ctx.beginPath(); ctx.moveTo(x, y - 16); ctx.lineTo(x - 6, y + 4); ctx.lineTo(x, y); ctx.lineTo(x + 6, y + 4); ctx.closePath(); ctx.fill();
    ctx.font = "11px system-ui"; ctx.textAlign = "center"; ctx.fillText("N", x, y + 16); ctx.textAlign = "left";
  }

  // ------------------------------------------------------------------ interaction
  private down = (e: PointerEvent): void => {
    const rect = this.canvas.getBoundingClientRect();
    const h = this.onDrag ? this.hitHandle(e.clientX - rect.left, e.clientY - rect.top) : null;
    if (h) {
      this.dragHandle = h.id;
      this.onDrag?.(h.id, h.x, h.y, "start");
    } else {
      this.dragging = { px: e.clientX, py: e.clientY, cx: this.cx, cy: this.cy };
    }
    this.moved = false;
    this.canvas.setPointerCapture(e.pointerId);
  };

  private move = (e: PointerEvent): void => {
    const rect = this.canvas.getBoundingClientRect();
    const px = e.clientX - rect.left, py = e.clientY - rect.top;
    const [wx, wy] = this.toWorld(px, py);
    if (this.dragHandle) {
      this.moved = true;
      const h = this.handles.find((k) => k.id === this.dragHandle);
      if (h) { h.x = wx; h.y = wy; }
      this.onDrag?.(this.dragHandle, wx, wy, "move");
      this.requestRender();
      return;
    }
    if (this.dragging) {
      const dx = e.clientX - this.dragging.px, dy = e.clientY - this.dragging.py;
      if (Math.abs(dx) + Math.abs(dy) > 2) this.moved = true;
      this.cx = this.dragging.cx - dx / this.scale;
      this.cy = this.dragging.cy + dy / this.scale;
      this.requestRender();
    } else {
      this.canvas.style.cursor = this.onDrag && this.hitHandle(px, py) ? "grab" : "crosshair";
    }
    this.onMove?.(wx, wy);
  };

  private up = (e: PointerEvent): void => {
    const rect = this.canvas.getBoundingClientRect();
    const [wx, wy] = this.toWorld(e.clientX - rect.left, e.clientY - rect.top);
    if (this.dragHandle) {
      const id = this.dragHandle;
      this.dragHandle = null;
      this.onDrag?.(id, wx, wy, "end");
      return;
    }
    if (this.dragging && !this.moved) this.onClick?.(wx, wy);
    this.dragging = null;
  };

  private wheel = (e: WheelEvent): void => {
    e.preventDefault();
    const rect = this.canvas.getBoundingClientRect();
    const px = e.clientX - rect.left, py = e.clientY - rect.top;
    const [wx, wy] = this.toWorld(px, py);
    const f = Math.exp(-e.deltaY * 0.0015);
    this.scale = Math.min(Math.max(this.scale * f, 1e-4), 1e4);
    const [nx, ny] = this.toWorld(px, py);
    this.cx += wx - nx;
    this.cy += wy - ny;
    this.requestRender();
  };
}
