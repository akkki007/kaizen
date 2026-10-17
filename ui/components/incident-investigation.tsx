"use client";

import { Check, ChevronDown, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Button, Segmented, Skeleton, Tag } from "@/components/app/kit";
import { ErrorState } from "@/components/states";
import { Citation } from "@/components/incident-focus";
import { IncidentExplanation, type Explanation } from "@/components/incident-explanation";
import { IncidentRemediation } from "@/components/incident-remediation";
import { useBaseline, useIncidentDetail, useMe, type RankMethod } from "@/lib/api/hooks";
import { useRootCause } from "@/lib/api/hooks-incident";
import type { components } from "@/lib/api/schema";
import { cn } from "@/lib/utils";
import { stepSummary } from "@/lib/workspace";

type Candidate = components["schemas"]["RankedCandidate"];
type Feature = components["schemas"]["FeatureValue"];

const VIEWS = [
  { id: "graph", label: "Graph" },
  { id: "b1", label: "Baseline: keywords" },
  { id: "b0", label: "Baseline: latest commit" },
] as const;

const VERDICT_COPY: Record<string, string> = {
  confident: "Confident",
  ambiguous: "Ambiguous: top suspects are too close to call",
  no_code_evidence: "No code evidence: nothing ties these changes to the failure",
  no_candidates: "No candidate commits found for this incident",
};

const KIND: Record<string, string> = { commit: "commit", config: "config", dependency: "dependency" };

const TONE = { good: "text-green", bad: "text-red", warn: "text-amber", none: "text-ink-2" } as const;

function Verdict({ tone, children }: { tone: keyof typeof TONE; children: ReactNode }) {
  return (
    <p role="status" className={cn("flex items-center gap-1.5 text-[13px]", TONE[tone])}>
      {children}
    </p>
  );
}

/** A detail-page section: hairline header with title and optional controls, then content. */
export function Section({ id, title, aside, children }: { id: string; title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section data-enter aria-labelledby={id} className="border-b border-line">
      <div className="flex min-h-11 flex-wrap items-center gap-2 border-b border-line px-6 py-2">
        <h2 id={id} className="text-[13px] font-semibold">
          {title}
        </h2>
        {aside && <div className="ml-auto">{aside}</div>}
      </div>
      <div className="px-6 py-4">{children}</div>
    </section>
  );
}

function FeatureRow({ f }: { f: Feature }) {
  const [open, setOpen] = useState(false);
  const pct = Math.round(Math.min(1, Math.max(0, f.value)) * 100);
  return (
    <li>
      <div className="grid grid-cols-[84px_minmax(0,1fr)_36px_52px] items-center gap-3 text-[12.5px] sm:grid-cols-[96px_minmax(0,1fr)_40px_56px_auto]">
        <span className="text-ink-2">{f.name}</span>
        <span className="h-1.5 overflow-hidden rounded-full bg-ink/[0.07]" aria-hidden="true">
          <i className={cn("block h-full rounded-full", f.weight < 0 ? "bg-amber" : "bg-accent")} style={{ width: `${pct}%` }} />
        </span>
        <span className="num text-right text-ink-2">{f.value.toFixed(2)}</span>
        <span className="num text-right text-ink-3" title="weight × value">
          {f.contribution >= 0 ? "+" : "−"}
          {Math.abs(f.contribution).toFixed(3)}
        </span>
        <button
          type="button"
          aria-expanded={open}
          aria-label={`Show the query behind ${f.name}`}
          onClick={() => setOpen((o) => !o)}
          className="col-span-4 inline-flex h-5 items-center gap-1 justify-self-start rounded px-1.5 font-mono text-[11px] text-ink-3 transition-colors hover:bg-hover hover:text-ink sm:col-span-1"
        >
          {f.query}
          <ChevronDown size={11} className={cn("transition-transform", open && "rotate-180")} />
        </button>
      </div>
      {(f.explanation || (f.evidence_ids ?? []).length > 0) && (
        <div className="mt-1 flex flex-wrap items-center gap-1 pl-0 text-[12px] text-ink-3 sm:pl-[108px]">
          {f.explanation && <span className="mr-1">{f.explanation}</span>}
          {(f.evidence_ids ?? []).map((id) => (
            <Citation key={id} id={id} />
          ))}
        </div>
      )}
      {open && <pre className="mt-2 overflow-x-auto rounded-md border border-line bg-panel-2 px-3 py-2.5 font-mono text-[11.5px] leading-[1.6] text-ink-2">{f.cypher}</pre>}
    </li>
  );
}

function Suspect({ c, defaultOpen, emphasis, footer }: { c: Candidate; defaultOpen: boolean; emphasis?: boolean; footer?: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  const top = c.rank === 1;
  return (
    <li className={cn("rounded-lg border", emphasis ? "border-amber/50 bg-amber/5" : top ? "border-line bg-panel-2" : "border-transparent")} data-rank={c.rank}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="grid w-full grid-cols-[24px_minmax(0,1fr)_auto] items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-hover"
      >
        <span className="num text-[12px] text-ink-3">#{c.rank}</span>
        <span className="min-w-0">
          <span className="flex min-w-0 items-center gap-2">
            <Tag tone={c.kind === "commit" ? "outline" : "amber"}>{KIND[c.kind ?? "commit"] ?? c.kind}</Tag>
            <code className="font-mono text-[12px] text-link">{c.kind === "commit" || !c.kind ? c.sha.slice(0, 7) : c.sha.slice(0, 32)}</code>
            <span className="truncate text-[13px] text-ink">{c.message || "(no message)"}</span>
          </span>
          <span className="mt-0.5 block text-[12px] text-ink-3">
            {c.author ?? "unknown"}
            {c.deployment ? ` · ${c.deployment}` : ""}
          </span>
        </span>
        <span className="text-right">
          <strong className="num block text-[14px] font-medium">{c.score.toFixed(2)}</strong>
          {c.features.length > 0 && <small className="num text-[11.5px] text-ink-3">{Math.round(c.confidence * 100)}% conf.</small>}
        </span>
      </button>
      {open && footer && c.features.length === 0 && <div className="px-3 pb-3">{footer}</div>}
      {open && c.features.length > 0 && (
        <div className="flex flex-col gap-3 px-3 pb-3 pt-1 animate-rise">
          <ul className="flex flex-col gap-2" aria-label={`Score breakdown for ${c.sha.slice(0, 7)}`}>
            {c.features.map((f) => (
              <FeatureRow key={f.name} f={f} />
            ))}
          </ul>
          {c.evidence.length > 0 && (
            <p className="flex flex-wrap items-center gap-1.5 text-[12px] text-ink-3">
              <span>Path</span>
              {c.evidence.map((e) => (
                <span key={`${e.changed_function}>${e.thrown_function}`} className="inline-flex items-center gap-1">
                  <Citation id={`Function:${e.changed_function}`} />
                  {e.changed_function !== e.thrown_function && (
                    <>
                      → <Citation id={`Function:${e.thrown_function}`} />
                    </>
                  )}
                  <Citation id={`Error:${e.error}`} />
                </span>
              ))}
            </p>
          )}
          {footer}
        </div>
      )}
    </li>
  );
}

/** Approver decision on the top suspect: confirm it as the root cause, or reject it with a reason. */
function RootCause({ incidentId, sha, decided }: { incidentId: string; sha: string; decided: { sha: string | null; status: string | null } }) {
  const rc = useRootCause(incidentId);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const status = rc.data ? rc.data.root_cause_status : decided.status;
  const sameSha = rc.data ? rc.data.sha === sha : decided.sha === sha || decided.status === "rejected";
  return (
    <div className="mt-3 flex flex-col gap-2 border-t border-line pt-3" aria-label="Root cause decision">
      {status && sameSha ? (
        <p role="status" className={cn("text-[12.5px]", status === "confirmed" ? "text-green" : "text-amber")}>
          {status === "confirmed" ? `Confirmed as the root cause.` : "Rejected as the root cause."}
        </p>
      ) : (
        <p className="text-[12.5px] text-ink-3">Is this the root cause? Your answer trains the ranking and is remembered for similar incidents.</p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button disabled={rc.isPending} onClick={() => rc.mutate({ sha, confirmed: true })}>
          <Check size={13} /> Confirm root cause
        </Button>
        <Button disabled={rc.isPending} onClick={() => setRejecting((r) => !r)} aria-expanded={rejecting}>
          <X size={13} /> Not the cause
        </Button>
      </div>
      {rejecting && (
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            rc.mutate({ sha, confirmed: false, reason }, { onSuccess: () => setRejecting(false) });
          }}
        >
          <label className="sr-only" htmlFor="rc-reason">
            Reason
          </label>
          <input id="rc-reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why not? (optional)" className="h-7 min-w-0 flex-1 rounded-md border border-line bg-panel-2 px-2 text-[12.5px] outline-none focus:border-accent" />
          <Button type="submit" variant="primary" disabled={rc.isPending}>
            Reject
          </Button>
        </form>
      )}
      {rc.error && <p role="alert" className="text-[12.5px] text-red">{rc.error.message}</p>}
    </div>
  );
}

/** Suspects (with the baselines for comparison), the agent's trace and explanation, and the fix. */
export function IncidentInvestigation({ incidentId, evidence }: { incidentId: string; evidence?: ReactNode }) {
  const [view, setView] = useState<RankMethod>("graph");
  const detail = useIncidentDetail(incidentId);
  const baseline = useBaseline(incidentId, view === "graph" ? "b1" : view, view !== "graph");
  const me = useMe();

  if (detail.isLoading) {
    return (
      <div className="flex flex-col gap-3 px-6 py-6">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    );
  }
  if (detail.error || !detail.data) {
    return (
      <div className="p-6">
        <ErrorState error={detail.error} onRetry={() => detail.refetch()} />
      </div>
    );
  }

  const graph = detail.data.ranking;
  const shown = view === "graph" ? graph : (baseline.data ?? null);
  const graphTop = graph.candidates[0]?.sha;
  const inv = detail.data.investigation;
  const role = me.data?.repos[detail.data.incident.repo];
  const graphTone = graph.verdict === "confident" ? "good" : graph.verdict === "ambiguous" ? "warn" : "none";

  return (
    <>
      <Section id="sus-title" title="Suspect commits" aside={<Segmented label="Ranking method" options={VIEWS} value={view} onChange={setView} />}>
        <div className="mb-3">
          {view === "graph" ? (
            <Verdict tone={graphTone}>
              {VERDICT_COPY[graph.verdict] ?? graph.verdict}
              {graph.verdict === "confident" || graph.verdict === "ambiguous" ? <span className="text-ink-3"> · margin {graph.margin.toFixed(2)}</span> : null}
              {graph.weights_version && <span className="text-ink-4"> · weights {graph.weights_version}</span>}
            </Verdict>
          ) : null}
          {view === "graph" && graph.verdict === "ambiguous" && (
            <p className="mt-2 rounded-md border border-amber/40 bg-amber/10 px-3 py-2 text-[13px] text-amber" data-testid="abstain">
              Kaizen abstains: the top {Math.min(3, graph.candidates.length)} suspects are too close to call. Each is shown below with its evidence; confirm the real cause.
            </p>
          )}
          {view !== "graph" && shown ? (
            shown.candidates[0]?.sha === graphTop ? (
              <Verdict tone="good">
                <Check size={14} aria-hidden /> The baseline picks the same commit as the graph.
              </Verdict>
            ) : (
              <Verdict tone="bad">
                <X size={14} aria-hidden /> The baseline blames <code className="font-mono">{shown.candidates[0]?.sha.slice(0, 7)}</code>; the graph says{" "}
                <code className="font-mono">{graphTop?.slice(0, 7)}</code>.
              </Verdict>
            )
          ) : null}
        </div>
        {view !== "graph" && baseline.isLoading && <Skeleton className="h-14 w-full" />}
        {shown && shown.candidates.length === 0 && <p className="text-[13px] text-ink-3">No candidate commits.</p>}
        {shown && shown.candidates.length > 0 && (
          <ol aria-label="Suspects" className="-mx-3 flex flex-col gap-1">
            {shown.candidates.map((c, i) => (
              <Suspect
                key={`${view}-${c.sha}`}
                c={c}
                defaultOpen={view === "graph" && (i === 0 || (graph.verdict === "ambiguous" && i < 3))}
                emphasis={view === "graph" && graph.verdict === "ambiguous" && i < 3}
                footer={
                  view === "graph" && i === 0 && role === "approver" && (c.kind ?? "commit") === "commit" ? (
                    <RootCause incidentId={incidentId} sha={c.sha} decided={{ sha: detail.data.confirmed_root_cause ?? null, status: detail.data.root_cause_status ?? null }} />
                  ) : undefined
                }
              />
            ))}
          </ol>
        )}
      </Section>

      {evidence}

      <Section id="inv-title" title="Agent investigation" aside={<span className="num text-[12px] text-ink-3">{inv.steps.length} tool call{inv.steps.length === 1 ? "" : "s"}</span>}>
                {inv.steps.length > 0 && (
          <ol className="relative flex flex-col gap-3 before:absolute before:bottom-2 before:left-[10px] before:top-2 before:w-px before:bg-line" aria-label="Tool calls in order">
            {inv.steps.map((s) => (
              <li key={s.n} className="relative grid grid-cols-[22px_minmax(0,1fr)] gap-3">
                <span className="num z-10 grid size-[21px] place-items-center rounded-full border border-line bg-panel text-[11px] text-ink-2">{s.n}</span>
                <div className="min-w-0">
                  <p className="flex items-baseline gap-2 text-[13px]">
                    <code className="font-mono text-[12.5px] text-ink">{s.name}</code>
                    <span className="num text-[11.5px] text-ink-4">{s.latency_ms.toFixed(0)} ms</span>
                  </p>
                  <p className="mt-0.5 text-[12.5px] text-ink-2">{stepSummary(s.name, s.summary)}</p>
                  {s.node_ids.length > 0 && (
                    <p className="mt-1.5 flex flex-wrap gap-1">
                      {s.node_ids.slice(0, 6).map((n) => (
                        <Tag key={n} tone="outline" className="font-mono">
                          {n}
                        </Tag>
                      ))}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
        <div className={inv.steps.length ? "mt-5" : undefined}>
          <IncidentExplanation status={inv.status} explanation={inv.explanation as Explanation | null} errors={inv.validation_errors} />
        </div>
      </Section>

      <Section id="fix-title" title="Fix and verification">
        <IncidentRemediation
          incidentId={incidentId}
          remediation={detail.data.remediation}
          canAct={role === "approver" || !!detail.data.incident.simulated}
          hasSuspect={graph.verdict === "confident" || graph.verdict === "ambiguous"}
          simulated={!!detail.data.incident.simulated}
        />
      </Section>
    </>
  );
}
