"use client";

import { ArrowUpRight, Check, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Button, Tag } from "@/components/app/kit";
import { ConfirmDialog } from "@/components/incident-dialog";
import { DiffViewer } from "@/components/incident-diff";
import { useProposeFix } from "@/lib/api/hooks";
import { useApproveRemediation, useRejectRemediation } from "@/lib/api/hooks-incident";
import type { components } from "@/lib/api/schema";
import { haptic } from "@/lib/motion";
import { cn } from "@/lib/utils";

type Remediation = components["schemas"]["RemediationView"];
type M = { error_rate?: number | null; p95_ms?: number | null; samples?: number | null };
type Verification = {
  passed?: boolean;
  run_url?: string;
  error_rate_before?: number | null;
  error_rate_after?: number | null;
  baseline_error_rate?: number | null;
  p95_ms?: number | null;
  metrics?: { failing?: M; fixed?: M; baseline?: M };
  verdict?: { passed?: boolean; checks?: Record<string, boolean | null>; reasons?: string[] };
};

export const REMEDIATION_COPY: Record<string, string> = {
  proposing: "Preparing the fix branch…",
  verifying: "Fix PR opened; verifying in staging…",
  verified: "Fix tested in staging: passed",
  failed: "Fix tested in staging: failed",
  needs_manual: "The commit cannot be reverted cleanly; it needs a manual fix",
  approved: "Approved; waiting for the pull request to be merged",
  merged: "Merged: incident resolved",
  rejected: "Fix rejected",
};

const pct = (x?: number | null) => (x === null || x === undefined ? "n/a" : `${(x * 100).toFixed(1)}%`);
const ms = (x?: number | null) => (x === null || x === undefined ? "n/a" : `${Math.round(x)} ms`);

/** Failing vs fixed vs baseline as horizontal bars on one scale. */
function Bars({ title, rows, fmt }: { title: string; rows: { label: string; value: number | null | undefined; tone: string }[]; fmt: (x?: number | null) => string }) {
  const max = Math.max(1e-9, ...rows.map((r) => r.value ?? 0));
  return (
    <figure className="flex flex-col gap-1.5">
      <figcaption className="text-[12px] text-ink-3">{title}</figcaption>
      {rows.map((r) => (
        <div key={r.label} className="grid grid-cols-[92px_minmax(0,1fr)_64px] items-center gap-2 text-[12.5px]">
          <span className="text-ink-2">{r.label}</span>
          <span className="h-2.5 overflow-hidden rounded-full bg-ink/[0.07]" aria-hidden="true">
            <i className={cn("block h-full rounded-full", r.tone)} style={{ width: `${Math.max(2, ((r.value ?? 0) / max) * 100)}%` }} />
          </span>
          <span className="num text-right text-ink">{fmt(r.value)}</span>
        </div>
      ))}
    </figure>
  );
}

function MergeOutcome({ rem, approvedMerge }: { rem: Remediation; approvedMerge: string | null }) {
  const merge = rem.merge as { policy?: string; status?: string; error?: string } | null | undefined;
  const policy = merge?.policy ?? (approvedMerge && approvedMerge !== "manual" ? "app" : approvedMerge ? "manual" : null);
  if (!rem.approved_by && rem.status !== "merged") return null;
  if (rem.simulated) {
    return (
      <p className="text-[13px] text-ink-2" data-testid="merge-outcome">
        Demo mode: the merge is simulated. The incident is resolved and the fix is written to Kaizen&apos;s memory; nothing changed on GitHub.
      </p>
    );
  }
  const status = rem.status === "merged" ? "merged" : (merge?.status ?? approvedMerge);
  return (
    <p className="text-[13px] text-ink-2" data-testid="merge-outcome">
      {policy === "app"
        ? status === "merged"
          ? "Merge policy: Kaizen merges approved fixes. The pull request is merged."
          : status === "failed"
            ? `Merge policy: Kaizen merges approved fixes, but the merge failed${merge?.error ? ` (${merge.error})` : ""}. Merge it on GitHub.`
            : "Merge policy: Kaizen merges approved fixes. Merge requested."
        : "Merge policy: manual. A person merges the pull request on GitHub."}
    </p>
  );
}

export function IncidentRemediation({ incidentId, remediation, canAct, hasSuspect, simulated = false }: { incidentId: string; remediation: Remediation | null; canAct: boolean; hasSuspect: boolean; simulated?: boolean }) {
  const propose = useProposeFix(incidentId);
  const approve = useApproveRemediation(incidentId);
  const reject = useRejectRemediation(incidentId);
  const [dialog, setDialog] = useState<"approve" | "reject" | null>(null);

  if (!remediation) {
    return (
      <div className="flex flex-col items-start gap-3">
        <p className="text-[13px] text-ink-3">No fix has been proposed for this incident yet.</p>
        {simulated && <p className="text-[12.5px] text-ink-3" data-testid="demo-fix-note">Demo incident: Kaizen runs the real fix flow (revert, staging check, your approval) but opens no pull request and merges nothing on GitHub.</p>}
        {canAct && hasSuspect && (
          <Button variant="primary" disabled={propose.isPending} onClick={() => (haptic("confirm"), propose.mutate())}>
            {propose.isPending ? "Requesting…" : simulated ? "Propose the revert (demo)" : "Open revert PR for the top suspect"}
          </Button>
        )}
        {propose.isSuccess && <p className="text-[13px] text-ink-3">Requested. The pull request will appear here shortly.</p>}
        {propose.error && <p role="alert" className="text-[13px] text-red">{propose.error.message}</p>}
      </div>
    );
  }

  const r = remediation;
  const v = r.verification as Verification | null | undefined;
  const m = v?.metrics;
  const bad = ["failed", "needs_manual", "rejected"].includes(r.status);
  const good = ["verified", "approved", "merged"].includes(r.status);
  const canDecide = canAct && r.status === "verified";
  const canReject = canAct && ["verifying", "verified", "failed", "needs_manual"].includes(r.status);

  return (
    <div className="flex flex-col gap-4">
      <p role="status" className={cn("flex items-center gap-1.5 text-[13px]", bad ? "text-red" : good ? "text-green" : "text-amber")}>
        {good ? <Check size={14} aria-hidden /> : bad ? <X size={14} aria-hidden /> : null}
        {REMEDIATION_COPY[r.status] ?? r.status}
      </p>
      {r.simulated && <p className="text-[12.5px] text-amber" data-testid="demo-fix-note">Simulated: no pull request was opened and nothing is merged on GitHub. The revert diff below is the real commit, inverted; the staging result is synthetic.</p>}
      <div className="flex flex-wrap items-center gap-1.5 text-[13px] text-ink-3">
        <Tag tone={r.kind === "revert" ? "neutral" : "accent"}>{r.kind === "revert" ? "Revert" : "Patch"}</Tag>
        {r.labels.map((l) => (
          <Tag key={l} tone="outline">
            {l}
          </Tag>
        ))}
        <span>
          of <code className="font-mono text-ink-2">{r.sha?.slice(0, 7)}</code> on <code className="font-mono text-ink-2">{r.branch}</code>
        </span>
        {r.pr_url && (
          <a href={r.pr_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-link underline underline-offset-2">
            pull request
          </a>
        )}
        {v?.run_url && (
          <a href={v.run_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-link underline underline-offset-2">
            Actions run <ArrowUpRight size={12} />
          </a>
        )}
        {r.check_status && <Tag tone={r.check_status === "success" ? "green" : r.check_status === "failure" ? "red" : "amber"}>check: {r.check_status}</Tag>}
      </div>
      {r.rationale && <p className="text-[13px] leading-[1.55] text-ink-2">{r.rationale}</p>}
      {r.diff && <DiffViewer diff={r.diff} />}

      {v && (
        <div className="grid gap-4 rounded-lg border border-line p-4 sm:grid-cols-2" aria-label="Verification">
          <Bars
            title="Error rate"
            fmt={pct}
            rows={[
              { label: "Before the fix", value: m?.failing?.error_rate ?? v.error_rate_before, tone: "bg-red" },
              { label: "In staging after", value: m?.fixed?.error_rate ?? v.error_rate_after, tone: "bg-green" },
              { label: "Baseline", value: m?.baseline?.error_rate ?? v.baseline_error_rate, tone: "bg-ink-3" },
            ]}
          />
          <Bars
            title="p95 latency"
            fmt={ms}
            rows={[
              { label: "Before the fix", value: m?.failing?.p95_ms, tone: "bg-red" },
              { label: "In staging after", value: m?.fixed?.p95_ms ?? v.p95_ms, tone: "bg-green" },
              { label: "Baseline", value: m?.baseline?.p95_ms, tone: "bg-ink-3" },
            ]}
          />
          {v.verdict && (
            <div className="sm:col-span-2">
              <p className={cn("text-[12.5px] font-medium", v.verdict.passed ? "text-green" : "text-red")}>Verdict: {v.verdict.passed ? "passed" : "failed"}</p>
              {v.verdict.checks && (
                <p className="mt-1 flex flex-wrap gap-1">
                  {Object.entries(v.verdict.checks).map(([k, ok]) => (
                    <Tag key={k} tone={ok === null ? "neutral" : ok ? "green" : "red"}>
                      {k}: {ok === null ? "not measured" : ok ? "ok" : "failed"}
                    </Tag>
                  ))}
                </p>
              )}
              {!!v.verdict.reasons?.length && (
                <ul className="mt-1.5 list-disc pl-5 text-[12.5px] text-ink-2">
                  {v.verdict.reasons.map((x) => (
                    <li key={x}>{x}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}

      {(canDecide || canReject) && (
        <div className="flex flex-wrap gap-2">
          {canDecide && (
            <Button variant="primary" onClick={() => (haptic("tap"), setDialog("approve"))}>
              Approve fix
            </Button>
          )}
          {canReject && <Button onClick={() => (haptic("tap"), setDialog("reject"))}>Reject fix</Button>}
        </div>
      )}
      {!canAct && r.status === "verified" && <p className="text-[12.5px] text-ink-3">Only approvers on this repository can approve or reject the fix.</p>}
      {r.approved_by && (
        <p className="text-[13px] text-ink-2" data-testid="approved-by">
          Approved by <strong className="font-medium text-ink">{r.approved_by}</strong>.
        </p>
      )}
      <MergeOutcome rem={r} approvedMerge={approve.data?.merge ?? null} />
      {r.status === "rejected" && (
        <p className="text-[13px] text-ink-2" data-testid="rejected-by">
          Rejected by <strong className="font-medium text-ink">{r.rejected_by}</strong>: {r.reject_reason}
        </p>
      )}

      {r.prior_fixes.length > 0 && (
        <div>
          <h3 className="mb-1.5 text-[12.5px] font-semibold">Prior fixes for similar incidents</h3>
          <ul className="flex flex-col gap-1 text-[12.5px] text-ink-2">
            {(r.prior_fixes as { incident_id: string; similarity?: number; fix_kind?: string; fix_pr_url?: string; fix_sha?: string }[]).map((p) => (
              <li key={p.incident_id}>
                <Link href={`/incidents/${encodeURIComponent(p.incident_id)}`} className="font-mono text-link hover:underline">
                  {p.incident_id}
                </Link>{" "}
                · {p.fix_kind ?? "fix"} of <code className="font-mono">{p.fix_sha?.slice(0, 7)}</code>
                {p.similarity !== undefined && <> · similarity {p.similarity.toFixed(2)}</>}
                {p.fix_pr_url && (
                  <>
                    {" · "}
                    <a href={p.fix_pr_url} target="_blank" rel="noreferrer" className="text-link underline underline-offset-2">
                      PR
                    </a>
                  </>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <ConfirmDialog
        open={dialog === "approve"}
        title="Approve this fix?"
        confirmLabel="Approve fix"
        busy={approve.isPending}
        error={approve.error?.message}
        onClose={() => setDialog(null)}
        onConfirm={() =>
          approve.mutate(r.id, {
            onSuccess: () => {
              haptic("confirm");
              setDialog(null);
            },
          })
        }
      >
        The {r.kind} of <code className="font-mono">{r.sha?.slice(0, 7)}</code> passed verification in staging. Approving records you as the approver; depending on the merge policy Kaizen then merges the pull request or a person merges it on GitHub.
      </ConfirmDialog>
      <ConfirmDialog
        open={dialog === "reject"}
        title="Reject this fix?"
        confirmLabel="Reject fix"
        danger
        reason="Why is this fix wrong?"
        reasonRequired
        busy={reject.isPending}
        error={reject.error?.message}
        onClose={() => setDialog(null)}
        onConfirm={(reason) =>
          reject.mutate(
            { remediationId: r.id, reason },
            {
              onSuccess: () => {
                haptic("confirm");
                setDialog(null);
              },
            },
          )
        }
      >
        Kaizen closes the pull request with your reason as a comment, and the incident goes back for a person to handle.
      </ConfirmDialog>
    </div>
  );
}
