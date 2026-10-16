"use client";

import cytoscape, { type Core } from "cytoscape";
import { useTheme } from "next-themes";
import { useEffect, useRef } from "react";
import { DANGER, type Graph, toElements } from "@/lib/evidence-graph";

/** Cytoscape view of the incident's evidence subgraph; the causal path is emphasised. */
export function EvidenceGraphView({
  graph,
  selected,
  onSelect,
}: {
  graph: Graph;
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const { resolvedTheme } = useTheme();
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    if (!host.current) return;
    // Cytoscape cannot read CSS variables, so label colours are plain hex per theme.
    const dark = resolvedTheme !== "light";
    const text = dark ? "#a3a4ab" : "#57544e";
    const edge = dark ? "#54555c" : "#aaa69e";
    const instance = cytoscape({
      container: host.current,
      elements: toElements(graph),
      // Layered and deterministic: the same graph always looks the same, the causal chain reads in one direction.
      layout: { name: "breadthfirst", directed: true, spacingFactor: 1.15, padding: 24 },
      minZoom: 0.3,
      maxZoom: 2.5,
      style: [
        { selector: "node", style: { "background-color": "data(color)", label: "data(label)", color: text, "font-size": 10, "font-family": "Geist, ui-sans-serif, sans-serif", "text-valign": "bottom", "text-margin-y": 4, width: 22, height: 22 } },
        { selector: "node.hl", style: { "border-width": 3, "border-color": DANGER, width: 30, height: 30 } },
        { selector: "edge", style: { width: 1.2, "line-color": edge, "target-arrow-color": edge, "target-arrow-shape": "triangle", "curve-style": "bezier", label: "data(label)", "font-size": 8, color: text, "text-rotation": "autorotate", opacity: 0.8 } },
        { selector: "edge.hl", style: { width: 3, "line-color": DANGER, "target-arrow-color": DANGER, opacity: 1 } },
        { selector: ":selected", style: { "overlay-color": "#7d84f6", "overlay-opacity": 0.25 } },
      ],
    });
    if (instance.zoom() > 1.4) instance.zoom({ level: 1.4, position: { x: instance.width() / 2, y: instance.height() / 2 } }).center();
    instance.on("tap", "node", (e) => onSelectRef.current(e.target.id()));
    instance.on("tap", (e) => e.target === instance && onSelectRef.current(null));
    cy.current = instance;
    return () => {
      instance.destroy();
      cy.current = null;
    };
  }, [graph, resolvedTheme]);

  useEffect(() => {
    const c = cy.current;
    if (!c) return;
    c.nodes().unselect();
    if (selected) c.getElementById(selected).select();
  }, [selected, graph]);

  return (
    <div
      ref={host}
      data-lenis-prevent
      role="img"
      aria-label="Evidence graph"
      className="h-[420px] w-full rounded-lg border border-line bg-panel-2 [background-image:radial-gradient(var(--line)_1px,transparent_1px)] [background-size:18px_18px]"
    />
  );
}
