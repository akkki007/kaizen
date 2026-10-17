import type { ElementDefinition } from "cytoscape";
import type { components } from "./api/schema";

export type Graph = components["schemas"]["EvidenceGraph"];
export type GraphNode = components["schemas"]["EvidenceNode"];
export type GraphEdge = components["schemas"]["EvidenceEdge"];
export type Overlay = "blast" | "history";

export const DANGER = "#f0675d";
export const FOCUS = "#7d84f6";
export const OVERLAY_COLOR: Record<Overlay, string> = { blast: "#ef8f45", history: "#a48cf0" };

export type NodeShape =
  | "star"
  | "hexagon"
  | "ellipse"
  | "rectangle"
  | "round-rectangle"
  | "tag"
  | "barrel"
  | "triangle"
  | "diamond"
  | "vee"
  | "pentagon"
  | "octagon";

/** One fixed colour and shape per node type; the legend renders exactly this table. */
export const NODE_STYLE: Record<string, { color: string; shape: NodeShape }> = {
  Incident: { color: "#f0675d", shape: "star" },
  Deployment: { color: "#d6a03c", shape: "hexagon" },
  Commit: { color: "#7d84f6", shape: "ellipse" },
  File: { color: "#8b8c94", shape: "rectangle" },
  Function: { color: "#5aa9e6", shape: "round-rectangle" },
  Endpoint: { color: "#4fb7a8", shape: "tag" },
  Service: { color: "#a48cf0", shape: "barrel" },
  Error: { color: "#ef8f45", shape: "triangle" },
  Fix: { color: "#4ac26b", shape: "diamond" },
  Test: { color: "#9fc46b", shape: "vee" },
  ConfigChange: { color: "#e5689a", shape: "pentagon" },
  DependencyChange: { color: "#c79a6b", shape: "octagon" },
};
export const LEGEND = Object.keys(NODE_STYLE);
const OTHER = { color: "#6b6c73", shape: "ellipse" as NodeShape };
export const styleOf = (label: string) => NODE_STYLE[label] ?? OTHER;

/** Short human label: a name when the node has one, else its key without the type prefix. */
export function nodeLabel(n: Pick<GraphNode, "id" | "props">): string {
  const p = n.props as Record<string, unknown>;
  if (p.name) return String(p.name);
  const key = n.id.split(":").slice(1).join(":");
  if (n.id.startsWith("Commit:")) return key.slice(0, 7);
  if (n.id.startsWith("Function:")) return key.split(":").pop() ?? key;
  return key.length > 28 ? `${key.slice(0, 27)}…` : key;
}

export function toElements(graph: Graph): ElementDefinition[] {
  const nodes = new Set(graph.nodes.map((n) => n.id));
  return [
    ...graph.nodes.map((n) => {
      const s = styleOf(n.label);
      const classes = [n.highlight ? "hl" : "", n.overlay ? `ov ov-${n.overlay}` : ""].filter(Boolean).join(" ");
      return { data: { id: n.id, label: nodeLabel(n), kind: n.label, color: s.color, shape: s.shape }, classes };
    }),
    ...graph.edges
      .filter((e) => nodes.has(e.source) && nodes.has(e.target))
      .map((e) => ({
        data: { id: e.id, source: e.source, target: e.target, label: e.rel },
        classes: [e.highlight ? "hl" : "", e.overlay ? `ov ov-${e.overlay}` : ""].filter(Boolean).join(" "),
      })),
  ];
}

/** Base graph plus overlay graphs: overlay-only nodes/edges keep their `overlay` mark. */
export function mergeGraphs(base: Graph, ...overlays: (Graph | undefined)[]): Graph {
  const nodes = new Map(base.nodes.map((n) => [n.id, n]));
  const edges = new Map(base.edges.map((e) => [e.id, e]));
  for (const g of overlays) {
    if (!g) continue;
    for (const n of g.nodes) if (!nodes.has(n.id)) nodes.set(n.id, n);
    for (const e of g.edges) if (!edges.has(e.id)) edges.set(e.id, e);
  }
  return { incident_id: base.incident_id, nodes: [...nodes.values()], edges: [...edges.values()] };
}

export type Neighbour = { id: string; rel: string; out: boolean; edge: string };

export type GraphModel = {
  graph: Graph;
  elements: ElementDefinition[];
  nodes: Map<string, GraphNode>;
  edges: Map<string, GraphEdge>;
  adjacency: Map<string, Neighbour[]>;
};

/** Everything the view needs in one pass: elements, lookups and the adjacency for keyboard moves. */
export function buildModel(graph: Graph): GraphModel {
  const nodes = new Map(graph.nodes.map((n) => [n.id, n]));
  const edges = new Map<string, GraphEdge>();
  const adjacency = new Map<string, Neighbour[]>();
  for (const id of nodes.keys()) adjacency.set(id, []);
  for (const e of graph.edges) {
    if (!nodes.has(e.source) || !nodes.has(e.target)) continue;
    edges.set(e.id, e);
    adjacency.get(e.source)!.push({ id: e.target, rel: e.rel, out: true, edge: e.id });
    adjacency.get(e.target)!.push({ id: e.source, rel: e.rel, out: false, edge: e.id });
  }
  return { graph, elements: toElements(graph), nodes, edges, adjacency };
}

/** A citation is a node id or an edge id ("<src>|<rel>|<dst>"); null when it is not in the graph. */
export function resolveCitation(model: Pick<GraphModel, "nodes" | "edges">, id: string): { kind: "node" | "edge"; id: string } | null {
  if (model.nodes.has(id)) return { kind: "node", id };
  if (model.edges.has(id)) return { kind: "edge", id };
  return null;
}

export type Direction = "left" | "right" | "up" | "down";
type Point = { x: number; y: number };
const VEC: Record<Direction, Point> = { left: { x: -1, y: 0 }, right: { x: 1, y: 0 }, up: { x: 0, y: -1 }, down: { x: 0, y: 1 } };

/**
 * Keyboard move: the connected node that lies most in `dir` from `current` (on screen).
 * Neighbours behind the direction are ignored; ties go to the nearer one. Null when none.
 */
export function nextNode(adjacency: Map<string, Neighbour[]>, positions: Record<string, Point>, current: string, dir: Direction): string | null {
  const here = positions[current];
  if (!here) return null;
  const v = VEC[dir];
  let best: string | null = null;
  let bestScore = Infinity;
  for (const n of adjacency.get(current) ?? []) {
    const p = positions[n.id];
    if (!p || n.id === current) continue;
    const dx = p.x - here.x;
    const dy = p.y - here.y;
    const along = dx * v.x + dy * v.y;
    if (along <= 0) continue;
    const across = Math.abs(dx * v.y - dy * v.x);
    const score = along + across * 2; // prefer straight ahead over far to the side
    if (score < bestScore || (score === bestScore && best !== null && n.id < best)) {
      best = n.id;
      bestScore = score;
    }
  }
  return best;
}

/** Sentence for the aria-live region when keyboard focus lands on a node. */
export function describeNode(model: GraphModel, id: string): string {
  const n = model.nodes.get(id);
  if (!n) return "";
  const nb = model.adjacency.get(id) ?? [];
  const parts = nb.slice(0, 4).map((x) => `${x.out ? x.rel + " to" : x.rel + " from"} ${nodeLabel(model.nodes.get(x.id)!)}`);
  const more = nb.length > 4 ? `, and ${nb.length - 4} more` : "";
  const flags = [n.highlight ? "on the causal path" : "", n.overlay ? `${n.overlay === "blast" ? "blast radius" : "history"} overlay` : ""].filter(Boolean);
  return `${n.label} ${nodeLabel(n)}${flags.length ? `, ${flags.join(", ")}` : ""}. ${nb.length} connection${nb.length === 1 ? "" : "s"}${parts.length ? ": " + parts.join("; ") : ""}${more}. Enter opens the inspector.`;
}

/** Where keyboard navigation starts: the selected node, else the incident, else the first node. */
export function startNode(model: GraphModel, selected: string | null): string | null {
  if (selected && model.nodes.has(selected)) return selected;
  if (selected && model.edges.has(selected)) return model.edges.get(selected)!.source;
  const inc = model.graph.nodes.find((n) => n.label === "Incident" && n.id === `Incident:${model.graph.incident_id}`);
  return inc?.id ?? model.graph.nodes[0]?.id ?? null;
}
