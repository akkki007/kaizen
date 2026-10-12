"use client";

import Link from "next/link";
import { use, useEffect, type CSSProperties } from "react";
import { useDashboard } from "@/components/analytics-dashboard/context";
import { ArrowRightIcon } from "@/components/analytics-dashboard/icons";
import { IncidentEvidence } from "@/components/incident-evidence";
import { useIncidents, useLlmCost } from "@/lib/api/hooks";
import { STATUS } from "@/lib/dashboard-data";
import { relativeTime } from "@/lib/time";

export default function IncidentPage({ params }: { params: Promise<{ id: string }> }) {
  const id = decodeURIComponent(use(params).id);
  const incidents = useIncidents();
  const incident = incidents.data?.items.find((i) => i.id === id);
  const status = incident ? (STATUS[incident.status as keyof typeof STATUS] ?? STATUS.open) : null;
  const cost = useLlmCost(id);

  // Keep the side panel on the incident this page is about.
  const { data, openPr } = useDashboard();
  const prNumber = data.pullRequests.find((pr) => pr.href === `/incidents/${encodeURIComponent(id)}`)?.number;
  useEffect(() => {
    if (prNumber !== undefined) openPr(prNumber);
    // openPr changes identity with every selection; only follow the route.
  }, [prNumber]);

  return (
    <div className="page-content incident-page">
      <header className="page-head enter" style={{ "--i": 1 } as CSSProperties}>
        <div className="page-head-text">
          <Link href="/incidents" className="back-link press">
            <ArrowRightIcon size={13} strokeWidth={2} style={{ transform: "rotate(180deg)" }} />
            Incidents
          </Link>
          <h1 className="page-title">
            {incident ? (
              <>
                Elevated failures in <code>{incident.service}</code>
              </>
            ) : (
              id
            )}
          </h1>
          {incident && (
            <p className="page-sub">
              {incident.repo} · {incident.env} · onset {relativeTime(incident.onset_ts)} · detected {relativeTime(incident.detected_at)}
            </p>
          )}
        </div>
        {status && (
          <p className="status" data-kind={status.kind}>
            {status.label}
          </p>
        )}
      </header>

      <section className="card card-pad enter" style={{ "--i": 2 } as CSSProperties} aria-labelledby="ev-title">
        <div className="card-head">
          <h2 id="ev-title" className="card-title">
            Evidence graph
          </h2>
          <span className="ev-legend">
            <i aria-hidden="true" /> causal path
          </span>
        </div>
        <IncidentEvidence incidentId={id} />
      </section>

      {cost.data && cost.data.calls > 0 && (
        <section className="card card-pad enter" style={{ "--i": 3 } as CSSProperties} aria-labelledby="cost-title">
          <div className="card-head">
            <h2 id="cost-title" className="card-title">
              LLM usage
            </h2>
          </div>
          <dl className="usage">
            <div>
              <dt>Calls</dt>
              <dd>{cost.data.calls}</dd>
            </div>
            <div>
              <dt>Tokens in / out</dt>
              <dd>
                {cost.data.input_tokens.toLocaleString()} / {cost.data.output_tokens.toLocaleString()}
              </dd>
            </div>
            <div>
              <dt>Cost</dt>
              <dd>${cost.data.cost_usd.toFixed(4)}</dd>
            </div>
            <div>
              <dt>Latency</dt>
              <dd>{(cost.data.latency_ms_total / 1000).toFixed(1)} s</dd>
            </div>
          </dl>
        </section>
      )}
    </div>
  );
}
