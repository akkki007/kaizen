"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { Button } from "@/components/ui/button";
import { MOCK } from "@/lib/api/client";
import { useInstallUrl } from "@/lib/api/hooks";

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

function LoginInner() {
  const params = useSearchParams();
  const router = useRouter();
  const install = useInstallUrl();
  const error = params.get("error");
  const next = params.get("next");
  const state = error ? (ERRORS[error] ?? ERRORS.invalid) : null;
  const noRepos = error === "no_repos";

  function signIn() {
    if (MOCK) {
      // Full page navigation so AuthGate picks up the mock session immediately.
      window.location.assign(next && next.startsWith("/") ? next : "/overview");
      return;
    }
    const qs = new URLSearchParams();
    qs.set("next", next && next.startsWith("/") ? next : "/overview");
    qs.set("origin", window.location.origin);
    window.location.assign(`/auth/login?${qs.toString()}`);
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col items-center justify-center gap-6 p-6 text-center">
      <h1 className="text-2xl font-semibold">Kaizen</h1>
      <p className="text-sm text-muted-foreground">
        Connect production failures to the code changes behind them. You will see the repositories
        where the Kaizen GitHub App is installed and you have access.
      </p>

      {state && (
        <div role="alert" className="card card-pad w-full text-left" data-kind={noRepos ? "info" : "error"}>
          <p className="card-title">{state.title}</p>
          <p className="text-sm text-muted-foreground">{state.detail}</p>
          {noRepos && install.data?.url && (
            <a
              href={install.data.url}
              target="_blank"
              rel="noreferrer"
              className="btn btn-primary press mt-3 inline-flex"
            >
              Install the GitHub App
            </a>
          )}
        </div>
      )}

      <Button onClick={signIn}>
        <svg viewBox="0 0 16 16" className="size-4 fill-current" aria-hidden>
          <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
        </svg>
        {noRepos ? "I installed it — try again" : "Sign in with GitHub"}
      </Button>
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
