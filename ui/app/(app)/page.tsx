"use client";

import Link from "next/link";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { IncidentTable } from "@/components/incident-table";
import { useRepo } from "@/components/repo-context";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useIncidents } from "@/lib/api/hooks";

export default function DashboardPage() {
  const { repo } = useRepo();
  const q = useIncidents();
  const items = (q.data?.items ?? []).filter((i) => !repo || i.repo === repo);
  const open = items.filter((i) => i.status !== "resolved");
  const services = new Set(open.map((i) => i.service));

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Overview</h1>
      {q.isLoading && <LoadingState />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <Card>
              <CardHeader><CardTitle>Open incidents</CardTitle></CardHeader>
              <CardContent className="text-3xl font-semibold">{open.length}</CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>Affected services</CardTitle></CardHeader>
              <CardContent className="text-3xl font-semibold">{services.size}</CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>Total incidents</CardTitle></CardHeader>
              <CardContent className="text-3xl font-semibold">{items.length}</CardContent>
            </Card>
          </div>
          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="font-medium">Recent incidents</h2>
              <Link className="text-sm text-primary" href="/incidents">View all</Link>
            </div>
            {items.length === 0 ? (
              <EmptyState title="No incidents" hint="Everything is healthy for this repository." />
            ) : (
              <IncidentTable incidents={items.slice(0, 5)} />
            )}
          </section>
        </>
      )}
    </div>
  );
}
