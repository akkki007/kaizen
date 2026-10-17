import type { IncidentSummary } from "@/lib/api/hooks-dashboard";
import { cn } from "@/lib/utils";

const KIND: Record<string, string> = { commit: "commit", config: "config", dependency: "dependency" };

/** The suspected cause named by the latest investigation, or a muted placeholder. */
export function SuspectLine({ suspect, className }: { suspect: IncidentSummary["suspect"]; className?: string }) {
  if (!suspect) return <span className={cn("text-ink-4", className)}>no suspect yet</span>;
  const id = suspect.kind === "commit" ? suspect.sha.slice(0, 7) : suspect.sha.split("@")[0];
  return (
    <span className={cn("flex min-w-0 items-center gap-1.5", className)} title={`${KIND[suspect.kind] ?? suspect.kind} ${suspect.sha}: ${suspect.message} (${Math.round(suspect.confidence * 100)}% confidence)`}>
      <code className="shrink-0 font-mono text-ink-2">{id}</code>
      <span className="truncate text-ink-3">{suspect.message}</span>
      <span className="num shrink-0 text-ink-4">{Math.round(suspect.confidence * 100)}%</span>
    </span>
  );
}
