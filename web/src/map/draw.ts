/** Mouse interaction: picking, drawing alignment IPs / lines, comment pins, spot heights, IP dragging. */
import * as Cesium from "cesium";
import type { PickId } from "./layers";
import type { MapViewer } from "./viewer";

export interface DrawCallbacks {
  onClick?: (p: { x: number; y: number; z: number }, picked: PickId | null, ev: { shift: boolean }) => void;
  onDoubleClick?: (p: { x: number; y: number; z: number } | null) => void;
  onMove?: (p: { x: number; y: number; z: number } | null, picked: PickId | null) => void;
  onDragIp?: (index: number, p: { x: number; y: number; z: number }, phase: "start" | "move" | "end") => void;
  onRightClick?: () => void;
}

export class Interaction {
  private handler: Cesium.ScreenSpaceEventHandler;
  private mv: MapViewer;
  callbacks: DrawCallbacks = {};
  dragEnabled = false;
  private dragging: number | null = null;
  private lastClick = 0;

  constructor(mv: MapViewer) {
    this.mv = mv;
    this.handler = new Cesium.ScreenSpaceEventHandler(mv.scene.canvas);
    this.handler.setInputAction((e: Cesium.ScreenSpaceEventHandler.PositionedEvent) => {
      const now = performance.now();
      if (now - this.lastClick < 280) return; // part of a double click
      this.lastClick = now;
      const picked = this.pick(e.position);
      const p = this.mv.pickProject(e.position);
      if (p) this.callbacks.onClick?.(p, picked, { shift: false });
    }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
    this.handler.setInputAction((e: Cesium.ScreenSpaceEventHandler.PositionedEvent) => {
      this.callbacks.onDoubleClick?.(this.mv.pickProject(e.position));
    }, Cesium.ScreenSpaceEventType.LEFT_DOUBLE_CLICK);
    this.handler.setInputAction(() => this.callbacks.onRightClick?.(), Cesium.ScreenSpaceEventType.RIGHT_CLICK);
    this.handler.setInputAction((e: Cesium.ScreenSpaceEventHandler.MotionEvent) => {
      if (this.dragging !== null) {
        const p = this.mv.pickProject(e.endPosition);
        if (p) this.callbacks.onDragIp?.(this.dragging, p, "move");
        return;
      }
      const picked = this.pick(e.endPosition);
      this.mv.scene.canvas.style.cursor = picked && (picked.type === "ip" || picked.type === "comment" || picked.type === "point") ? "pointer" : "";
      this.callbacks.onMove?.(this.mv.pickProject(e.endPosition), picked);
    }, Cesium.ScreenSpaceEventType.MOUSE_MOVE);
    this.handler.setInputAction((e: Cesium.ScreenSpaceEventHandler.PositionedEvent) => {
      if (!this.dragEnabled) return;
      const picked = this.pick(e.position);
      if (picked?.type === "ip") {
        this.dragging = picked.index;
        this.mv.scene.screenSpaceCameraController.enableInputs = false;
        const p = this.mv.pickProject(e.position);
        if (p) this.callbacks.onDragIp?.(picked.index, p, "start");
      }
    }, Cesium.ScreenSpaceEventType.LEFT_DOWN);
    this.handler.setInputAction((e: Cesium.ScreenSpaceEventHandler.PositionedEvent) => {
      if (this.dragging !== null) {
        const idx = this.dragging;
        this.dragging = null;
        this.mv.scene.screenSpaceCameraController.enableInputs = true;
        const p = this.mv.pickProject(e.position);
        if (p) this.callbacks.onDragIp?.(idx, p, "end");
        this.lastClick = performance.now(); // suppress the click that follows a drag
      }
    }, Cesium.ScreenSpaceEventType.LEFT_UP);
  }

  pick(pos: Cesium.Cartesian2): PickId | null {
    const picked = this.mv.scene.pick(pos);
    if (picked && picked.id && typeof picked.id === "object" && "type" in picked.id) return picked.id as PickId;
    return null;
  }

  destroy(): void {
    this.handler.destroy();
  }
}
