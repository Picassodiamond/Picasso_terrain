/** Cesium viewer setup: base maps, scene mode, camera helpers. */
import * as Cesium from "cesium";
import type { Frame } from "./coords";

export type BaseMap = "osm" | "opentopo" | "esri" | "carto" | "none";

const BASEMAPS: Record<Exclude<BaseMap, "none">, { url: string; credit: string; maxLevel: number; subdomains?: string[] }> = {
  osm: { url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", credit: "© OpenStreetMap contributors", maxLevel: 19, subdomains: ["a", "b", "c"] },
  opentopo: { url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", credit: "© OpenStreetMap contributors, SRTM | © OpenTopoMap (CC-BY-SA)", maxLevel: 17, subdomains: ["a", "b", "c"] },
  esri: { url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", credit: "Esri, Maxar, Earthstar Geographics", maxLevel: 19 },
  carto: { url: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png", credit: "© OpenStreetMap contributors © CARTO", maxLevel: 19, subdomains: ["a", "b", "c", "d"] },
};

export class MapViewer {
  viewer: Cesium.Viewer;
  scene: Cesium.Scene;
  frame: Frame;
  private gridLayer: Cesium.ImageryLayer | null = null;

  constructor(container: HTMLElement, frame: Frame) {
    this.frame = frame;
    Cesium.Ion.defaultAccessToken = "";
    const credits = document.createElement("div");
    credits.className = "map-credits";
    container.parentElement?.appendChild(credits);
    this.viewer = new Cesium.Viewer(container, {
      creditContainer: credits,
      baseLayer: false,
      terrain: undefined,
      animation: false,
      timeline: false,
      geocoder: false,
      homeButton: false,
      sceneModePicker: false,
      baseLayerPicker: false,
      navigationHelpButton: false,
      fullscreenButton: false,
      infoBox: false,
      selectionIndicator: false,
      shouldAnimate: false,
      skyAtmosphere: false,
      contextOptions: { webgl: { preserveDrawingBuffer: true } },
    });
    this.scene = this.viewer.scene;
    this.scene.globe.baseColor = Cesium.Color.fromCssColorString("#1f2937");
    this.scene.globe.depthTestAgainstTerrain = false;
    this.scene.globe.enableLighting = false;
    this.scene.skyBox && (this.scene.skyBox.show = false);
    this.scene.backgroundColor = Cesium.Color.fromCssColorString("#0b1220");
    this.scene.sun && (this.scene.sun.show = false);
    this.scene.moon && (this.scene.moon.show = false);
    (this.scene as any).highDynamicRange = false;
    this.scene.postProcessStages.fxaa.enabled = true;
    this.scene.screenSpaceCameraController.minimumZoomDistance = 2;
    this.scene.screenSpaceCameraController.enableTilt = true;
    if (!frame.georeferenced) this.setBaseMap("none");
    else this.setBaseMap("osm");
  }

  /** Called when base-map tiles fail to load (network, blocked host...). */
  onTileError: ((message: string) => void) | null = null;
  private tileErrorReported = false;

  setBaseMap(kind: BaseMap): void {
    this.viewer.imageryLayers.removeAll();
    this.gridLayer = null;
    this.tileErrorReported = false;
    if (kind === "none" || !this.frame.georeferenced) {
      const grid = new Cesium.GridImageryProvider({
        cells: 8,
        color: Cesium.Color.fromCssColorString("#334155"),
        glowColor: Cesium.Color.TRANSPARENT,
        glowWidth: 0,
        backgroundColor: Cesium.Color.fromCssColorString("#1f2937"),
      });
      this.gridLayer = this.viewer.imageryLayers.addImageryProvider(grid);
      this.gridLayer.alpha = 0.6;
      return;
    }
    const b = BASEMAPS[kind];
    const provider = new Cesium.UrlTemplateImageryProvider({
      url: b.url,
      subdomains: b.subdomains,
      credit: new Cesium.Credit(b.credit),
      maximumLevel: b.maxLevel,
    });
    provider.errorEvent.addEventListener((err: any) => {
      if (this.tileErrorReported) return;
      this.tileErrorReported = true;
      this.onTileError?.(`Base map tiles could not be loaded (${err?.message || "network error"}). Check the internet connection or a firewall/proxy blocking ${new URL(b.url.replace("{s}", "a")).host}.`);
    });
    this.viewer.imageryLayers.addImageryProvider(provider);
  }

  setSceneMode(mode: "2D" | "3D" | "2.5D"): void {
    if (mode === "2D") this.scene.morphTo2D(0.5);
    else if (mode === "2.5D") this.scene.morphToColumbusView(0.5);
    else this.scene.morphTo3D(0.5);
    // re-centre on the data once the morph has finished (the camera keeps its old position otherwise)
    const remove = this.scene.morphComplete.addEventListener(() => {
      remove();
      setTimeout(() => this.centreOnData(), 100);
    });
    setTimeout(() => this.centreOnData(), 1200); // belt and braces: morphComplete is not always raised
  }

  /** Put the data extent in view in the current scene mode (instant). */
  centreOnData(): void {
    const b = this.lastBounds;
    if (!b) return;
    if (this.scene.mode === Cesium.SceneMode.SCENE3D) {
      this.flyToBounds(b, this.lastZRange, 0.8);
      return;
    }
    const z = this.pickHeight;
    const cartos = [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]].map(([x, y]) => Cesium.Cartographic.fromCartesian(this.frame.toCartesian(x, y, z)));
    const rect = Cesium.Rectangle.fromCartographicArray(cartos);
    const pad = Math.max(rect.width, rect.height) * 0.15;
    rect.west -= pad; rect.east += pad; rect.south -= pad; rect.north += pad;
    this.viewer.camera.setView({ destination: rect });
    this.scene.requestRender();
  }

  /** Fly to a bounding box in project coordinates. */
  flyToBounds(bounds: number[] | null | undefined, zRange?: number[] | null, duration = 1.2): void {
    if (!bounds || bounds.length < 4 || bounds.some((v) => v === null || Number.isNaN(v))) return;
    this.lastBounds = bounds;
    this.lastZRange = zRange;
    if (this.scene.mode !== Cesium.SceneMode.SCENE3D) {
      const [minx, miny, maxx, maxy] = bounds;
      const z = zRange && zRange[0] != null ? (zRange[0] + (zRange[1] ?? zRange[0])) / 2 : 0;
      const sphere = Cesium.BoundingSphere.fromPoints([this.frame.toCartesian(minx, miny, z), this.frame.toCartesian(maxx, maxy, z), this.frame.toCartesian(minx, maxy, z), this.frame.toCartesian(maxx, miny, z)]);
      sphere.radius = Math.max(sphere.radius * 1.1, 30);
      this.viewer.camera.flyToBoundingSphere(sphere, { duration, offset: new Cesium.HeadingPitchRange(0, -Cesium.Math.PI_OVER_TWO, sphere.radius * 2.5) });
      return;
    }
    const [minx, miny, maxx, maxy] = bounds;
    const z = zRange && zRange[0] != null ? (zRange[0] + (zRange[1] ?? zRange[0])) / 2 : 0;
    const corners = [
      this.frame.toCartesian(minx, miny, z), this.frame.toCartesian(maxx, miny, z),
      this.frame.toCartesian(maxx, maxy, z), this.frame.toCartesian(minx, maxy, z),
    ];
    const sphere = Cesium.BoundingSphere.fromPoints(corners);
    sphere.radius = Math.max(sphere.radius * 1.15, 30);
    this.viewer.camera.flyToBoundingSphere(sphere, {
      duration,
      offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-55), sphere.radius * 2.2),
    });
  }

  lookDown(): void {
    const c = this.viewer.camera;
    const pos = c.positionWC;
    c.flyTo({ destination: pos, orientation: { heading: 0, pitch: -Cesium.Math.PI_OVER_TWO, roll: 0 }, duration: 0.6 });
  }

  /** Terrain primitive + reference height used for parallax-free picking. */
  private pickTarget: Cesium.Primitive | null = null;
  private pickHeight = 0;
  private pickCentre: [number, number] | null = null;
  private lastBounds: number[] | null = null;
  private lastZRange: number[] | null | undefined = null;

  setPickTarget(prim: Cesium.Primitive | null, height: number, centre: [number, number] | null): void {
    this.pickTarget = prim;
    this.pickHeight = height;
    this.pickCentre = centre;
  }

  /** Project coordinates under the cursor.
   *  1. depth pick on the TIN surface when the cursor is over it,
   *  2. otherwise intersect the view ray with a horizontal plane at the terrain's mean height
   *     (so clicks beside the TIN are still at the right elevation, unlike the ellipsoid at 0 m). */
  pickProject(windowPos: Cesium.Cartesian2): { x: number; y: number; z: number } | null {
    let cart: Cesium.Cartesian3 | undefined;
    if (this.pickTarget && this.scene.pickPositionSupported && this.scene.mode === Cesium.SceneMode.SCENE3D) {
      const picked = this.scene.pick(windowPos);
      if (picked && picked.primitive === this.pickTarget) {
        const p = this.scene.pickPosition(windowPos);
        if (p && Cesium.defined(p) && !Number.isNaN(p.x)) cart = p;
      }
    }
    if (!cart && this.scene.mode === Cesium.SceneMode.SCENE3D) {
      const ray = this.viewer.camera.getPickRay(windowPos);
      if (ray) {
        const cx = this.pickCentre?.[0] ?? this.frame.originX;
        const cy = this.pickCentre?.[1] ?? this.frame.originY;
        const origin = this.frame.toCartesian(cx, cy, this.pickHeight);
        const normal = this.scene.globe.ellipsoid.geodeticSurfaceNormal(origin, new Cesium.Cartesian3());
        const plane = Cesium.Plane.fromPointNormal(origin, normal);
        const hit = Cesium.IntersectionTests.rayPlane(ray, plane);
        if (hit) cart = hit;
      }
    }
    if (!cart) {
      const p = this.viewer.camera.pickEllipsoid(windowPos, this.scene.globe.ellipsoid);
      if (p) {
        // lift the ellipsoid hit to the reference height (2D / Columbus modes)
        const carto = Cesium.Cartographic.fromCartesian(p);
        carto.height = this.pickHeight;
        cart = Cesium.Cartographic.toCartesian(carto);
      }
    }
    if (!cart) return null;
    const out = this.frame.fromCartesian(cart);
    if (this.frame.kind === "local" && this.scene.mode !== Cesium.SceneMode.SCENE3D) out.z = this.pickHeight;
    return out;
  }

  requestRender(): void {
    this.scene.requestRender();
  }

  destroy(): void {
    this.viewer.destroy();
  }
}
