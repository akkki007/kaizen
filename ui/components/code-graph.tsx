"use client";

import cytoscape, { type Core } from "cytoscape";
import { useEffect, useRef } from "react";
import type { components } from "@/lib/api/schema";

type CodeGraph = components["schemas"]["CodeGraph"];

const GREEN = ["#d9f99d", "#34d399"]; // light lime to emerald: healthy code
const RED = ["#fca5a5", "#dc2626"];
const AMBER = ["#fde68a", "#f59e0b"];

/**
 * Obsidian-style map of the repository: services group their functions, calls link them, recent
 * commits point at the functions they changed. Everything is green until something breaks; only
 * the failing function turns red (and pulses) and the suspected commit turns amber.
 */
export function CodeGraphView({ graph, onSelect }: { graph: CodeGraph; onSelect: (id: string | null) => void }) {
  const host = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const shape = useRef("");
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  // (re)build only when the set of nodes/edges changes; state changes are applied in place
  useEffect(() => {
    if (!host.current) return;
    const signature = graph.nodes.map((n) => n.id).join("|") + "#" + graph.edges.length;
    if (cy.current && shape.current === signature) {
      for (const n of graph.nodes) cy.current.getElementById(n.id).data("state", n.state);
      return;
    }
    shape.current = signature;
    cy.current?.destroy();
    const elements = [
      ...graph.nodes.map((n) => ({
        data: { id: n.id, label: n.label, kind: n.kind, parent: n.parent ?? undefined, state: n.state, detail: n.detail },
      })),
      ...graph.edges.map((e, i) => ({ data: { id: `e${i}`, source: e.source, target: e.target, rel: e.rel } })),
    ];
    const grad = (c: string[]) => ({ "background-fill": "linear-gradient", "background-gradient-stop-colors": c.join(" "), "background-gradient-direction": "to-bottom-right" });
    const instance = cytoscape({
      container: host.current,
      elements,
      layout: { name: "cose", animate: false, nodeRepulsion: () => 9000, idealEdgeLength: () => 55, padding: 24, randomize: false, componentSpacing: 60 } as never,
      minZoom: 0.2,
      maxZoom: 3,
      style: [
        { selector: "node[kind='service']", style: { shape: "round-rectangle", "background-color": "#10b981", "background-opacity": 0.07, "border-width": 1, "border-color": "#10b981", "border-opacity": 0.35, "border-style": "dashed", label: "data(label)", "text-valign": "top", "text-halign": "center", color: "#6ee7b7", "font-size": 12, "font-weight": 600 } as never },
        { selector: "node[kind='function']", style: { shape: "ellipse", width: 15, height: 15, ...grad(GREEN), "border-width": 1, "border-color": "#10b981", label: "data(label)", color: "#9ca3af", "font-size": 8, "text-valign": "bottom", "text-margin-y": 3, "min-zoomed-font-size": 9 } as never },
        { selector: "node[kind='commit']", style: { shape: "diamond", width: 13, height: 13, ...grad(GREEN), "border-width": 1, "border-color": "#10b981", label: "data(label)", color: "#9ca3af", "font-size": 8, "text-valign": "bottom", "text-margin-y": 3, "min-zoomed-font-size": 9 } as never },
        { selector: "node[state='failing']", style: { ...grad(RED), "border-color": "#ef4444", "border-width": 3, width: 24, height: 24, color: "#fca5a5", "font-size": 10, "font-weight": 700, "min-zoomed-font-size": 0, "z-index": 20 } as never },
        { selector: "node[state='suspect']", style: { ...grad(AMBER), "border-color": "#f59e0b", "border-width": 3, width: 22, height: 22, color: "#fcd34d", "font-size": 10, "font-weight": 700, "min-zoomed-font-size": 0, "z-index": 19 } as never },
        { selector: "edge", style: { width: 1, "line-color": "#6b7280", "line-opacity": 0.35, "curve-style": "bezier", "target-arrow-shape": "triangle", "target-arrow-color": "#6b7280", "arrow-scale": 0.6 } },
        { selector: "edge[rel='modifies']", style: { "line-style": "dashed", "line-color": "#a3e635", "line-opacity": 0.4, "target-arrow-color": "#a3e635" } },
        { selector: "node:selected", style: { "overlay-color": "#ffffff", "overlay-opacity": 0.12, "overlay-padding": 6 } },
      ],
    });
    instance.on("tap", "node", (e) => onSelectRef.current(e.target.id()));
    instance.on("tap", (e) => e.target === instance && onSelectRef.current(null));
    cy.current = instance;
    // pulse the failing nodes so the eye lands on them
    let alive = true;
    const pulse = () => {
      if (!alive || !cy.current) return;
      const bad = cy.current.nodes("[state='failing']");
      if (bad.length === 0) return void setTimeout(pulse, 800);
      bad.animate({ style: { "border-width": 7 } }, { duration: 650 }).animate({ style: { "border-width": 3 } }, { duration: 650, complete: pulse });
    };
    pulse();
    return () => {
      alive = false;
    };
  }, [graph]);

  useEffect(() => () => cy.current?.destroy(), []);

  return <div ref={host} role="img" aria-label="Code graph: green nodes are healthy, red is failing, amber is the suspected commit" className="size-full min-h-[460px]" data-testid="code-graph" />;
}
