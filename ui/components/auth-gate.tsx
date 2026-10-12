"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { ErrorState, LoadingState } from "@/components/states";
import { ApiError } from "@/lib/api/client";
import { useMe } from "@/lib/api/hooks";
import type { components } from "@/lib/api/schema";

type User = components["schemas"]["User"];

/** Renders children only for a signed-in user; otherwise redirects to /login (401) or shows the error. */
export function AuthGate({ children }: { children: (me: User) => ReactNode }) {
  const me = useMe();
  const router = useRouter();
  const unauthenticated = me.error instanceof ApiError && me.error.status === 401;

  useEffect(() => {
    if (unauthenticated) router.replace("/login");
  }, [unauthenticated, router]);

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
