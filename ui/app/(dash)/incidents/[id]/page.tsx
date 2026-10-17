"use client";

import { ArrowUpRight, Link2 } from "lucide-react";
import { use, useState } from "react";
import { AskIncident, IncidentFacts } from "@/components/app/incident-brief";
import { STATUS, StatusBadge, Tag, buttonClass } from "@/components/app/kit";
import { useWorkspace } from "@/components/app/shell";
import { Topbar } from "@/components/app/topbar";
import { IncidentEvidence } from "@/components/incident-evidence";
import { IncidentInvestigation, Section } from "@/components/incident-investigation";
import { LogoMark } from "@/components/landing/logo";
import { IncidentActions, IncidentTimeline, SimilarIncidents } from "@/components/incident-actions";
import { IncidentFocusProvider } from "@/components/incident-focus";
import { IncidentMetricsChart } from "@/components/incident-metrics";
import { PipelineTracker } from "@/components/incident-pipeline";
import { useIncidentDetail, useLlmCost, useMe } from "@/lib/api/hooks";
import { useIncidentHistory, useLiveIncident } from "@/lib/api/hooks-incident";
import { pipeline } from "@/lib/pipeline";
import { haptic } from "@/lib/motion";
import { ago } from "@/lib/workspace";

export default function IncidentPage({ params }: { params: Promise<{ id: string }> }) {
  const id = decodeURIComponent(use(params).id);
  return (
    <IncidentFocusProvider>
      <IncidentView id={id} />
    </IncidentFocusProvider>
  );
}

function IncidentView({ id }: { id: string }) {
  const { ws } = useWorkspace();
  const detail = useIncidentDetail(id);
  const history = useIncidentHistory(id);
  const me = useMe();
  // the detail is live (stream/polling); the workspace list is the fallback while it loads
  const incident = detail.data?.incident ?? ws.incidents.find((i) => i.id === id);
  const live = useLiveIncident(id, incident?.status);
  const role = incident ? me.data?.repos[incident.repo] : undefined;
  const stages = detail.data
    ? pipeline({
        status: detail.data.incident.status,
        investigationStatus: detail.data.investigation.status,
        remediationStatus: detail.data.remediation?.status ?? null,
        history: (history.data ?? []).map((t) => t.to_status),
      })
    : null;
  const cost = useLlmCost(id);
  const [copied, setCopied] = useState(false);
  const now = Date.now();

  return (
    <div className="flex flex-1 flex-col">
      <Topbar
        crumbs={[
          { label: <span className="inline-flex items-center gap-1.5"><LogoMark className="size-3.5 text-accent" />Kaizen</span>, href: "/overview" },
          { label: "Incidents", href: "/incidents" },
          { label: <code className="font-mono text-[12.5px]">{id}</code> },
        ]}
        actions={
          <>
            <button
              type="button"
              className={buttonClass("ghost")}
              onClick={() => {
                void navigator.clipboard?.writeText(window.location.href);
                haptic("confirm");
                setCopied(true);
                window.setTimeout(() => setCopied(false), 1400);
              }}
            >
              <Link2 size={13} className={copied ? "text-green" : undefined} /> <span className="max-sm:hidden">{copied ? "Copied" : "Copy link"}</span>
            </button>
            {incident && (
              <a href={`https://github.com/${incident.repo}`} target="_blank" rel="noreferrer" className={buttonClass("secondary")}>
                <span className="max-sm:hidden">Open on</span> GitHub <ArrowUpRight size={13} />
              </a>
            )}
          </>
        }
      />

      <header data-enter className="border-b border-line px-6 py-6">
        <h1 className="text-[22px] font-semibold leading-[1.25] tracking-[-0.02em]">
          {incident ? (
            <>
              Elevated failures in <code className="font-mono text-[20px]">{incident.service}</code>
            </>
          ) : (
            id
          )}
        </h1>
        {incident && (
          <div className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[12.5px] text-ink-3">
            <StatusBadge status={incident.status} />
            {!(incident.status in STATUS) && <Tag tone="neutral">{incident.status.replace("_", " ")}</Tag>}
            <span className="sr-only" data-testid="incident-status">{incident.status}</span>
            <span>
              {incident.repo} · {incident.env} · onset {ago(now - Date.parse(incident.onset_ts))} ago · detected {ago(now - Date.parse(incident.detected_at))} ago
            </span>
          </div>
        )}
      </header>

      {stages && (
        <section aria-labelledby="pipe-title" className="border-b border-line px-6 py-4">
          <h2 id="pipe-title" className="sr-only">
            Pipeline
          </h2>
          <PipelineTracker stages={stages} mode={live.mode} />
        </section>
      )}

      <div className="grid flex-1 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 xl:border-r xl:border-line">
          <Section id="metric-title" title="Around the onset">
            <IncidentMetricsChart incidentId={id} />
          </Section>
          <IncidentInvestigation
            incidentId={id}
            evidence={
              <Section
                id="ev-title"
                title="Evidence graph"
                aside={
                  <span className="inline-flex items-center gap-1.5 text-[12px] text-ink-3">
                    <i aria-hidden="true" className="h-[3px] w-3.5 rounded-full bg-red" /> causal path
                  </span>
                }
              >
                <IncidentEvidence incidentId={id} />
              </Section>
            }
          />

          {cost.data && cost.data.calls > 0 && (
            <Section id="cost-title" title="LLM usage">
              <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                {[
                  ["Calls", String(cost.data.calls)],
                  ["Tokens in / out", `${cost.data.input_tokens.toLocaleString()} / ${cost.data.output_tokens.toLocaleString()}`],
                  ["Cost", `$${cost.data.cost_usd.toFixed(4)}`],
                  ["Latency", `${(cost.data.latency_ms_total / 1000).toFixed(1)} s`],
                ].map(([k, v]) => (
                  <div key={k}>
                    <dt className="text-[12px] text-ink-3">{k}</dt>
                    <dd className="num mt-1 text-[15px] font-medium">{v}</dd>
                  </div>
                ))}
              </dl>
            </Section>
          )}
        </div>

        <aside data-enter className="flex flex-col gap-6 border-t border-line px-6 py-5 xl:sticky xl:top-[46px] xl:h-[calc(100dvh-16px-48px)] xl:overflow-y-auto xl:border-t-0" data-lenis-prevent>
          {incident && (
            <section aria-labelledby="actions-title">
              <h2 id="actions-title" className="mb-3 text-[13px] font-semibold">
                Actions
              </h2>
              <IncidentActions incidentId={id} status={incident.status} acknowledgedBy={detail.data?.acknowledged_by ?? null} role={role} />
            </section>
          )}
          <section aria-labelledby="similar-title">
            <h2 id="similar-title" className="mb-3 text-[13px] font-semibold">
              Similar incidents
            </h2>
            <SimilarIncidents incidentId={id} />
          </section>
          <section aria-labelledby="history-title">
            <h2 id="history-title" className="mb-3 text-[13px] font-semibold">
              Status history
            </h2>
            <IncidentTimeline incidentId={id} />
          </section>
          {incident && (
            <section aria-labelledby="about-title">
              <h2 id="about-title" className="mb-3 text-[13px] font-semibold">
                About
              </h2>
              <IncidentFacts incident={incident} />
            </section>
          )}
          <section aria-labelledby="ask-title" className="xl:mt-auto">
            <h2 id="ask-title" className="mb-2.5 text-[13px] font-semibold">
              Ask Kaizen
            </h2>
            <AskIncident incidentId={id} />
          </section>
        </aside>
      </div>
    </div>
  );
}
