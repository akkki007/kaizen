"use client";

import { ArrowUpRight, KeyRound } from "lucide-react";
import { use, useState, type FormEvent } from "react";
import { Button, Skeleton, Tag, buttonClass, type TagTone } from "@/components/app/kit";
import { avatarUrl, useWorkspace } from "@/components/app/shell";
import { Section } from "@/components/incident-investigation";
import { CodeBlock } from "@/components/ui/code-block";
import { useApiKeys, useBackfillStatus, useCreateKey, useInstallUrl, useReimport, useRevokeKey } from "@/lib/api/hooks";
import { haptic } from "@/lib/motion";
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
  const { me, ws } = useWorkspace();
  const role = me.repos[repo] as "viewer" | "approver" | undefined;
  const stats = ws.repos.find((r) => r.name === repo);

  return (
    <div className="flex flex-1 flex-col">
      <header data-enter className="flex flex-col gap-4 border-b border-line px-6 py-6 sm:flex-row sm:items-center">
        {/* eslint-disable-next-line @next/next/no-img-element -- owner avatar */}
        <img src={avatarUrl(owner, 96)} alt="" className="size-12 rounded-xl border border-line bg-raised" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="text-[22px] font-semibold tracking-[-0.02em]">
              <span className="font-normal text-ink-3">{owner} / </span>
              {name}
            </h1>
            {role && <Tag tone={role === "approver" ? "green" : "outline"}>{role}</Tag>}
          </div>
          <p className="mt-1 text-[13px] text-ink-2">API keys authenticate the runtime events Kaizen uses to detect incidents in this repository.</p>
        </div>
        {stats && (
          <div className="flex shrink-0 divide-x divide-line overflow-hidden rounded-lg border border-line text-[12px]">
            <div className="px-3.5 py-2">
              <p className="text-ink-3">Open</p>
              <p className={`num mt-0.5 text-[15px] font-medium ${stats.open ? "text-red" : ""}`}>{stats.open}</p>
            </div>
            <div className="px-3.5 py-2">
              <p className="text-ink-3">All incidents</p>
              <p className="num mt-0.5 text-[15px] font-medium">{stats.total}</p>
            </div>
          </div>
        )}
        <a href={`https://github.com/${repo}`} target="_blank" rel="noreferrer" className={buttonClass("secondary", "shrink-0")}>
          Open on GitHub <ArrowUpRight size={13} />
        </a>
      </header>

      {role ? (
        <div>
          <Import repo={repo} canManage={role === "approver"} />
          <Keys repo={repo} canManage={role === "approver"} />
        </div>
      ) : (
        <div data-enter className="px-6 py-16 text-center">
          <p className="text-[14px] font-medium">No access</p>
          <p className="mt-1 text-[13px] text-ink-3">You do not have access to {repo}. Install the GitHub App on it and sign in again.</p>
        </div>
      )}
    </div>
  );
}

const JOB_BADGE: Record<string, { tone: TagTone; label: string }> = {
  queued: { tone: "amber", label: "Queued" },
  running: { tone: "amber", label: "Importing…" },
  succeeded: { tone: "green", label: "Up to date" },
  failed: { tone: "red", label: "Failed" },
  dead: { tone: "red", label: "Failed" },
};

/** Import state for the repository: the App installation, the last import and its live progress. */
function Import({ repo, canManage }: { repo: string; canManage: boolean }) {
  const status = useBackfillStatus(repo);
  const reimport = useReimport(repo);
  const install = useInstallUrl();
  const s = status.data;
  const job = s?.job;
  const badge = job ? (JOB_BADGE[job.status] ?? { tone: "neutral" as const, label: job.status }) : null;
  const busy = job ? ["queued", "running"].includes(job.status) : false;

  return (
    <Section id="import-title" title="Import" aside={badge && <Tag tone={badge.tone}>{badge.label}</Tag>}>
      {status.isLoading && <Skeleton className="h-4 w-48" />}
      {status.error && (
        <p role="alert" className="flex items-center gap-2 text-[13px] text-red">
          {status.error.message}
          <Button onClick={() => status.refetch()}>Try again</Button>
        </p>
      )}
      {s && (
        <div className="flex flex-wrap items-center gap-3 text-[13px] text-ink-2">
          <span>
            {job
              ? busy
                ? `Import started ${relativeTime(job.created_at)}`
                : `Last import ${relativeTime(job.updated_at)}`
              : s.backfilled_at
                ? `Last import ${relativeTime(s.backfilled_at)}`
                : "Never imported"}
          </span>
          {!s.installed && <span className="text-ink-3">· GitHub App not installed</span>}
          {canManage && s.installed && !busy && (
            <Button variant="secondary" className="ml-auto" disabled={reimport.isPending} onClick={() => reimport.mutate()}>
              {reimport.isPending ? "Starting…" : "Re-import"}
            </Button>
          )}
        </div>
      )}
      {!s?.installed && install.data?.url && (
        <p className="mt-2.5 text-[13px] text-ink-3">
          Install the GitHub App on this repository to import its history.{" "}
          <a href={install.data.url} target="_blank" rel="noreferrer" className="text-link hover:underline">
            Connect {repo}
          </a>
        </p>
      )}
      {reimport.error && <p role="alert" className="mt-2 text-[13px] text-red">{reimport.error.message}</p>}
      {job?.error && <pre className="mt-3 overflow-x-auto rounded-md bg-red/10 px-3 py-2.5 font-mono text-[12px] text-red">{job.error}</pre>}
    </Section>
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
  const active = keys.data?.filter((k) => !k.revoked_at).length ?? 0;

  async function submit(e: FormEvent) {
    e.preventDefault();
    const made = await create.mutateAsync(label.trim());
    haptic("confirm");
    setCreated(made);
    setLabel("");
  }

  return (
    <>
      <Section id="keys-title" title="API keys" aside={keys.data && <span className="num text-[12px] text-ink-3">{active} active</span>}>
        {canManage && (
          <form onSubmit={submit} className="mb-4 flex gap-2">
            <label className="flex h-7 min-w-0 flex-1 items-center rounded-md border border-line bg-panel-2 px-2.5 focus-within:border-ink-4">
              <span className="sr-only">Key label</span>
              <input
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="Label, e.g. production agent"
                maxLength={80}
                className="w-full bg-transparent text-[12.5px] outline-none placeholder:text-ink-4"
              />
            </label>
            <button type="submit" className={buttonClass("primary")} disabled={create.isPending}>
              {create.isPending ? "Creating…" : "Create key"}
            </button>
          </form>
        )}
        {create.error && <p role="alert" className="mb-3 text-[13px] text-red">{create.error.message}</p>}
        {fresh && (
          <p role="status" className="mb-3 rounded-md bg-green/12 px-3 py-2.5 text-[13px] text-green">
            New key created. Copy it from the example below now; it will not be shown again.
          </p>
        )}
        {keys.isLoading && <Skeleton className="h-10 w-full" />}
        {keys.error && (
          <p role="alert" className="flex items-center gap-2 text-[13px] text-red">
            {keys.error.message}
            <Button onClick={() => keys.refetch()}>Try again</Button>
          </p>
        )}
        {keys.data && keys.data.length === 0 && <p className="text-[13px] text-ink-3">No keys yet. Create one to start sending runtime events.</p>}
        {keys.data && keys.data.length > 0 && (
          <ul aria-label="API keys" className="-mx-6 border-t border-line">
            {keys.data.map((k) => (
              <li key={k.id} className="grid grid-cols-[16px_minmax(0,1fr)_auto_auto] items-center gap-3 border-b border-line px-6 py-2.5 last:border-b-0 sm:grid-cols-[16px_minmax(0,1fr)_auto_auto_72px]">
                <KeyRound size={14} className={k.revoked_at ? "text-ink-4" : "text-ink-3"} />
                <span className={`truncate text-[13px] ${k.revoked_at ? "text-ink-3 line-through" : "text-ink"}`}>{k.label || "Unlabelled key"}</span>
                <span className="hidden text-[12px] text-ink-3 sm:block">created {relativeTime(k.created_at)}</span>
                <Tag tone={k.revoked_at ? "neutral" : "green"}>{k.revoked_at ? "revoked" : "active"}</Tag>
                {canManage && !k.revoked_at ? (
                  <Button variant="ghost" className="justify-self-end text-red hover:text-red" disabled={revoke.isPending} onClick={() => revoke.mutate(k.id)}>
                    Revoke
                  </Button>
                ) : (
                  <span className="hidden sm:block" />
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section id="send-title" title="Send an event">
        <CodeBlock
          code={eventSnippet(repo, fresh ?? "<your-api-key>", origin)}
          language="bash"
          filename="telemetry event"
          accent="#7d84f6"
          showLineNumbers={false}
          highlightLines={fresh ? [2] : undefined}
        />
      </Section>
    </>
  );
}
