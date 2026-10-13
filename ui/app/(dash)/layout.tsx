"use client";

import { usePathname, useRouter } from "next/navigation";
import { useMemo, type ReactNode } from "react";
import { AuthGate } from "@/components/auth-gate";
import { AnalyticsDashboard, type AnalyticsDashboardPage } from "@/components/block/analytics-dashboard";
import { IncidentEvidence } from "@/components/incident-evidence";
import { ErrorState, LoadingState } from "@/components/states";
import { useIncidents, useSignOut } from "@/lib/api/hooks";
import type { components } from "@/lib/api/schema";
import { toDashboardData } from "@/lib/dashboard-data";

const PATHS: Record<AnalyticsDashboardPage, string> = {
  overview: "/overview",
  repositories: "/repositories",
  reviews: "/incidents",
  settings: "/settings",
};

function pageFor(path: string): AnalyticsDashboardPage {
  if (path.startsWith("/repositories")) return "repositories";
  if (path.startsWith("/incidents")) return "reviews";
  if (path.startsWith("/settings")) return "settings";
  return "overview";
}

/** Detail routes keep their section active in the rail but get their own title. */
function titleFor(path: string): string | undefined {
  const [, section, ...rest] = path.split("/");
  if (section === "incidents" && rest.length) return `Incidents / ${decodeURIComponent(rest[0])}`;
  if (section === "repositories" && rest.length >= 2) return `Repositories / ${rest.join("/")}`;
  return undefined;
}

const incidentIdOf = (href: string) => decodeURIComponent(href.split("/").pop() ?? "");

function Shell({ me, children }: { me: components["schemas"]["User"]; children: ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const q = useIncidents();
  const signOut = useSignOut();
  const data = useMemo(() => toDashboardData(q.data?.items ?? [], me.repos), [q.data, me.repos]);
  if (q.isLoading) return <div className="p-8"><LoadingState /></div>;
  if (q.error) return <div className="p-8"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>;
  return (
    <div className="h-dvh">
      <AnalyticsDashboard
        data={data}
        brand={{ name: "Kaizen" }}
        user={{ name: me.login, email: `${me.login}@users.noreply.github.com`, avatar: data.people[0]?.avatar ?? "", role: "Signed in with GitHub" }}
        assistantName="Kaizen"
        page={pageFor(path)}
        onPageChange={(page) => router.push(PATHS[page])}
        pageKey={path}
        title={titleFor(path)}
        onSignOut={() => signOut.mutate()}
        signingOut={signOut.isPending}
        renderIncidentAside={(pr) =>
          pr.href ? (
            <>
              <h2 className="card-title aside-title">Evidence graph</h2>
              <IncidentEvidence key={pr.href} incidentId={incidentIdOf(pr.href)} stacked />
            </>
          ) : null
        }
      >
        {children}
      </AnalyticsDashboard>
    </div>
  );
}

/** Every signed-in page lives inside the analytics dashboard; the URL picks the page. */
export default function DashboardLayout({ children }: { children: ReactNode }) {
  return <AuthGate>{(me) => <Shell me={me}>{children}</Shell>}</AuthGate>;
}
