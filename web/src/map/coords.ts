/** Project coordinates <-> Cesium Cartesian3.
 *
 * local : plain grid metres (legacy behaviour). Coordinates are placed in an East-North-Up frame
 *         anchored at a fixed lon/lat, offset by an origin (data bounds) to keep values small.
 * geo   : project CRS -> WGS84 via proj4 in the browser, then Cartesian3.fromDegrees.
 */
import * as Cesium from "cesium";
import proj4 from "proj4";
import type { CrsInfo } from "../api";

const ANCHOR_LON = 84.0;
const ANCHOR_LAT = 28.0;

proj4.defs("EPSG:32644", "+proj=utm +zone=44 +datum=WGS84 +units=m +no_defs");
proj4.defs("EPSG:32645", "+proj=utm +zone=45 +datum=WGS84 +units=m +no_defs");

/** Optional placement of a local grid on the Earth: local point (x, y) sits at (lon, lat). */
export interface Anchor { lon: number; lat: number; x: number; y: number }

export class Frame {
  readonly kind: "local" | "geo";
  /** true when coordinates can be placed on a base map (real CRS or anchored local grid) */
  readonly georeferenced: boolean;
  private enu: Cesium.Matrix4 | null = null;
  private enuInv: Cesium.Matrix4 | null = null;
  private converter: proj4.Converter | null = null;
  originX = 0;
  originY = 0;

  constructor(info: CrsInfo | null | undefined, origin?: [number, number], anchor?: Anchor | null) {
    if (!info || info.is_local) {
      this.kind = "local";
      this.georeferenced = !!anchor;
      const lon = anchor?.lon ?? ANCHOR_LON;
      const lat = anchor?.lat ?? ANCHOR_LAT;
      const a = Cesium.Cartesian3.fromDegrees(lon, lat, 0);
      this.enu = Cesium.Transforms.eastNorthUpToFixedFrame(a);
      this.enuInv = Cesium.Matrix4.inverse(this.enu, new Cesium.Matrix4());
      if (anchor) { this.originX = anchor.x; this.originY = anchor.y; }
      else if (origin) [this.originX, this.originY] = origin;
    } else {
      this.georeferenced = true;
      this.kind = "geo";
      let def: string = info.proj4 || (info.epsg ? `EPSG:${info.epsg}` : info.spec);
      if (info.epsg === 4326) def = "EPSG:4326";
      this.converter = proj4(def, "EPSG:4326");
    }
  }

  setOrigin(x: number, y: number): void {
    this.originX = x;
    this.originY = y;
  }

  toCartesian(x: number, y: number, z = 0): Cesium.Cartesian3 {
    if (this.kind === "local") {
      const local = new Cesium.Cartesian3(x - this.originX, y - this.originY, z);
      return Cesium.Matrix4.multiplyByPoint(this.enu!, local, new Cesium.Cartesian3());
    }
    const [lon, lat] = this.converter!.forward([x, y]);
    return Cesium.Cartesian3.fromDegrees(lon, lat, z);
  }

  toCartesians(coords: ArrayLike<number>[] | number[][]): Cesium.Cartesian3[] {
    const out: Cesium.Cartesian3[] = [];
    for (const c of coords as number[][]) out.push(this.toCartesian(c[0], c[1], c.length > 2 ? c[2] ?? 0 : 0));
    return out;
  }

  fromCartesian(c: Cesium.Cartesian3): { x: number; y: number; z: number } {
    if (this.kind === "local") {
      const l = Cesium.Matrix4.multiplyByPoint(this.enuInv!, c, new Cesium.Cartesian3());
      return { x: l.x + this.originX, y: l.y + this.originY, z: l.z };
    }
    const carto = Cesium.Cartographic.fromCartesian(c);
    const [x, y] = this.converter!.inverse([Cesium.Math.toDegrees(carto.longitude), Cesium.Math.toDegrees(carto.latitude)]);
    return { x, y, z: carto.height };
  }

  toLonLat(x: number, y: number): [number, number] | null {
    if (this.kind === "local") return null;
    const [lon, lat] = this.converter!.forward([x, y]);
    return [lon, lat];
  }
}
