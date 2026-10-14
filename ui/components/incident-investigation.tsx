"use client";

import { Check, ChevronDown, X } from "lucide-react";
import { useState } from "react";
import { Segmented } from "@/components/analytics-dashboard/ui";
import { ErrorState, LoadingState } from "@/components/states";
import {
  useApproveFix,
  useBaseline,
  useIncidentDetail,
  useMe,
  useProposeFix,
  type RankMethod,
} from "@/lib/api/hooks";
import type { components } from "@/lib/api/schema";

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

function FeatureRow({ f }: { f: Feature }) {
  const [open, setOpen] = useState(false);
  const pct = Math.round(Math.min(1, Math.max(0, f.value)) * 100);
  return (
    <li className="feat">
      <div className="feat-line">
        <span className="feat-name">{f.name}</span>
        <span className="feat-bar" aria-hidden="true" data-negative={f.weight < 0}>
          <i style={{ width: `${pct}%` }} />
        </span>
        <span className="feat-val">{f.value.toFixed(2)}</span>
        <span className="feat-contrib" title="weight × value">
          {f.contribution >= 0 ? "+" : "−"}
          {Math.abs(f.contribution).toFixed(3)}
        </span>
        <button
          type="button"
          className="feat-query press"
          aria-expanded={open}
          aria-label={`Show the query behind ${f.name}`}
          onClick={() => setOpen((o) => !o)}
        >
          <code>{f.query}</code>
          <ChevronDown size={12} aria-hidden style={{ transform: open ? "rotate(180deg)" : undefined }} />
        </button>
      </div>
      {open && <pre className="feat-cypher">{f.cypher}</pre>}
    </li>
  );
}

function Suspect({ c, defaultOpen }: { c: Candidate; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <li className="suspect" data-top={c.rank === 1}>
      <button type="button" className="suspect-head press" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="suspect-rank">#{c.rank}</span>
        <span className="suspect-main">
          <code className="suspect-sha">{c.sha.slice(0, 7)}</code>
          <span className="suspect-msg">{c.message || "(no message)"}</span>
          <span className="suspect-meta">
            {c.author ?? "unknown"}
            {c.deployment ? ` · ${c.deployment}` : ""}
          </span>
        </span>
        <span className="suspect-score">
          <strong>{c.score.toFixed(2)}</strong>
          {c.features.length > 0 && <small>{Math.round(c.confidence * 100)}% conf.</small>}
        </span>
      </button>
      {open && c.features.length > 0 && (
        <div className="suspect-body">
          <ul className="feats" aria-label={`Score breakdown for ${c.sha.slice(0, 7)}`}>
            {c.features.map((f) => (
              <FeatureRow key={f.name} f={f} />
            ))}
          </ul>
          {c.evidence.length > 0 && (
            <p className="suspect-path">
              <span>Path</span>
              {c.evidence.map((e) => (
                <code key={`${e.changed_function}>${e.thrown_function}`}>
                  {e.changed_function.split(":").pop()} → {e.thrown_function.split(":").pop()}
                </code>
              ))}
            </p>
          )}
        </div>
      )}
    </li>
  );
}

type Remediation = components["schemas"]["RemediationView"];

const REMEDIATION_COPY: Record<string, string> = {
  proposing: "Preparing the revert branch…",
  verifying: "Revert PR opened; verifying in staging…",
  verified: "Fix tested in staging: passed",
  failed: "Fix tested in staging: failed",
  needs_manual: "The commit cannot be reverted cleanly; it needs a manual fix",
  approved: "Approved; waiting for the pull request to be merged",
  merged: "Merged: incident resolved",
};

function FixSection({
  incidentId,
  remediation,
  canAct,
  hasSuspect,
}: {
  incidentId: string;
  remediation: Remediation | null;
  canAct: boolean;
  hasSuspect: boolean;
}) {
  const propose = useProposeFix(incidentId);
  const approve = useApproveFix(incidentId);
  const v = remediation?.verification as
    | { passed: boolean; run_url?: string; error_rate_before?: number | null; error_rate_after?: number | null; baseline_error_rate?: number | null }
    | null
    | undefined;
  const pct = (x?: number | null) => (x === null || x === undefined ? "n/a" : `${(x * 100).toFixed(1)}%`);

  return (
    <section className="card card-pad" aria-labelledby="fix-title">
      <div className="card-head">
        <h2 id="fix-title" className="card-title">Fix and verification</h2>
      </div>
      {!remediation && (
        <>
          <p className="muted">No fix has been proposed for this incident yet.</p>
          {canAct && hasSuspect && (
            <button type="button" className="btn press" disabled={propose.isPending} onClick={() => propose.mutate()}>
              {propose.isPending ? "Requesting…" : "Open revert PR for the top suspect"}
            </button>
          )}
          {propose.isSuccess && <p className="muted">Requested. The pull request will appear here shortly.</p>}
          {propose.error && <p role="alert" className="verdict" data-verdict="wrong">{propose.error.message}</p>}
        </>
      )}
      {remediation && (
        <div className="fix">
          <p className="verdict" data-verdict={remediation.status === "failed" || remediation.status === "needs_manual" ? "wrong" : remediation.status === "verified" || remediation.status === "merged" ? "confident" : "ambiguous"}>
            {REMEDIATION_COPY[remediation.status] ?? remediation.status}
          </p>
          <p className="muted">
            Revert of <code>{remediation.sha?.slice(0, 7)}</code> on <code>{remediation.branch}</code>
            {remediation.pr_url && (
              <> · <a href={remediation.pr_url} target="_blank" rel="noreferrer">pull request</a></>
            )}
          </p>
          {v && (
            <dl className="usage">
              <div><dt>Before the fix</dt><dd>{pct(v.error_rate_before)}</dd></div>
              <div><dt>In staging after</dt><dd>{pct(v.error_rate_after)}</dd></div>
              <div><dt>Baseline</dt><dd>{pct(v.baseline_error_rate)}</dd></div>
              {v.run_url && <div><dt>Run</dt><dd><a href={v.run_url} target="_blank" rel="noreferrer">Actions</a></dd></div>}
            </dl>
          )}
          {canAct && remediation.status === "verified" && (
            <button type="button" className="btn press" disabled={approve.isPending} onClick={() => approve.mutate(remediation.id)}>
              {approve.isPending ? "Approving…" : "Approve fix"}
            </button>
          )}
          {remediation.approved_by && <p className="muted">Approved by {remediation.approved_by}. A human merges the pull request.</p>}
          {approve.error && <p role="alert" className="verdict" data-verdict="wrong">{approve.error.message}</p>}
        </div>
      )}
    </section>
  );
}

export function IncidentInvestigation({ incidentId }: { incidentId: string }) {
  const [view, setView] = useState<RankMethod>("graph");
  const detail = useIncidentDetail(incidentId);
  const baseline = useBaseline(incidentId, view === "graph" ? "b1" : view, view !== "graph");
  const me = useMe();

  if (detail.isLoading) return <LoadingState rows={4} />;
  if (detail.error || !detail.data) return <ErrorState error={detail.error} onRetry={() => detail.refetch()} />;

  const graph = detail.data.ranking;
  const shown = view === "graph" ? graph : (baseline.data ?? null);
  const graphTop = graph.candidates[0]?.sha;
  const inv = detail.data.investigation;
  const role = me.data?.repos[detail.data.incident.repo];

  return (
    <>
      <section className="card card-pad" aria-labelledby="sus-title">
        <div className="card-head">
          <h2 id="sus-title" className="card-title">Suspect commits</h2>
          <Segmented label="Ranking method" options={VIEWS} value={view} onChange={setView} />
        </div>

        {view === "graph" ? (
          <p className="verdict" data-verdict={graph.verdict}>
            {VERDICT_COPY[graph.verdict] ?? graph.verdict}
            {graph.verdict === "confident" || graph.verdict === "ambiguous" ? ` · margin ${graph.margin.toFixed(2)}` : ""}
          </p>
        ) : shown ? (
          <p className="verdict" data-verdict={shown.candidates[0]?.sha === graphTop ? "confident" : "wrong"}>
            {shown.candidates[0]?.sha === graphTop ? (
              <><Check size={14} aria-hidden /> The baseline picks the same commit as the graph.</>
            ) : (
              <><X size={14} aria-hidden /> The baseline blames <code>{shown.candidates[0]?.sha.slice(0, 7)}</code>; the graph says <code>{graphTop?.slice(0, 7)}</code>.</>
            )}
          </p>
        ) : null}

        {view !== "graph" && baseline.isLoading && <LoadingState rows={2} />}
        {shown && shown.candidates.length === 0 && <p className="muted">No candidate commits.</p>}
        {shown && shown.candidates.length > 0 && (
          <ol className="suspects">
            {shown.candidates.map((c, i) => (
              <Suspect key={`${view}-${c.sha}`} c={c} defaultOpen={view === "graph" && i === 0} />
            ))}
          </ol>
        )}
      </section>

      <section className="card card-pad" aria-labelledby="inv-title">
        <div className="card-head">
          <h2 id="inv-title" className="card-title">Agent investigation</h2>
          <span className="muted">{inv.steps.length} tool call{inv.steps.length === 1 ? "" : "s"}</span>
        </div>
        {inv.status === "not_run" && (
          <p className="muted">The agent has not run for this incident yet. The ranking above is already computed.</p>
        )}
        {inv.steps.length > 0 && (
          <ol className="trace" aria-label="Tool calls in order">
            {inv.steps.map((s) => (
              <li key={s.n}>
                <span className="trace-n">{s.n}</span>
                <div>
                  <p className="trace-line">
                    <code>{s.name}</code>
                    <span className="trace-ms">{s.latency_ms.toFixed(0)} ms</span>
                  </p>
                  <p className="trace-sum">{s.summary}</p>
                  {s.node_ids.length > 0 && (
                    <p className="chips">
                      {s.node_ids.slice(0, 6).map((n) => (
                        <code key={n} className="chip">{n}</code>
                      ))}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
        {inv.status === "explanation_invalid" && (
          <p className="verdict" data-verdict="wrong">
            The agent's explanation failed citation validation and was discarded. The ranking above is unaffected.
          </p>
        )}
        {inv.explanation && (
          <div className="explain">
            <h3>Explanation</h3>
            <ul>
              {(inv.explanation.claims as { text: string; cited_node_ids: string[] }[]).map((c, i) => (
                <li key={i}>
                  <p>{c.text}</p>
                  <p className="chips">
                    {c.cited_node_ids.map((n) => (
                      <code key={n} className="chip">{n}</code>
                    ))}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <FixSection
        incidentId={incidentId}
        remediation={detail.data.remediation}
        canAct={role === "approver"}
        hasSuspect={graph.verdict === "confident" || graph.verdict === "ambiguous"}
      />
    </>
  );
}
