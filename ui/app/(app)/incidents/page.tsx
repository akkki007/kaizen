"use client";

import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { IncidentTable } from "@/components/incident-table";
import { useRepo } from "@/components/repo-context";
import { useIncidents } from "@/lib/api/hooks";

export default function IncidentsPage() {
  const { repo } = useRepo();
  const q = useIncidents();
  const items = (q.data?.items ?? []).filter((i) => !repo || i.repo === repo);
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Incidents</h1>
      {q.isLoading && <LoadingState />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && items.length === 0 && (
        <EmptyState title="No incidents" hint="Detected incidents appear here within seconds of onset." />
      )}
      {items.length > 0 && <IncidentTable incidents={items} />}
    </div>
  );
}
