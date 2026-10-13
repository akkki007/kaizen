"use client";

import type { CSSProperties } from "react";
import { RepositoriesPage } from "@/components/analytics-dashboard/pages";
import { useInstallUrl, useMe } from "@/lib/api/hooks";

/** The repositories section: the connected list, or how to connect the first one. */
export default function Page() {
  const me = useMe();
  const install = useInstallUrl();
  if (me.data && Object.keys(me.data.repos).length === 0) {
    return (
      <div className="page-content">
        <header className="page-head enter" style={{ "--i": 1 } as CSSProperties}>
          <div className="page-head-text">
            <h1 className="page-title">Repositories</h1>
            <p className="page-sub">Repositories come from the CausalDeploy GitHub App installation.</p>
          </div>
        </header>
        <section className="card card-pad ev-state enter" style={{ "--i": 2 } as CSSProperties}>
          <p className="ev-state-title">No repositories connected</p>
          <p>
            Install the CausalDeploy GitHub App on a repository you can administer. CausalDeploy
            imports its history and starts tracking incidents as soon as the App is installed.
          </p>
          {install.data?.url && (
            <a href={install.data.url} target="_blank" rel="noreferrer" className="btn btn-primary press">
              Connect a repository
            </a>
          )}
        </section>
      </div>
    );
  }
  return <RepositoriesPage />;
}
