"use client";

import Link from "next/link";
import { useState } from "react";
import { Button, Skeleton, Tag } from "@/components/app/kit";
import { ConfirmDialog } from "@/components/incident-dialog";
import { useIncidentAction, useIncidentHistory, useSimilarIncidents, type IncidentAction } from "@/lib/api/hooks-incident";
import { haptic } from "@/lib/motion";
import { when } from "@/lib/workspace";

const TERMINAL = new Set(["resolved", "closed"]);

/** Acknowledge (anyone with access), close / reopen (approvers), with a reason for close/reopen. */
export function IncidentActions({ incidentId, status, acknowledgedBy, role }: { incidentId: string; status: string; acknowledgedBy: string | null; role?: string }) {
  const act = useIncidentAction(incidentId);
  const [dialog, setDialog] = useState<"close" | "reopen" | null>(null);
  const terminal = TERMINAL.has(status);
  const approver = role === "approver";
  const run = (action: IncidentAction, reason?: string) =>
    act.mutate({ action, reason }, { onSuccess: () => (haptic("confirm"), setDialog(null)) });

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        {!terminal && !acknowledgedBy && role && (
          <Button onClick={() => run("acknowledge")} disabled={act.isPending}>
            Acknowledge
          </Button>
        )}
        {approver && !terminal && <Button onClick={() => setDialog("close")}>Close incident</Button>}
        {approver && terminal && <Button onClick={() => setDialog("reopen")}>Reopen</Button>}
      </div>
      {acknowledgedBy && <p className="text-[12.5px] text-ink-2">Acknowledged by {acknowledgedBy}</p>}
      {!approver && <p className="text-[12px] text-ink-3">Closing and reopening need the approver role.</p>}
      {act.error && !dialog && <p role="alert" className="text-[12.5px] text-red">{act.error.message}</p>}
      <ConfirmDialog
        open={dialog !== null}
        title={dialog === "close" ? "Close this incident?" : "Reopen this incident?"}
        confirmLabel={dialog === "close" ? "Close incident" : "Reopen"}
        reason="Reason"
        busy={act.isPending}
        error={act.error?.message}
        onClose={() => setDialog(null)}
        onConfirm={(reason) => dialog && run(dialog, reason)}
      >
        {dialog === "close" ? "Closing without a verified fix is for false alarms and won't-fix cases. It is recorded in the history." : "The incident goes back to open and can be investigated again."}
      </ConfirmDialog>
    </div>
  );
}

/** Every status change, oldest first. */
export function IncidentTimeline({ incidentId }: { incidentId: string }) {
  const h = useIncidentHistory(incidentId);
  if (h.isLoading) return <Skeleton className="h-24 w-full" />;
  if (!h.data?.length) return <p className="text-[12.5px] text-ink-3">No status changes yet.</p>;
  return (
    <ol aria-label="Status history" className="relative flex flex-col gap-2.5 before:absolute before:bottom-1 before:left-[4px] before:top-1 before:w-px before:bg-line">
      {h.data.map((t, i) => (
        <li key={i} className="relative grid grid-cols-[10px_minmax(0,1fr)] gap-2.5 text-[12.5px]">
          <i aria-hidden="true" className="z-10 mt-1.5 size-[9px] rounded-full border border-line bg-panel" />
          <div className="min-w-0">
            <p className="text-ink">
              {t.from_status ? (
                <>
                  {t.from_status} → <strong className="font-medium">{t.to_status}</strong>
                </>
              ) : (
                <strong className="font-medium">{t.to_status}</strong>
              )}
            </p>
            <p className="text-[11.5px] text-ink-3">
              {t.actor} · {when(t.at)}
              {t.reason ? ` · ${t.reason}` : ""}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}

/** Incidents this one resembles, best first, with the fix that resolved each. */
export function SimilarIncidents({ incidentId }: { incidentId: string }) {
  const q = useSimilarIncidents(incidentId);
  if (q.isLoading) return <Skeleton className="h-20 w-full" />;
  if (!q.data?.length) return <p className="text-[12.5px] text-ink-3">No similar past incidents.</p>;
  return (
    <ul aria-label="Similar incidents" className="flex flex-col gap-3">
      {q.data.map((s, i) => (
        <li key={s.incident_id} className={i === 0 && s.fix ? "rounded-lg border border-green/40 bg-green/5 p-3" : "rounded-lg border border-line p-3"}>
          <div className="flex flex-wrap items-center gap-2">
            <Link href={`/incidents/${encodeURIComponent(s.incident_id)}`} className="font-mono text-[12.5px] text-link hover:underline">
              {s.incident_id}
            </Link>
            {s.status && <Tag tone={s.status === "resolved" ? "green" : "neutral"}>{s.status}</Tag>}
            <span className="num ml-auto text-[12px] text-ink-2">{Math.round(s.similarity * 100)}% similar</span>
          </div>
          <span className="mt-1.5 block h-1.5 overflow-hidden rounded-full bg-ink/[0.07]" aria-hidden="true">
            <i className="block h-full rounded-full bg-accent" style={{ width: `${Math.round(s.similarity * 100)}%` }} />
          </span>
          {s.fix && (
            <p className="mt-2 text-[12.5px] text-ink">
              {i === 0 ? <strong className="font-semibold text-green">Fix that worked: </strong> : "Fix: "}
              {s.fix.kind ?? "fix"}
              {s.confirmed_root_cause && (
                <>
                  {" "}
                  of <code className="font-mono">{s.confirmed_root_cause.slice(0, 7)}</code>
                </>
              )}
              {s.fix.pr_url && (
                <>
                  {" · "}
                  <a href={s.fix.pr_url} target="_blank" rel="noreferrer" className="text-link underline underline-offset-2">
                    fix PR
                  </a>
                </>
              )}
            </p>
          )}
          {s.basis.length > 0 && (
            <details className="mt-1.5 text-[11.5px] text-ink-3">
              <summary className="cursor-pointer">Why: {s.basis.length} shared signal{s.basis.length === 1 ? "" : "s"}</summary>
              <ul className="mt-1 flex flex-col gap-0.5 font-mono [overflow-wrap:anywhere]">
                {s.basis.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            </details>
          )}
        </li>
      ))}
    </ul>
  );
}
