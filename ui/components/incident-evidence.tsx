"use client";

import { Copy, Expand } from "lucide-react";
import { useState } from "react";
import { EvidenceGraphView } from "@/components/evidence-graph";
import { ApiError } from "@/lib/api/client";
import { useEvidenceGraph } from "@/lib/api/hooks";

/** Evidence graph with a node inspector; used on the incident page and in the expanded incident panel. */
export function IncidentEvidence({ incidentId, stacked = false }: { incidentId: string; stacked?: boolean }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [expand, setExpand] = useState<string | undefined>();
  const graph = useEvidenceGraph(incidentId, expand);
  const node = graph.data?.nodes.find((n) => n.id === selected);
  const edges = graph.data?.edges.filter((e) => e.source === selected || e.target === selected) ?? [];

  if (graph.isLoading) return <div className="ev-state shimmer">Loading evidence…</div>;
  if (graph.error instanceof ApiError && graph.error.status === 404) {
    return (
      <div className="ev-state">
        <p className="ev-state-title">No evidence yet</p>
        <p>The graph appears once this incident has been linked to code and deployments.</p>
      </div>
    );
  }
  if (graph.error || !graph.data) {
    return (
      <div className="ev-state" role="alert">
        <p className="ev-state-title">Could not load the evidence graph</p>
        <p>{graph.error instanceof Error ? graph.error.message : "Unknown error"}</p>
        <button type="button" className="btn press" onClick={() => graph.refetch()}>
          Try again
        </button>
      </div>
    );
  }

  return (
    <div className="ev" data-stacked={stacked}>
      <EvidenceGraphView graph={graph.data} selected={selected} onSelect={setSelected} />
      <div className="ev-inspector">
        {!node ? (
          <p className="ev-hint">
            Select a node to inspect it. The <span className="ev-path">red path</span> is the causal chain from the
            change to the failure.
          </p>
        ) : (
          <>
            <div className="ev-node-head">
              <span className="tag">{node.label}</span>
              <button
                type="button"
                className="icon-btn press has-tip tip-below tip-end"
                data-tip="Copy citable id"
                aria-label="Copy citable id"
                onClick={() => navigator.clipboard?.writeText(node.id)}
              >
                <Copy size={14} strokeWidth={1.8} />
              </button>
            </div>
            <p className="ev-node-id">{node.id}</p>
            <dl className="kv ev-props">
              {Object.entries(node.props).map(([k, v]) => (
                <div key={k}>
                  <dt>{k}</dt>
                  <dd>{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
                </div>
              ))}
            </dl>
            {edges.length > 0 && (
              <ul className="ev-edges">
                {edges.map((e) => (
                  <li key={e.id} data-path={e.highlight}>
                    {e.source === selected ? `${e.rel} → ${e.target}` : `${e.source} → ${e.rel}`}
                  </li>
                ))}
              </ul>
            )}
            <button type="button" className="btn press" onClick={() => setExpand(node.id)} disabled={expand === node.id}>
              <Expand size={14} strokeWidth={1.8} /> Expand neighbours
            </button>
          </>
        )}
      </div>
    </div>
  );
}
