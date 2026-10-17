import { LEGEND, NODE_STYLE, OVERLAY_COLOR, DANGER, type NodeShape } from "@/lib/evidence-graph";

/** SVG stand-ins for the Cytoscape node shapes, in a 12x12 box. */
const POINTS: Partial<Record<NodeShape, string>> = {
  star: "6,0.5 7.6,4.3 11.6,4.5 8.5,7.1 9.5,11.2 6,9 2.5,11.2 3.5,7.1 0.4,4.5 4.4,4.3",
  hexagon: "3,1 9,1 11.5,6 9,11 3,11 0.5,6",
  triangle: "6,1 11.5,11 0.5,11",
  diamond: "6,0.5 11.5,6 6,11.5 0.5,6",
  vee: "0.5,1 6,5 11.5,1 6,11.5",
  pentagon: "6,0.5 11.5,4.6 9.4,11.3 2.6,11.3 0.5,4.6",
  octagon: "3.6,0.5 8.4,0.5 11.5,3.6 11.5,8.4 8.4,11.5 3.6,11.5 0.5,8.4 0.5,3.6",
  tag: "0.5,1.5 8,1.5 11.5,6 8,10.5 0.5,10.5",
};

export function ShapeGlyph({ shape, color, className }: { shape: NodeShape; color: string; className?: string }) {
  const pts = POINTS[shape];
  return (
    <svg viewBox="0 0 12 12" className={className ?? "size-3 shrink-0"} aria-hidden="true">
      {pts ? (
        <polygon points={pts} fill={color} />
      ) : shape === "ellipse" ? (
        <circle cx="6" cy="6" r="5.5" fill={color} />
      ) : shape === "barrel" ? (
        <rect x="0.5" y="1" width="11" height="10" rx="4" ry="2" fill={color} />
      ) : (
        <rect x="0.5" y="1.5" width="11" height="9" rx={shape === "round-rectangle" ? 2.5 : 0} fill={color} />
      )}
    </svg>
  );
}

/** The legend: exactly the colour/shape table the graph is drawn with. */
export function EvidenceLegend({ overlays }: { overlays: { blast: boolean; history: boolean } }) {
  return (
    <ul aria-label="Graph legend" className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11.5px] text-ink-2">
      {LEGEND.map((label) => (
        <li key={label} className="inline-flex items-center gap-1.5">
          <ShapeGlyph shape={NODE_STYLE[label].shape} color={NODE_STYLE[label].color} />
          {label}
        </li>
      ))}
      <li className="inline-flex items-center gap-1.5">
        <i aria-hidden="true" className="h-[3px] w-3.5 rounded-full" style={{ background: DANGER }} /> causal path
      </li>
      {overlays.blast && (
        <li className="inline-flex items-center gap-1.5">
          <i aria-hidden="true" className="h-0 w-3.5 border-t-2 border-dashed" style={{ borderColor: OVERLAY_COLOR.blast }} /> blast radius
        </li>
      )}
      {overlays.history && (
        <li className="inline-flex items-center gap-1.5">
          <i aria-hidden="true" className="h-0 w-3.5 border-t-2 border-dashed" style={{ borderColor: OVERLAY_COLOR.history }} /> history
        </li>
      )}
    </ul>
  );
}
