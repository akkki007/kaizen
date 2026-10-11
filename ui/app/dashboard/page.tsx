"use client";

import { useMemo } from "react";
import { AnalyticsDashboard } from "@/components/block/analytics-dashboard";
import { AuthGate } from "@/components/auth-gate";
import { ErrorState, LoadingState } from "@/components/states";
import { useIncidents } from "@/lib/api/hooks";
import { toDashboardData } from "@/lib/dashboard-data";
import type { components } from "@/lib/api/schema";

function Dashboard({ me }: { me: components["schemas"]["User"] }) {
  const q = useIncidents();
  const data = useMemo(() => toDashboardData(q.data?.items ?? [], me.repos), [q.data, me.repos]);
  if (q.isLoading) return <div className="p-8"><LoadingState /></div>;
  if (q.error) return <div className="p-8"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>;
  return (
    <div className="h-dvh">
      <AnalyticsDashboard
        data={data}
        brand={{ name: "CausalDeploy" }}
        user={{ name: me.login, email: `${me.login}@users.noreply.github.com`, avatar: data.people[0]?.avatar ?? "", role: "Signed in with GitHub" }}
        assistantName="CausalDeploy"
      />
    </div>
  );
}

export default function DashboardPage() {
  return <AuthGate>{(me) => <Dashboard me={me} />}</AuthGate>;
}
