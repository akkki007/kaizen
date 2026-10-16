import type { ElementDefinition } from "cytoscape";
import type { components } from "./api/schema";

export type Graph = components["schemas"]["EvidenceGraph"];

// One hue per node label (stable); the highlighted causal path is drawn in the danger colour.
const HUES = ["#7d84f6", "#5aa9e6", "#4fb7a8", "#d6a03c", "#a48cf0", "#8b8c94"];
const hue = (label: string) =>
  HUES[[...label].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) % HUES.length];
export const DANGER = "#f0675d";

export function toElements(graph: Graph): ElementDefinition[] {
  const nodes = new Set(graph.nodes.map((n) => n.id));
  return [
    ...graph.nodes.map((n) => ({
      data: { id: n.id, label: n.props.name ? String(n.props.name) : n.id.split(":").slice(1).join(":"), kind: n.label, color: hue(n.label) },
      classes: n.highlight ? "hl" : "",
    })),
    ...graph.edges
      .filter((e) => nodes.has(e.source) && nodes.has(e.target))
      .map((e) => ({ data: { id: e.id, source: e.source, target: e.target, label: e.rel }, classes: e.highlight ? "hl" : "" })),
  ];
}

