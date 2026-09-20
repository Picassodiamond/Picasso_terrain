/** Front-end module registry.
 *
 * The terrain workspace is the core. Design modules (road, later canal and building) are separate
 * workspaces on top of an immutable TIN run, loaded lazily as their own chunk so switching is instant
 * and the shell knows nothing about a module's internals. The backend mirrors this list in
 * plm/api/modules.py and is the source of truth for availability and stages.
 */
import type { Design, Project } from "../api";

export interface ModuleInstance { destroy(): void }
export interface ModuleContext { root: HTMLElement; project: Project; design: Design }
export interface ModuleManifest {
  id: string;
  label: string;
  icon: string;
  description: string;
  status: "available" | "planned";
  open?: (ctx: ModuleContext) => Promise<ModuleInstance>;
}

export const MODULES: ModuleManifest[] = [
  {
    id: "road", label: "Road design", icon: "🛣", status: "available",
    description: "Horizontal and vertical alignment, templates and corridor, cut / fill, retaining walls, culverts and drainage.",
    open: async (ctx) => (await import("./road/workspace")).openRoadWorkspace(ctx),
  },
  { id: "canal", label: "Canal design", icon: "〰", status: "planned", description: "Canal alignment, bed slope, sections, hydraulics, structures." },
  { id: "building", label: "Building site", icon: "▦", status: "planned", description: "Site grading: platforms, batters, surface-to-surface volumes, site drainage." },
];

export const moduleById = (id: string): ModuleManifest | undefined => MODULES.find((m) => m.id === id);
export const terrainHash = (pid: string): string => `#/p/${pid}`;
export const designHash = (pid: string, d: { module: string; id: number }): string => `#/p/${pid}/${d.module}/${d.id}`;
