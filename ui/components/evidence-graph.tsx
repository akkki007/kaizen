"use client";

import cytoscape, { type Core } from "cytoscape";
import { useTheme } from "next-themes";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { DANGER, FOCUS, OVERLAY_COLOR, describeNode, nextNode, startNode, type Direction, type GraphModel } from "@/lib/evidence-graph";

const KEYS: Record<string, Direction> = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };

/**
 * Cytoscape view of the incident's evidence subgraph. Fixed colour + shape per node type, the
 * causal path in the danger colour, overlays dashed. Keyboard: Tab into the canvas, arrows move
 * between connected nodes, Enter opens the inspector, Escape clears; an aria-live line
 * describes the focused node.
 */
export function EvidenceGraphView({
  model,
  selected,
  nonce,
  onSelect,
}: {
  model: GraphModel;
  selected: string | null;
  nonce: number;
  onSelect: (id: string | null) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const { resolvedTheme } = useTheme();
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const [cursor, setCursor] = useState<string | null>(null);
  const [live, setLive] = useState("");

  useEffect(() => {
    if (!host.current) return;
    // Cytoscape cannot read CSS variables, so label colours are plain hex per theme.
    const dark = resolvedTheme !== "light";
    const text = dark ? "#a3a4ab" : "#57544e";
    const edge = dark ? "#54555c" : "#aaa69e";
    const big = model.graph.nodes.length > 200;
    const instance = cytoscape({
      container: host.current,
      elements: model.elements,
      // Layered and deterministic: the same graph always looks the same, the causal chain reads in one direction.
      layout: { name: "breadthfirst", directed: true, spacingFactor: big ? 0.9 : 1.15, padding: 24, animate: false },
      minZoom: 0.15,
      maxZoom: 2.5,
      textureOnViewport: big,
      hideEdgesOnViewport: big,
      pixelRatio: big ? 1 : "auto",
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(color)",
            shape: "data(shape)" as never,
            label: "data(label)",
            color: text,
            "font-size": 10,
            "min-zoomed-font-size": 7,
            "font-family": "Geist, ui-sans-serif, sans-serif",
            "text-valign": "bottom",
            "text-margin-y": 4,
            width: 22,
            height: 22,
          },
        },
        { selector: "node.hl", style: { "border-width": 3, "border-color": DANGER, width: 30, height: 30 } },
        { selector: "node.ov", style: { "border-width": 2, "border-style": "dashed", opacity: 0.85 } },
        { selector: "node.ov-blast", style: { "border-color": OVERLAY_COLOR.blast } },
        { selector: "node.ov-history", style: { "border-color": OVERLAY_COLOR.history } },
        { selector: "edge", style: { width: 1.2, "line-color": edge, "target-arrow-color": edge, "target-arrow-shape": "triangle", "curve-style": "bezier", label: "data(label)", "font-size": 8, "min-zoomed-font-size": 8, color: text, "text-rotation": "autorotate", opacity: 0.8 } },
        { selector: "edge.hl", style: { width: 3, "line-color": DANGER, "target-arrow-color": DANGER, opacity: 1 } },
        { selector: "edge.ov", style: { "line-style": "dashed" } },
        { selector: "edge.ov-blast", style: { "line-color": OVERLAY_COLOR.blast, "target-arrow-color": OVERLAY_COLOR.blast } },
        { selector: "edge.ov-history", style: { "line-color": OVERLAY_COLOR.history, "target-arrow-color": OVERLAY_COLOR.history } },
        { selector: ".dim", style: { opacity: 0.25 } },
        { selector: "node.focus", style: { "overlay-color": FOCUS, "overlay-opacity": 0.3, "overlay-padding": 8, "border-width": 4, "border-color": FOCUS, "z-index": 10 } },
        { selector: "edge.focus", style: { width: 5, "line-color": FOCUS, "target-arrow-color": FOCUS, opacity: 1, "z-index": 10 } },
        { selector: "node.kbd", style: { "outline-width": 3, "outline-color": FOCUS, "outline-offset": 4 } as never },
      ],
    });
    if (instance.zoom() > 1.4) instance.zoom({ level: 1.4, position: { x: instance.width() / 2, y: instance.height() / 2 } }).center();
    instance.on("tap", "node", (e) => onSelectRef.current(e.target.id()));
    instance.on("tap", "edge", (e) => onSelectRef.current(e.target.id()));
    instance.on("tap", (e) => e.target === instance && onSelectRef.current(null));
    cy.current = instance;
    return () => {
      instance.destroy();
      cy.current = null;
    };
  }, [model, resolvedTheme]);

  // Selection (from a click, a citation or the keyboard): highlight it and bring it into view.
  useEffect(() => {
    const c = cy.current;
    if (!c) return;
    c.elements().removeClass("focus dim");
    if (!selected) return;
    const el = c.getElementById(selected);
    if (el.empty()) return;
    const ends = c.edges().getElementById(selected).connectedNodes(); // empty unless it is an edge
    const around = ends.nonempty() ? ends.union(el) : el.closedNeighborhood();
    c.elements().not(around).addClass("dim");
    el.addClass("focus");
    ends.addClass("focus");
    c.stop();
    c.animate({ center: { eles: el }, zoom: Math.max(c.zoom(), 1) }, { duration: 320, easing: "ease-out-cubic" });
  }, [selected, nonce, model]);

  useEffect(() => {
    const c = cy.current;
    if (!c) return;
    c.nodes().removeClass("kbd");
    if (cursor) c.getElementById(cursor).addClass("kbd");
  }, [cursor, model]);

  function moveTo(id: string) {
    setCursor(id);
    setLive(describeNode(model, id));
    const c = cy.current;
    if (c) c.animate({ center: { eles: c.getElementById(id) } }, { duration: 200 });
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const c = cy.current;
    if (!c) return;
    const dir = KEYS[e.key];
    if (dir) {
      e.preventDefault();
      const from = cursor ?? startNode(model, selected);
      if (!from) return;
      if (!cursor) return moveTo(from);
      const positions: Record<string, { x: number; y: number }> = {};
      for (const n of model.adjacency.get(from) ?? []) positions[n.id] = c.getElementById(n.id).position();
      positions[from] = c.getElementById(from).position();
      const next = nextNode(model.adjacency, positions, from, dir);
      if (next) moveTo(next);
      else setLive(`No connected node ${dir === "up" ? "above" : dir === "down" ? "below" : `to the ${dir}`}.`);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      const id = cursor ?? startNode(model, selected);
      if (id) {
        setCursor(id);
        onSelect(id);
        setLive(`Inspecting ${describeNode(model, id).split(".")[0]}.`);
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      setCursor(null);
      onSelect(null);
      setLive("Selection cleared.");
    }
  }

  return (
    <div className="relative">
      <div
        id="evidence-graph"
        ref={host}
        data-lenis-prevent
        data-selected={selected ?? ""}
        data-node-count={model.graph.nodes.length}
        tabIndex={0}
        role="application"
        aria-roledescription="evidence graph"
        aria-label={`Evidence graph, ${model.graph.nodes.length} nodes. Arrow keys move between connected nodes, Enter inspects, Escape clears.`}
        onKeyDown={onKeyDown}
        onBlur={() => setCursor(null)}
        className="h-[460px] w-full rounded-lg border border-line bg-panel-2 outline-none [background-image:radial-gradient(var(--line)_1px,transparent_1px)] [background-size:18px_18px] focus-visible:ring-2 focus-visible:ring-accent"
      />
      <p aria-live="polite" className="sr-only" data-testid="graph-live">
        {live}
      </p>
    </div>
  );
}
