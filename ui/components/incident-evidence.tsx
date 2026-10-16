"use client";

import { Copy, Expand } from "lucide-react";
import { useState } from "react";
import { Button, IconButton, Skeleton, Tag } from "@/components/app/kit";
import { EvidenceGraphView } from "@/components/evidence-graph";
import { ApiError } from "@/lib/api/client";
import { useEvidenceGraph } from "@/lib/api/hooks";
import { haptic } from "@/lib/motion";

function State({ title, children, alert }: { title: string; children: React.ReactNode; alert?: boolean }) {
  return (
    <div role={alert ? "alert" : undefined} className="flex flex-col items-center gap-1.5 rounded-lg border border-dashed border-line px-4 py-10 text-center text-[13px] text-ink-3">
      <p className="text-[13.5px] font-medium text-ink">{title}</p>
      {children}
    </div>
  );
}

/** Evidence graph with a node inspector beside it. */
export function IncidentEvidence({ incidentId }: { incidentId: string }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [expand, setExpand] = useState<string | undefined>();
  const graph = useEvidenceGraph(incidentId, expand);
  const node = graph.data?.nodes.find((n) => n.id === selected);
  const edges = graph.data?.edges.filter((e) => e.source === selected || e.target === selected) ?? [];

  if (graph.isLoading) return <Skeleton className="h-[420px] w-full" />;
  if (graph.error instanceof ApiError && graph.error.status === 404) {
    return (
      <State title="No evidence yet">
        <p>The graph appears once this incident has been linked to code and deployments.</p>
      </State>
    );
  }
  if (graph.error || !graph.data) {
    return (
      <State title="Could not load the evidence graph" alert>
        <p>{graph.error instanceof Error ? graph.error.message : "Unknown error"}</p>
        <Button className="mt-2" onClick={() => graph.refetch()}>
          Try again
        </Button>
      </State>
    );
  }

  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_260px]">
      <EvidenceGraphView graph={graph.data} selected={selected} onSelect={(id) => (haptic("tap"), setSelected(id))} />
      <div className="flex min-w-0 flex-col gap-2.5 rounded-lg border border-line bg-panel-2 p-3.5 text-[12.5px]">
        {!node ? (
          <p className="leading-[1.55] text-ink-3">
            Select a node to inspect it. The <span className="text-red">red path</span> is the causal chain from the change to the failure.
          </p>
        ) : (
          <div key={node.id} className="flex flex-col gap-2.5 animate-rise">
            <div className="flex items-center justify-between">
              <Tag tone="accent">{node.label}</Tag>
              <IconButton label="Copy citable id" onClick={() => (navigator.clipboard?.writeText(node.id), haptic("confirm"))}>
                <Copy size={13} />
              </IconButton>
            </div>
            <p className="font-mono text-[11.5px] break-all text-ink-2">{node.id}</p>
            <dl className="grid grid-cols-[72px_minmax(0,1fr)] gap-x-2 gap-y-1.5">
              {Object.entries(node.props).map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-ink-3">{k}</dt>
                  <dd className="font-mono text-[11.5px] [overflow-wrap:anywhere] text-ink">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
                </div>
              ))}
            </dl>
            {edges.length > 0 && (
              <ul className="flex flex-col gap-1 border-t border-line pt-2.5 text-[11.5px] text-ink-3 [overflow-wrap:anywhere]">
                {edges.map((e) => (
                  <li key={e.id} className={e.highlight ? "text-red" : undefined}>
                    {e.source === selected ? `${e.rel} → ${e.target}` : `${e.source} → ${e.rel}`}
                  </li>
                ))}
              </ul>
            )}
            <Button className="self-start" onClick={() => setExpand(node.id)} disabled={expand === node.id}>
              <Expand size={13} /> Expand neighbours
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
