/** Minimal reactive store + event bus. */
import type { Alignment, ContourSet, Project, SectionSet, TinRun, User } from "./api";

export interface AppState {
  user: User | null;
  authEnabled: boolean;
  project: Project | null;
  tinRuns: TinRun[];
  currentRun: number | null;
  contourSets: ContourSet[];
  visibleContourSets: number[];
  alignments: Alignment[];
  currentAlignment: number | null;
  sectionSets: SectionSet[];
  currentSectionSet: number | null;
  currentSectionIndex: number;
  tool: "none" | "alignment" | "line" | "comment" | "spot" | "profile";
  busy: string | null;
  layers: LayerVisibility;
  tinStyle: { mode: "ramp" | "flat" | "wire"; opacity: number };
}

export interface LayerVisibility {
  points: boolean; pointLabels: boolean;
  featureLines: boolean; boundary: boolean; voids: boolean; digitisedContours: boolean;
  tin: boolean; tinHull: boolean; tinIssues: boolean; tinRejected: boolean;
  contours: boolean; contourLabels: boolean;
  alignment: boolean; chainageLabels: boolean; keyPoints: boolean;
  sections: boolean;
  comments: boolean;
}

export const DEFAULT_LAYERS: LayerVisibility = {
  points: true, pointLabels: false,
  featureLines: true, boundary: true, voids: true, digitisedContours: true,
  tin: true, tinHull: false, tinIssues: false, tinRejected: false,
  contours: true, contourLabels: true,
  alignment: true, chainageLabels: true, keyPoints: true,
  sections: true,
  comments: true,
};

type Listener<K extends keyof AppState> = (value: AppState[K], prev: AppState[K]) => void;

class Store {
  state: AppState = {
    user: null,
    authEnabled: false,
    project: null,
    tinRuns: [],
    currentRun: null,
    contourSets: [],
    visibleContourSets: [],
    alignments: [],
    currentAlignment: null,
    sectionSets: [],
    currentSectionSet: null,
    currentSectionIndex: 0,
    tool: "none",
    busy: null,
    layers: { ...DEFAULT_LAYERS },
    tinStyle: { mode: "ramp", opacity: 1 },
  };
  private listeners: { [K in keyof AppState]?: Listener<K>[] } = {};
  private events = new Map<string, ((payload?: any) => void)[]>();

  get<K extends keyof AppState>(k: K): AppState[K] {
    return this.state[k];
  }

  set<K extends keyof AppState>(k: K, v: AppState[K]): void {
    const prev = this.state[k];
    this.state[k] = v;
    (this.listeners[k] as Listener<K>[] | undefined)?.forEach((fn) => fn(v, prev));
  }

  on<K extends keyof AppState>(k: K, fn: Listener<K>): () => void {
    ((this.listeners[k] as Listener<K>[] | undefined) ?? (this.listeners[k] = [] as any)).push(fn as any);
    return () => {
      this.listeners[k] = (this.listeners[k] as any[]).filter((f) => f !== fn) as any;
    };
  }

  emit(event: string, payload?: any): void {
    this.events.get(event)?.forEach((fn) => fn(payload));
  }

  subscribe(event: string, fn: (payload?: any) => void): () => void {
    if (!this.events.has(event)) this.events.set(event, []);
    this.events.get(event)!.push(fn);
    return () => this.events.set(event, (this.events.get(event) || []).filter((f) => f !== fn));
  }
}

export const store = new Store();

/** Events: "refresh:points" | "refresh:lines" | "refresh:tin" | "refresh:contours" | "refresh:alignments" |
 *  "refresh:sections" | "refresh:comments" | "hover:chainage" {chainage,x,y,z} | "toast" {text, kind} |
 *  "map:flyTo" | "select:section" index | "alignment:preview" Alignment | "alignment:draft" IPIn[] */
export function toast(text: string, kind: "info" | "error" | "ok" = "info"): void {
  store.emit("toast", { text, kind });
}
