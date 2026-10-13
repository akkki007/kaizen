"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, type ReactNode } from "react";
import { ErrorState, LoadingState } from "@/components/states";
import { ApiError } from "@/lib/api/client";
import { useMe } from "@/lib/api/hooks";
import type { components } from "@/lib/api/schema";

type User = components["schemas"]["User"];

function Gate({ children }: { children: (me: User) => ReactNode }) {
  const me = useMe();
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const unauthenticated = me.error instanceof ApiError && me.error.status === 401;

  useEffect(() => {
    if (unauthenticated) {
      // Sign-in returns to this exact page afterwards (issue #97).
      const qs = search.toString();
      const next = pathname + (qs ? `?${qs}` : "");
      router.replace(`/login?next=${encodeURIComponent(next)}`);
    }
  }, [unauthenticated, router, pathname, search]);

  if (me.isLoading || unauthenticated) {
    return (
      <div className="mx-auto max-w-3xl p-8">
        <LoadingState />
      </div>
    );
  }
  if (me.error || !me.data) {
    return (
      <div className="mx-auto max-w-3xl p-8">
        <ErrorState error={me.error ?? new Error("no session")} onRetry={() => me.refetch()} />
      </div>
    );
  }
  return <>{children(me.data)}</>;
}

/** Renders children only for a signed-in user; otherwise redirects to /login (401) or shows the error. */
export function AuthGate({ children }: { children: (me: User) => ReactNode }) {
  // useSearchParams needs a Suspense boundary for the static prerender.
  return (
    <Suspense
      fallback={
        <div className="mx-auto max-w-3xl p-8">
          <LoadingState />
        </div>
      }
    >
      <Gate>{children}</Gate>
    </Suspense>
  );
}
