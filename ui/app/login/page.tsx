"use client";

import { ArrowLeft, CircleAlert, Info, Loader2 } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useLayoutEffect, useState } from "react";
import { Logo } from "@/components/landing/logo";
import { MOCK, api } from "@/lib/api/client";
import { useInstallUrl } from "@/lib/api/hooks";
import { EASE, gsap, haptic } from "@/lib/motion";

const ERRORS: Record<string, { title: string; detail: string }> = {
  denied: {
    title: "Sign-in was cancelled",
    detail: "You denied the GitHub authorization request. You can try again at any time.",
  },
  expired: {
    title: "That sign-in link expired",
    detail: "Sign-in links are single-use and short-lived for security. Start again below.",
  },
  github: {
    title: "GitHub rejected the sign-in",
    detail: "Try again. If it keeps failing, ask the project owner to check the GitHub App configuration.",
  },
  invalid: {
    title: "That sign-in link is not valid",
    detail: "Start again below.",
  },
  no_repos: {
    title: "No repositories yet",
    detail:
      "The Kaizen GitHub App is not installed on any repository you have access to. Install it on your repositories, then try again.",
  },
};

function GitHubMark() {
  return (
    <svg viewBox="0 0 16 16" className="size-4 fill-current" aria-hidden>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

function LoginInner() {
  const params = useSearchParams();
  const install = useInstallUrl();
  const [leaving, setLeaving] = useState(false);
  const error = params.get("error");
  const next = params.get("next");
  const state = error ? (ERRORS[error] ?? ERRORS.invalid) : null;
  const noRepos = error === "no_repos";

  useLayoutEffect(() => {
    const mm = gsap.matchMedia();
    mm.add("(prefers-reduced-motion: no-preference)", () => {
      gsap.fromTo("[data-in]", { autoAlpha: 0, y: 12 }, { autoAlpha: 1, y: 0, duration: 0.6, ease: EASE, stagger: 0.06 });
      if (state) gsap.fromTo("[role=alert]", { x: -6 }, { x: 0, duration: 0.5, ease: "elastic.out(1, 0.4)", delay: 0.3 });
    });
    if (state) haptic("warn");
    return () => mm.revert();
  }, [state]);

  function signIn() {
    haptic("confirm");
    setLeaving(true);
    if (MOCK) {
      // Full page navigation so AuthGate picks up the mock session immediately.
      window.location.assign(next && next.startsWith("/") ? next : "/connect");
      return;
    }
    const qs = new URLSearchParams();
    qs.set("next", next && next.startsWith("/") ? next : "/connect");
    qs.set("origin", window.location.origin);
    window.location.assign(`/auth/login?${qs.toString()}`);
  }

  async function tryDemo() {
    setLeaving(true);
    const r = await api.POST("/auth/demo").catch(() => null);
    if (!r?.response.ok) return void setLeaving(false);
    const repo = install.data?.demo_repo;
    window.location.assign(repo ? `/graph?repo=${encodeURIComponent(repo)}` : "/overview");
  }

  return (
    <main className="flex min-h-dvh flex-col bg-panel">
      <header className="flex items-center justify-between px-4 py-4 sm:px-8">
        <Link href="/" aria-label="Kaizen home" className="rounded-sm">
          <Logo />
        </Link>
        <Link href="/" className="inline-flex items-center gap-1.5 text-[12.5px] text-ink-3 transition-colors hover:text-ink">
          <ArrowLeft className="size-3.5" strokeWidth={1.8} aria-hidden />
          Back to site
        </Link>
      </header>

      <div className="flex flex-1 items-center justify-center px-4 pb-24">
        <div className="w-full max-w-[380px]">
          <h1 data-in className="text-[23px] font-semibold tracking-[-0.022em]">
            Sign in to Kaizen
          </h1>
          <p data-in className="mt-2 text-[13.5px] leading-[1.55] text-ink-2">
            Connect GitHub, choose a repository, and Kaizen starts watching it. Takes about a minute.
          </p>

          {state && (
            <div
              role="alert"
              data-kind={noRepos ? "info" : "error"}
              className={
                noRepos
                  ? "mt-6 flex gap-3 rounded-lg border border-line bg-panel-2 p-3.5 shadow-xs"
                  : "mt-6 flex gap-3 rounded-lg border border-transparent bg-red/12 p-3.5"
              }
            >
              {noRepos ? (
                <Info className="mt-px size-4 shrink-0 text-accent" strokeWidth={1.8} aria-hidden />
              ) : (
                <CircleAlert className="mt-px size-4 shrink-0 text-red" strokeWidth={1.8} aria-hidden />
              )}
              <div className="min-w-0">
                <p className="text-[13.5px] font-medium">{state.title}</p>
                <p className="mt-0.5 text-[12.5px] leading-[1.5] text-ink-2">{state.detail}</p>
                {noRepos && install.data?.url && (
                  <a
                    href={install.data.url}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-2.5 inline-flex h-[26px] items-center rounded-md border border-line bg-panel-2 px-2.5 text-[12px] font-medium shadow-xs transition-colors hover:bg-raised"
                  >
                    Install the GitHub App
                  </a>
                )}
              </div>
            </div>
          )}

          <button
            data-in
            type="button"
            onClick={signIn}
            disabled={leaving}
            className="mt-6 inline-flex h-9 w-full items-center justify-center gap-2 rounded-md bg-ink text-[13px] font-medium text-panel shadow-[inset_0_-1px_0_rgba(0,0,0,0.2)] hover:brightness-90 active:brightness-75 disabled:cursor-wait"
          >
            {leaving ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <GitHubMark />}
            {noRepos ? "I installed it — try again" : "Sign in with GitHub"}
          </button>

          {install.data?.demo && (
            <button
              data-in
              type="button"
              onClick={tryDemo}
              disabled={leaving}
              className="mt-3 inline-flex h-9 w-full items-center justify-center rounded-md border border-line bg-panel-2 text-[13px] font-medium hover:bg-raised disabled:cursor-wait"
            >
              Try the demo (no GitHub needed)
            </button>
          )}

          <p data-in className="mt-4 text-[12px] leading-[1.5] text-ink-4">
            Kaizen reads commit history and deployments. Fixes are only merged after an approver signs off.
          </p>
        </div>
      </div>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginInner />
    </Suspense>
  );
}
