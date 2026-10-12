"use client";

import Link from "next/link";
import { use, useState, type CSSProperties, type FormEvent } from "react";
import { ArrowRightIcon } from "@/components/analytics-dashboard/icons";
import { CodeBlock } from "@/components/ui/code-block";
import { useApiKeys, useCreateKey, useMe, useRevokeKey } from "@/lib/api/hooks";
import { relativeTime } from "@/lib/time";

function eventSnippet(repo: string, key: string, origin: string) {
  return `curl -X POST ${origin}/api/v1/events \\
  -H "X-API-Key: ${key}" \\
  -H "Content-Type: application/json" \\
  -d '[{
    "type": "telemetry",
    "repo": "${repo}",
    "service": "payments",
    "env": "prod",
    "ts": "${new Date().toISOString().slice(0, 19)}Z",
    "kind": "error",
    "endpoint": "POST /payments",
    "status_code": 500
  }]'`;
}

export default function RepositoryPage({ params }: { params: Promise<{ owner: string; name: string }> }) {
  const { owner, name } = use(params);
  const repo = `${owner}/${name}`;
  const me = useMe();
  const role = me.data?.repos[repo];

  return (
    <div className="page-content repo-page">
      <header className="page-head enter" style={{ "--i": 1 } as CSSProperties}>
        <div className="page-head-text">
          <Link href="/repositories" className="back-link press">
            <ArrowRightIcon size={13} strokeWidth={2} style={{ transform: "rotate(180deg)" }} />
            Repositories
          </Link>
          <h1 className="page-title">
            <span className="repo-org">{owner}/</span>
            {name}
          </h1>
          <p className="page-sub">API keys authenticate the runtime events CausalDeploy uses to detect incidents in this repository.</p>
        </div>
        {role && <span className="tag">{role}</span>}
      </header>

      {role ? (
        <Keys repo={repo} canManage={role === "approver"} />
      ) : (
        me.data && (
          <section className="card card-pad ev-state enter" style={{ "--i": 2 } as CSSProperties}>
            <p className="ev-state-title">No access</p>
            <p>You do not have access to {repo}. Install the GitHub App on it and sign in again.</p>
          </section>
        )
      )}
    </div>
  );
}

function Keys({ repo, canManage }: { repo: string; canManage: boolean }) {
  const keys = useApiKeys(repo);
  const create = useCreateKey(repo);
  const revoke = useRevokeKey(repo);
  const [label, setLabel] = useState("");
  const [created, setCreated] = useState<{ id: number; key: string } | null>(null);
  // The new key is offered until it is revoked; it is never retrievable again after that.
  const fresh = created && !keys.data?.some((k) => k.id === created.id && k.revoked_at) ? created.key : null;
  const origin = typeof window === "undefined" ? "" : window.location.origin;

  async function submit(e: FormEvent) {
    e.preventDefault();
    const made = await create.mutateAsync(label.trim());
    setCreated(made);
    setLabel("");
  }

  return (
    <>
      <section className="card card-pad enter" style={{ "--i": 2 } as CSSProperties} aria-labelledby="keys-title">
        <div className="card-head">
          <h2 id="keys-title" className="card-title">
            API keys
          </h2>
          {keys.data && <span className="leader-count">{keys.data.filter((k) => !k.revoked_at).length} active</span>}
        </div>

        {canManage && (
          <form onSubmit={submit} className="key-form">
            <label className="field">
              <span className="sr-only">Key label</span>
              <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Label, e.g. production agent" maxLength={80} />
            </label>
            <button type="submit" className="btn btn-primary press" disabled={create.isPending}>
              {create.isPending ? "Creating…" : "Create key"}
            </button>
          </form>
        )}
        {create.error && (
          <p role="alert" className="form-error">
            {create.error.message}
          </p>
        )}
        {fresh && (
          <p role="status" className="key-fresh">
            New key created. Copy it from the example below now; it will not be shown again.
          </p>
        )}

        {keys.isLoading && <p className="ev-hint shimmer">Loading keys…</p>}
        {keys.error && (
          <p role="alert" className="form-error">
            {keys.error.message}{" "}
            <button type="button" className="btn press" onClick={() => keys.refetch()}>
              Try again
            </button>
          </p>
        )}
        {keys.data && keys.data.length === 0 && <p className="ev-hint">No keys yet. Create one to start sending runtime events.</p>}
        {keys.data && keys.data.length > 0 && (
          <ul className="key-list">
            {keys.data.map((k) => (
              <li key={k.id} className="key-row" data-revoked={Boolean(k.revoked_at)}>
                <span className="key-label">{k.label || "Unlabelled key"}</span>
                <span className="key-meta">created {relativeTime(k.created_at)}</span>
                <span className="badge" data-kind={k.revoked_at ? "revoked" : "active"}>
                  {k.revoked_at ? "revoked" : "active"}
                </span>
                {canManage && !k.revoked_at ? (
                  <button type="button" className="btn press" disabled={revoke.isPending} onClick={() => revoke.mutate(k.id)}>
                    Revoke
                  </button>
                ) : (
                  <span />
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card card-pad enter" style={{ "--i": 3 } as CSSProperties} aria-labelledby="send-title">
        <div className="card-head">
          <h2 id="send-title" className="card-title">
            Send an event
          </h2>
        </div>
        <CodeBlock
          code={eventSnippet(repo, fresh ?? "<your-api-key>", origin)}
          language="bash"
          filename="telemetry event"
          accent="#4c9bff"
          showLineNumbers={false}
          highlightLines={fresh ? [2] : undefined}
        />
      </section>
    </>
  );
}
