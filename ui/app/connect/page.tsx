"use client";

import { ArrowRight, Check, CircleAlert, Loader2, Lock } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { AuthGate } from "@/components/auth-gate";
import { Logo } from "@/components/landing/logo";
import { LoadingState } from "@/components/states";
import { MOCK } from "@/lib/api/client";
import { useConnectRepos, useInstallUrl, useOnboarding, useSignOut, type RepoOnboarding } from "@/lib/api/hooks";
import type { components } from "@/lib/api/schema";
import { cn } from "@/lib/utils";

type User = components["schemas"]["User"];

const STEPS = ["Connect GitHub", "Choose repositories", "Start monitoring"] as const;

function Stepper({ active }: { active: 0 | 1 | 2 }) {
  return (
    <ol className="flex items-center gap-2 text-[12.5px]" aria-label="Setup progress">
      {STEPS.map((label, i) => {
        const done = i < active;
        return (
          <li key={label} className="flex items-center gap-2" aria-current={i === active ? "step" : undefined}>
            <span
              className={cn(
                "grid size-5 place-items-center rounded-full text-[11px] font-medium",
                done ? "bg-green text-panel" : i === active ? "bg-ink text-panel" : "bg-raised text-ink-3",
              )}
            >
              {done ? <Check className="size-3" strokeWidth={2.4} aria-hidden /> : i + 1}
            </span>
            <span className={cn("whitespace-nowrap", i === active ? "font-medium text-ink" : "text-ink-3")}>{label}</span>
            {i < STEPS.length - 1 && <span className="mx-1 h-px w-6 bg-line" aria-hidden />}
          </li>
        );
      })}
    </ol>
  );
}

const selectable = (r: RepoOnboarding) => r.role === "approver" && (r.state === "not_started" || r.state === "failed");

function why(r: RepoOnboarding): string | null {
  if (r.state === "unavailable") return "Not enabled on this deployment";
  if (r.role !== "approver" && r.state !== "ready" && r.state !== "importing") return "Needs push access to start";
  return null;
}

function StateBadge({ r }: { r: RepoOnboarding }) {
  const m: Record<string, [string, string]> = {
    ready: ["Monitoring", "bg-green/15 text-green"],
    importing: ["Importing", "bg-accent/15 text-accent"],
    failed: ["Failed", "bg-red/12 text-red"],
    not_started: ["Not started", "bg-raised text-ink-3"],
    unavailable: ["Unavailable", "bg-raised text-ink-3"],
  };
  const [label, cls] = m[r.state];
  return <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", cls)}>{label}</span>;
}

function refreshSession() {
  if (MOCK) return window.location.reload();
  const qs = new URLSearchParams({ next: "/connect?manage=1", origin: window.location.origin });
  window.location.assign(`/auth/login?${qs.toString()}`);
}

function Choose({ repos, user }: { repos: RepoOnboarding[]; user: User }) {
  const connect = useConnectRepos();
  const install = useInstallUrl();
  const choices = repos.filter(selectable);
  const [picked, setPicked] = useState<string[] | null>(null);
  const chosen = picked ?? (choices.length === 1 ? [choices[0].repo] : []);
  const toggle = (repo: string) =>
    setPicked((cur) => {
      const base = cur ?? chosen;
      return base.includes(repo) ? base.filter((x) => x !== repo) : [...base, repo];
    });

  return (
    <section aria-labelledby="choose-title">
      <h1 id="choose-title" className="text-[22px] font-semibold tracking-[-0.02em]">
        Which repository should Kaizen watch?
      </h1>
      <p className="mt-2 text-[13.5px] leading-[1.55] text-ink-2">
        Signed in as <b>{user.login}</b>. Kaizen reads the commit history and deployments of what you pick. It never
        pushes to your default branch.
      </p>
      <ul aria-label="Repositories" className="mt-6 divide-y divide-line overflow-hidden rounded-lg border border-line bg-panel-2">
        {repos.map((r) => {
          const can = selectable(r);
          const note = r.state === "failed" ? (r.detail ?? "The last import failed. Try again.") : why(r);
          return (
            <li key={r.repo}>
              <label
                className={cn(
                  "flex items-center gap-3 px-4 py-3",
                  can ? "cursor-pointer hover:bg-hover" : "cursor-not-allowed opacity-60",
                )}
              >
                <input
                  type="checkbox"
                  className="size-4 accent-[var(--accent)]"
                  disabled={!can}
                  checked={chosen.includes(r.repo)}
                  onChange={() => toggle(r.repo)}
                  aria-label={`Monitor ${r.repo}`}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13.5px] font-medium">{r.repo}</span>
                  {note && (
                    <span className={cn("mt-0.5 flex items-center gap-1 text-[12px]", r.state === "failed" ? "text-red" : "text-ink-3")}>
                      {!can && r.state !== "failed" && <Lock className="size-3" aria-hidden />}
                      {note}
                    </span>
                  )}
                </span>
                <StateBadge r={r} />
              </label>
            </li>
          );
        })}
      </ul>
      <button
        type="button"
        disabled={chosen.length === 0 || connect.isPending}
        onClick={() => connect.mutate(chosen)}
        className="mt-5 inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-ink text-[13.5px] font-medium text-panel hover:brightness-90 disabled:opacity-45"
      >
        {connect.isPending ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
        {chosen.length <= 1 ? "Start monitoring" : `Start monitoring ${chosen.length} repositories`}
        {!connect.isPending && <ArrowRight className="size-4" aria-hidden />}
      </button>
      <p className="mt-4 text-[12.5px] text-ink-3">
        Don&apos;t see your repository?{" "}
        {install.data?.url && (
          <a className="underline" href={install.data.url} target="_blank" rel="noreferrer">
            Add it on GitHub
          </a>
        )}
        {install.data?.url && ", then "}
        <button type="button" className="underline" onClick={refreshSession}>
          refresh the list
        </button>
        .
      </p>
      {connect.isError && (
        <p role="alert" className="mt-3 flex items-center gap-1.5 text-[12.5px] text-red">
          <CircleAlert className="size-3.5" aria-hidden /> Could not start: {(connect.error as Error).message}
        </p>
      )}
    </section>
  );
}

function Learned({ r }: { r: RepoOnboarding }) {
  const s = r.stats;
  const items: [number, string][] = [
    [s.commits, "commits read"],
    [s.functions, "functions mapped"],
    [s.services, "services"],
    [s.endpoints, "endpoints"],
  ];
  return (
    <dl className="mt-3 grid grid-cols-4 gap-2 text-center">
      {items.map(([n, label]) => (
        <div key={label} className="rounded-md bg-raised px-2 py-2">
          <dt className="sr-only">{label}</dt>
          <dd className="text-[16px] font-semibold tabular-nums">{n}</dd>
          <p className="text-[11px] text-ink-3" aria-hidden>
            {label}
          </p>
        </div>
      ))}
    </dl>
  );
}

function Progress({ repos }: { repos: RepoOnboarding[] }) {
  const active = repos.filter((r) => r.state === "importing" || r.state === "ready");
  const allReady = active.length > 0 && active.every((r) => r.state === "ready");
  return (
    <section aria-labelledby="progress-title">
      <h1 id="progress-title" className="text-[22px] font-semibold tracking-[-0.02em]">
        {allReady ? "Kaizen is watching" : "Reading your repository"}
      </h1>
      <p className="mt-2 text-[13.5px] leading-[1.55] text-ink-2">
        {allReady
          ? "The code graph is built. Deployments and pushes now update it automatically. When a deploy breaks something, Kaizen traces it to the commit and function behind it."
          : "Kaizen is cloning the history and mapping every commit to the functions it changed. This takes a minute."}
      </p>
      <ul className="mt-6 space-y-3" aria-live="polite">
        {active.map((r) => (
          <li key={r.repo} className="rounded-lg border border-line bg-panel-2 p-4">
            <div className="flex items-center gap-2">
              {r.state === "ready" ? (
                <Check className="size-4 text-green" strokeWidth={2.4} aria-hidden />
              ) : (
                <Loader2 className="size-4 animate-spin text-accent" aria-hidden />
              )}
              <span className="flex-1 truncate text-[13.5px] font-medium">{r.repo}</span>
              <StateBadge r={r} />
            </div>
            <Learned r={r} />
          </li>
        ))}
      </ul>
      {allReady && (
        <div className="mt-6 space-y-3">
          <Link
            href="/overview"
            className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-ink text-[13.5px] font-medium text-panel hover:brightness-90"
          >
            Open dashboard <ArrowRight className="size-4" aria-hidden />
          </Link>
          {active.some((r) => !r.has_api_key && r.role === "approver") && (
            <p className="text-[12.5px] leading-[1.55] text-ink-3">
              To catch live failures, also send your app&apos;s errors to Kaizen: open{" "}
              <Link className="underline" href={`/repositories/${active.find((r) => !r.has_api_key)?.repo}`}>
                the repository page
              </Link>{" "}
              to create an ingest key and copy the snippet.
            </p>
          )}
        </div>
      )}
    </section>
  );
}

function Install() {
  const install = useInstallUrl();
  const retry = refreshSession;
  return (
    <section aria-labelledby="install-title">
      <h1 id="install-title" className="text-[22px] font-semibold tracking-[-0.02em]">
        Give Kaizen access to a repository
      </h1>
      <p className="mt-2 text-[13.5px] leading-[1.55] text-ink-2">
        Install the Kaizen GitHub App and pick the repository on GitHub&apos;s page. It asks for read access to code and
        deployments. Come back here afterwards.
      </p>
      {install.data?.url ? (
        <a
          href={install.data.url}
          target="_blank"
          rel="noreferrer"
          className="mt-6 inline-flex h-10 w-full items-center justify-center rounded-md bg-ink text-[13.5px] font-medium text-panel hover:brightness-90"
        >
          Install on GitHub
        </a>
      ) : (
        <p className="mt-6 text-[13px] text-ink-3">The GitHub App is not configured on this server.</p>
      )}
      <button
        type="button"
        onClick={retry}
        className="mt-3 inline-flex h-10 w-full items-center justify-center rounded-md border border-line bg-panel-2 text-[13.5px] font-medium hover:bg-raised"
      >
        I installed it, continue
      </button>
    </section>
  );
}

function Flow({ user }: { user: User }) {
  const router = useRouter();
  const manage = useSearchParams().has("manage");
  const q = useOnboarding();
  const signOut = useSignOut();
  const repos = useMemo(() => q.data ?? [], [q.data]);
  const hasRepos = repos.length > 0;
  const importing = repos.some((r) => r.state === "importing");
  const ready = repos.some((r) => r.state === "ready");
  // Returning users go straight to the dashboard; "manage" (or a pending import) keeps them here.
  const [sawImport, setSawImport] = useState(false);
  useEffect(() => {
    if (importing) setSawImport(true);
  }, [importing]);
  useEffect(() => {
    if (q.data && ready && !importing && !manage && !sawImport) router.replace("/overview");
  }, [q.data, ready, importing, manage, sawImport, router]);

  const showProgress = importing || sawImport || (manage && ready && !repos.some(selectable));
  const active: 0 | 1 | 2 = !hasRepos ? 0 : showProgress ? 2 : 1;

  return (
    <main className="flex min-h-dvh flex-col bg-panel">
      <header className="flex items-center justify-between px-4 py-4 sm:px-8">
        <Link href="/" aria-label="Kaizen home" className="rounded-sm">
          <Logo />
        </Link>
        <div className="flex items-center gap-4 text-[12.5px] text-ink-3">
          {ready && (
            <Link href="/overview" className="hover:text-ink">
              Skip to dashboard
            </Link>
          )}
          <button type="button" onClick={() => signOut.mutate()} className="hover:text-ink">
            Sign out
          </button>
        </div>
      </header>
      <div className="flex flex-1 justify-center px-4 pb-24 pt-8">
        <div className="w-full max-w-[460px]">
          <div className="mb-8 overflow-x-auto">
            <Stepper active={active} />
          </div>
          {q.isLoading ? (
            <LoadingState rows={3} />
          ) : q.error ? (
            <p role="alert" className="text-[13px] text-red">
              Could not load your repositories. Refresh to try again.
            </p>
          ) : !hasRepos ? (
            <Install />
          ) : showProgress ? (
            <Progress repos={repos} />
          ) : (
            <Choose repos={repos} user={user} />
          )}
        </div>
      </div>
    </main>
  );
}

export default function ConnectPage() {
  return (
    <Suspense>
      <AuthGate>{(me) => <Flow user={me} />}</AuthGate>
    </Suspense>
  );
}
