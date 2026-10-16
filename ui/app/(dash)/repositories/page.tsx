"use client";

import { ArrowUpRight, ChevronRight, Search } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Tag, buttonClass } from "@/components/app/kit";
import { avatarUrl, useWorkspace } from "@/components/app/shell";
import { workspaceTabs } from "@/components/app/tabs";
import { Topbar } from "@/components/app/topbar";
import { LogoMark } from "@/components/landing/logo";
import { useInstallUrl } from "@/lib/api/hooks";
import { cn } from "@/lib/utils";
import { ago } from "@/lib/workspace";

/** Repositories where the Kaizen GitHub App is installed and the user has access. */
export default function RepositoriesPage() {
  const { ws } = useWorkspace();
  const install = useInstallUrl();
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const list = ws.repos.filter((r) => !q || r.name.toLowerCase().includes(q));
  const now = Date.now();

  const connect = install.data?.url && (
    <a href={install.data.url} target="_blank" rel="noreferrer" className={buttonClass("secondary")}>
      Connect repository <ArrowUpRight size={13} />
    </a>
  );

  return (
    <div className="flex flex-1 flex-col">
      <Topbar
        crumbs={[{ label: <span className="inline-flex items-center gap-1.5"><LogoMark className="size-3.5 text-accent" />Kaizen</span>, href: "/overview" }, { label: "Repositories" }]}
        tabs={workspaceTabs(ws, "repositories")}
        actions={connect}
      />

      <header data-enter className="border-b border-line px-6 py-6">
        <h1 className="text-[20px] font-semibold tracking-[-0.015em]">Repositories</h1>
        <p className="mt-1 text-[13px] text-ink-2">
          Repositories come from the Kaizen GitHub App installation. Your role follows your GitHub permissions.
        </p>
      </header>

      {ws.repos.length === 0 ? (
        <div data-enter className="px-6 py-16 text-center">
          <p className="text-[14px] font-medium">No repositories connected</p>
          <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-3">
            Install the Kaizen GitHub App on a repository you can administer. Kaizen imports its history and starts tracking incidents as soon as the App is installed.
          </p>
          {install.data?.url && (
            <a href={install.data.url} target="_blank" rel="noreferrer" className={buttonClass("primary", "mt-4")}>
              Connect a repository
            </a>
          )}
        </div>
      ) : (
        <>
          {ws.repos.length > 3 && (
            <div data-enter className="border-b border-line px-6 py-2.5">
              <label className="flex h-7 max-w-xs items-center gap-2 rounded-md border border-line bg-panel-2 px-2 focus-within:border-ink-4">
                <Search size={13} className="text-ink-3" />
                <span className="sr-only">Find a repository</span>
                <input
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Find a repository…"
                  className="h-full flex-1 bg-transparent text-[12.5px] outline-none placeholder:text-ink-4"
                />
              </label>
            </div>
          )}
          <ul data-enter aria-label="Repositories">
            {list.map((r) => {
              const [owner, name] = r.name.split("/");
              const peak = Math.max(1, ...r.daily);
              return (
                <li key={r.name}>
                  <Link href={`/repositories/${r.name}`} className="group grid grid-cols-[20px_minmax(0,1fr)_auto] items-center gap-3 border-b border-line px-6 py-3 transition-colors hover:bg-hover sm:grid-cols-[20px_minmax(0,1fr)_120px_130px_16px]">
                    {/* eslint-disable-next-line @next/next/no-img-element -- owner avatar */}
                    <img src={avatarUrl(owner, 40)} alt="" className="size-5 rounded-[5px] bg-raised" />
                    <span className="min-w-0">
                      <span className="flex items-center gap-2">
                        <span className="truncate text-[13.5px]">
                          <span className="text-ink-3">{owner} / </span>
                          <span className="font-semibold text-ink">{name}</span>
                        </span>
                        <Tag tone={r.role === "approver" ? "green" : "neutral"}>{r.role}</Tag>
                      </span>
                      <span className="mt-0.5 block text-[12px] text-ink-3">
                        {r.role === "approver" ? "Can approve fixes and manage API keys" : "Read-only access"}
                      </span>
                    </span>
                    <span className="hidden h-5 items-end gap-[2px] sm:flex" role="img" aria-label={`${r.total} incidents in the last 14 days`}>
                      {r.daily.map((n, i) => (
                        <span key={i} className={cn("w-[5px] rounded-[1.5px]", n ? "bg-red" : "bg-ink/[0.1]")} style={{ height: `${n ? 30 + (n / peak) * 70 : 20}%` }} />
                      ))}
                    </span>
                    <span className="text-right text-[12px] sm:text-left">
                      <span className={cn("num block", r.open ? "text-red" : "text-ink-2")}>
                        {r.open} open {r.open === 1 ? "incident" : "incidents"}
                      </span>
                      <span className="block text-ink-3">{r.last ? `last ${ago(now - r.last)} ago` : "no incidents"}</span>
                    </span>
                    <ChevronRight size={14} className="hidden text-ink-4 transition-transform group-hover:translate-x-0.5 group-hover:text-ink-2 sm:block" />
                  </Link>
                </li>
              );
            })}
          </ul>
          {list.length === 0 && <p className="px-6 py-6 text-[13px] text-ink-3">No repositories match “{query.trim()}”.</p>}
        </>
      )}
    </div>
  );
}
