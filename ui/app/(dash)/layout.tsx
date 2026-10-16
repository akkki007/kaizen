"use client";

import type { ReactNode } from "react";
import { AppShell } from "@/components/app/shell";
import { AuthGate } from "@/components/auth-gate";

/** Every signed-in page lives in the app shell; the URL picks the page. */
export default function DashboardLayout({ children }: { children: ReactNode }) {
  return <AuthGate>{(me) => <AppShell me={me}>{children}</AppShell>}</AuthGate>;
}
