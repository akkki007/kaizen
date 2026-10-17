"use client";

import { Copy, Expand, X } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Button, IconButton, Skeleton, Tag } from "@/components/app/kit";
import { EvidenceGraphView } from "@/components/evidence-graph";
import { EvidenceLegend, ShapeGlyph } from "@/components/evidence-legend";
import { useIncidentFocus } from "@/components/incident-focus";
import { ApiError } from "@/lib/api/client";
import { useEvidenceGraph } from "@/lib/api/hooks";
import { useEvidenceOverlay, useNodeDetail, type NodeDetail } from "@/lib/api/hooks-incident";
import { buildModel, mergeGraphs, nodeLabel, styleOf, type GraphModel } from "@/lib/evidence-graph";
import { haptic } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { when } from "@/lib/workspace";

function State({ title, children, alert }: { title: string; children: ReactNode; alert?: boolean }) {
  return (
    <div role={alert ? "alert" : undefined} className="flex flex-col items-center gap-1.5 rounded-lg border border-dashed border-line px-4 py-10 text-center text-[13px] text-ink-3">
      <p className="text-[13.5px] font-medium text-ink">{title}</p>
      {children}
    </div>
  );
}

function Toggle({ pressed, onClick, children }: { pressed: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-[12.5px] font-medium transition-colors",
        pressed ? "border-line bg-raised text-ink" : "border-transparent text-ink-3 hover:text-ink",
      )}
    >
      <i aria-hidden="true" className={cn("size-2 rounded-full border", pressed ? "bg-accent border-accent" : "border-ink-4")} />
      {children}
    </button>
  );
}

const fmt = (v: unknown) => (typeof v === "object" ? JSON.stringify(v) : String(v));
const shortFn = (id: string) => id.split(":").pop() ?? id;

function Props({ props }: { props: Record<string, unknown> }) {
  return (
    <dl className="grid grid-cols-[84px_minmax(0,1fr)] gap-x-2 gap-y-1.5">
      {Object.entries(props).map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-ink-3">{k}</dt>
          <dd className="font-mono text-[11.5px] [overflow-wrap:anywhere] text-ink">{fmt(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

function NodeLink({ id, label }: { id: string; label?: string }) {
  const { select } = useIncidentFocus();
  return (
    <button type="button" onClick={() => select(id)} className="max-w-full truncate text-left font-mono text-[11.5px] text-link hover:underline">
      {label ?? id}
    </button>
  );
}

function Detail({ d }: { d: NodeDetail }) {
  return (
    <>
      {d.commit && (
        <div className="flex flex-col gap-2 border-t border-line pt-2.5">
          <p className="text-[12.5px] text-ink">{d.commit.message}</p>
          <p className="text-[11.5px] text-ink-3">
            {d.commit.author ?? "unknown author"}
            {d.commit.ts ? ` · ${when(d.commit.ts)}` : ""}
          </p>
          {d.commit.changed_functions.length > 0 && (
            <div>
              <h4 className="mb-1 text-[11.5px] font-semibold text-ink-2">Changed functions</h4>
              <ul className="flex flex-col gap-1" aria-label="Changed functions">
                {d.commit.changed_functions.map((f) => (
                  <li key={f.function_id} className="flex flex-wrap items-baseline gap-x-2 text-[11.5px]">
                    <NodeLink id={`Function:${f.function_id}`} label={shortFn(f.function_id)} />
                    <span className="font-mono text-ink-3">
                      {f.path}:{f.start_line ?? "?"}–{f.end_line ?? "?"}
                    </span>
                    <span className="num text-green">+{f.lines_added ?? 0}</span>
                    <span className="num text-red">−{f.lines_removed ?? 0}</span>
                    {f.moved_from && <span className="text-ink-3">moved from {f.moved_from}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {d.commit.changed_files.length > 0 && (
            <p className="text-[11.5px] text-ink-3">
              Files: {d.commit.changed_files.map((f) => f.path ?? f.file_id).join(", ")}
            </p>
          )}
        </div>
      )}
      {d.function && (
        <div className="flex flex-col gap-1.5 border-t border-line pt-2.5 text-[11.5px]">
          <p className="font-mono text-ink-2">
            {d.function.path}:{d.function.start_line ?? "?"}–{d.function.end_line ?? "?"} {d.function.service ? `· ${d.function.service}` : ""}
          </p>
          <p className="text-ink-3">
            Callers:{" "}
            {d.function.callers.length ? d.function.callers.map((c, i) => <span key={c}>{i > 0 && ", "}<NodeLink id={`Function:${c}`} label={shortFn(c)} /></span>) : "none"}
          </p>
          <p className="text-ink-3">
            Callees:{" "}
            {d.function.callees.length ? d.function.callees.map((c, i) => <span key={c}>{i > 0 && ", "}<NodeLink id={`Function:${c}`} label={shortFn(c)} /></span>) : "none"}
          </p>
        </div>
      )}
      {d.error_samples.length > 0 && (
        <div className="border-t border-line pt-2.5">
          <h4 className="mb-1 text-[11.5px] font-semibold text-ink-2">Recent samples</h4>
          <ul className="flex flex-col gap-1.5" aria-label="Error samples">
            {d.error_samples.map((s, i) => (
              <li key={i} className="text-[11.5px]">
                <span className="font-mono text-red">{s.error_type ?? "Error"}</span> <span className="text-ink">{s.message}</span>
                <span className="block text-ink-4">
                  {when(s.ts)} {s.endpoint ? `· ${s.endpoint}` : ""} {s.status_code ? `· ${s.status_code}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {d.neighbours.length > 0 && (
        <div className="border-t border-line pt-2.5">
          <h4 className="mb-1 text-[11.5px] font-semibold text-ink-2">
            Neighbours <span className="num font-normal text-ink-3">{d.neighbours_total}</span>
          </h4>
          <ul className="flex flex-col gap-0.5 text-[11.5px] text-ink-3">
            {d.neighbours.map((n) => (
              <li key={`${n.rel}-${n.direction}-${n.id}`} className="flex min-w-0 items-center gap-1.5">
                <span className="shrink-0">{n.direction === "out" ? `${n.rel} →` : `← ${n.rel}`}</span>
                <NodeLink id={n.id} label={n.name ?? n.id} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

function Inspector({ incidentId, model, selected, onExpand, expanded }: { incidentId: string; model: GraphModel; selected: string | null; onExpand: (id: string) => void; expanded?: string }) {
  const { select } = useIncidentFocus();
  const node = selected ? model.nodes.get(selected) : undefined;
  const edge = selected && !node ? model.edges.get(selected) : undefined;
  const detail = useNodeDetail(incidentId, node ? node.id : null);

  if (!node && !edge) {
    return (
      <p className="leading-[1.55] text-ink-3">
        Select a node, or a citation in the explanation, to inspect it. The <span className="text-red">red path</span> is the causal chain from the change to the failure. Keyboard: focus the graph, then use the arrow keys and Enter.
      </p>
    );
  }
  if (edge) {
    return (
      <div key={edge.id} className="flex flex-col gap-2.5 animate-rise" data-testid="inspector" data-inspected={edge.id}>
        <div className="flex items-center justify-between">
          <Tag tone="accent">Edge · {edge.rel}</Tag>
          <IconButton label="Close inspector" onClick={() => select(null)}>
            <X size={13} />
          </IconButton>
        </div>
        <p className="flex flex-col gap-1 text-[11.5px]">
          <NodeLink id={edge.source} label={`${edge.source}`} />
          <span className="text-ink-3">{edge.rel} →</span>
          <NodeLink id={edge.target} label={`${edge.target}`} />
        </p>
        {Object.keys(edge.props).length > 0 && <Props props={edge.props} />}
      </div>
    );
  }
  const n = node!;
  const s = styleOf(n.label);
  return (
    <div key={n.id} className="flex flex-col gap-2.5 animate-rise" data-testid="inspector" data-inspected={n.id}>
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5">
          <ShapeGlyph shape={s.shape} color={s.color} />
          <Tag tone="accent">{n.label}</Tag>
          {n.highlight && <Tag tone="red">causal path</Tag>}
          {n.overlay && <Tag tone="amber">{n.overlay}</Tag>}
        </span>
        <span className="flex">
          <IconButton label="Copy citable id" onClick={() => (navigator.clipboard?.writeText(n.id), haptic("confirm"))}>
            <Copy size={13} />
          </IconButton>
          <IconButton label="Close inspector" onClick={() => select(null)}>
            <X size={13} />
          </IconButton>
        </span>
      </div>
      <h3 className="text-[13px] font-semibold text-ink">{nodeLabel(n)}</h3>
      <p className="font-mono text-[11px] break-all text-ink-3">{n.id}</p>
      {detail.isLoading ? <Skeleton className="h-24 w-full" /> : <Props props={(detail.data?.props ?? n.props) as Record<string, unknown>} />}
      {detail.data && <Detail d={detail.data} />}
      {detail.error instanceof Error && !(detail.error instanceof ApiError && detail.error.status === 404) ? <p className="text-[11.5px] text-red">{detail.error.message}</p> : null}
      <Button className="self-start" onClick={() => onExpand(n.id)} disabled={expanded === n.id}>
        <Expand size={13} /> Expand neighbours
      </Button>
    </div>
  );
}

/** Evidence graph with a legend, overlay toggles and a node inspector beside it. */
export function IncidentEvidence({ incidentId }: { incidentId: string }) {
  const { selected, nonce, select, setCitable } = useIncidentFocus();
  const [expand, setExpand] = useState<string | undefined>();
  const [blast, setBlast] = useState(false);
  const [history, setHistory] = useState(false);
  const graph = useEvidenceGraph(incidentId, expand);
  const blastQ = useEvidenceOverlay(incidentId, "blast", blast);
  const historyQ = useEvidenceOverlay(incidentId, "history", history);

  const model = useMemo(
    () => (graph.data ? buildModel(mergeGraphs(graph.data, blast ? blastQ.data : undefined, history ? historyQ.data : undefined)) : null),
    [graph.data, blast, blastQ.data, history, historyQ.data],
  );
  // Citations resolve against the base graph (what explanations may cite) plus overlays shown.
  useEffect(() => {
    setCitable(model ? new Set([...model.nodes.keys(), ...model.edges.keys()]) : null);
  }, [model, setCitable]);
  useEffect(() => () => setCitable(null), [setCitable]);

  if (graph.isLoading) return <Skeleton className="h-[460px] w-full" />;
  if (graph.error instanceof ApiError && graph.error.status === 404) {
    return (
      <State title="No evidence yet">
        <p>The graph appears once this incident has been linked to code and deployments.</p>
      </State>
    );
  }
  if (graph.error || !model) {
    return (
      <State title="Could not load the evidence graph" alert>
        <p>{graph.error instanceof Error ? graph.error.message : "Unknown error"}</p>
        <Button className="mt-2" onClick={() => graph.refetch()}>
          Try again
        </Button>
      </State>
    );
  }

  const overlayCount = model.graph.nodes.filter((n) => n.overlay).length;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div role="group" aria-label="Graph overlays" className="inline-flex items-center gap-0.5">
          <Toggle pressed={blast} onClick={() => (haptic("tap"), setBlast((b) => !b))}>
            Blast radius
          </Toggle>
          <Toggle pressed={history} onClick={() => (haptic("tap"), setHistory((h) => !h))}>
            History
          </Toggle>
        </div>
        {(blastQ.isFetching || historyQ.isFetching) && <span className="text-[12px] text-ink-3">Loading overlay…</span>}
        {(blast || history) && !blastQ.isFetching && !historyQ.isFetching && (
          <span className="text-[12px] text-ink-3" role="status">
            {overlayCount} overlay node{overlayCount === 1 ? "" : "s"}
          </span>
        )}
        <span className="num ml-auto text-[12px] text-ink-3">
          {model.graph.nodes.length} nodes · {model.edges.size} edges
        </span>
      </div>
      <EvidenceLegend overlays={{ blast, history }} />
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_300px]">
        <EvidenceGraphView model={model} selected={selected} nonce={nonce} onSelect={(id) => (haptic("tap"), select(id))} />
        <div className="flex max-h-[460px] min-w-0 flex-col gap-2.5 overflow-y-auto rounded-lg border border-line bg-panel-2 p-3.5 text-[12.5px]" data-lenis-prevent aria-label="Node inspector" role="region">
          <Inspector incidentId={incidentId} model={model} selected={selected} onExpand={setExpand} expanded={expand} />
        </div>
      </div>
    </div>
  );
}
