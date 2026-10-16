"use client";

import { useTheme } from "next-themes";
import { useEffect, useState, type ReactNode } from "react";
import { Button, Kbd, Segmented } from "@/components/app/kit";
import { avatarUrl, useWorkspace } from "@/components/app/shell";
import { Topbar } from "@/components/app/topbar";
import { LogoMark } from "@/components/landing/logo";
import { useSignOut } from "@/lib/api/hooks";

const THEMES = [
  { id: "dark", label: "Dark" },
  { id: "light", label: "Light" },
  { id: "system", label: "System" },
] as const;

function Row({ title, detail, children }: { title: string; detail: ReactNode; children?: ReactNode }) {
  return (
    <div className="flex flex-col gap-3 border-b border-line px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="text-[13px] font-medium text-ink">{title}</p>
        <p className="mt-0.5 text-[12.5px] text-ink-3">{detail}</p>
      </div>
      {children}
    </div>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section data-enter aria-label={title}>
      <h2 className="border-b border-line px-6 pb-2 pt-7 text-[12px] font-medium uppercase tracking-[0.06em] text-ink-3">{title}</h2>
      {children}
    </section>
  );
}

export default function SettingsPage() {
  const { me, ws } = useWorkspace();
  const signOut = useSignOut();
  const { theme, setTheme } = useTheme();
  // next-themes only knows the stored theme after mount.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const approverOn = ws.repos.filter((r) => r.role === "approver").length;

  return (
    <div className="flex flex-1 flex-col">
      <Topbar crumbs={[{ label: <span className="inline-flex items-center gap-1.5"><LogoMark className="size-3.5 text-accent" />Kaizen</span>, href: "/overview" }, { label: "Settings" }]} />
      <header data-enter className="border-b border-line px-6 py-6">
        <h1 className="text-[20px] font-semibold tracking-[-0.015em]">Settings</h1>
        <p className="mt-1 text-[13px] text-ink-2">Your account and how Kaizen looks in this browser.</p>
      </header>

      <div>
        <Group title="Account">
          <div className="flex items-center gap-3 border-b border-line px-6 py-4">
            {/* eslint-disable-next-line @next/next/no-img-element -- GitHub avatar */}
            <img src={avatarUrl(me.login, 80)} alt="" className="size-10 rounded-full bg-raised" />
            <div className="min-w-0 flex-1">
              <p className="text-[14px] font-medium">{me.login}</p>
              <p className="text-[12.5px] text-ink-3">Signed in with GitHub</p>
            </div>
            <Button onClick={() => signOut.mutate()} disabled={signOut.isPending}>
              {signOut.isPending ? "Signing out…" : "Sign out"}
            </Button>
          </div>
          <Row
            title="Repository access"
            detail={`${ws.repos.length} ${ws.repos.length === 1 ? "repository" : "repositories"}, approver on ${approverOn}. Access follows your GitHub permissions and the Kaizen GitHub App installation.`}
          />
        </Group>

        <Group title="Appearance">
          <Row title="Theme" detail="Dark is the default. Light and system follow your preference.">
            <Segmented label="Theme" options={THEMES} value={mounted ? ((theme as (typeof THEMES)[number]["id"]) ?? "dark") : "dark"} onChange={setTheme} />
          </Row>
        </Group>

        <Group title="Keyboard">
          {[
            ["Move through incidents", ["↑", "↓"], "or j / k"],
            ["Open the selected incident", ["↵"], ""],
            ["Close the preview", ["Esc"], ""],
            ["Send a question", ["↵"], "Shift ↵ for a new line"],
          ].map(([label, keys, note]) => (
            <div key={label as string} className="flex items-center justify-between border-b border-line px-6 py-3 text-[13px]">
              <span className="text-ink-2">{label}</span>
              <span className="flex items-center gap-1.5 text-[12px] text-ink-3">
                {(keys as string[]).map((k) => (
                  <Kbd key={k}>{k}</Kbd>
                ))}
                {note && <span className="ml-1">{note}</span>}
              </span>
            </div>
          ))}
        </Group>
      </div>
    </div>
  );
}
