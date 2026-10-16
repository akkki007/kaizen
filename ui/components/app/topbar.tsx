"use client";

import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { Fragment, type ReactNode } from "react";
import { haptic } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { NavButton } from "./shell";

export type Crumb = { label: ReactNode; href?: string };
export type Tab = { label: string; href: string; count?: number; active?: boolean };

/** Sticky bar at the top of the work panel: breadcrumb, section tabs, and actions on the right. */
export function Topbar({ crumbs, tabs, actions }: { crumbs: Crumb[]; tabs?: Tab[]; actions?: ReactNode }) {
  return (
    <header className="sticky top-0 z-20 flex h-[46px] shrink-0 items-center gap-3 border-b border-line bg-panel/90 px-4 backdrop-blur-md sm:px-6">
      <NavButton />
      {/* Small screens show only the current page; the menu button covers the rest. */}
      <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5 text-[13px]">
        {crumbs.map((c, i) => {
          const last = i === crumbs.length - 1;
          return (
            <Fragment key={i}>
              {i > 0 && <ChevronRight size={13} className="shrink-0 text-ink-4 max-sm:hidden" aria-hidden="true" />}
              {c.href && !last ? (
                <Link href={c.href} className="truncate text-ink-3 transition-colors hover:text-ink max-sm:hidden">
                  {c.label}
                </Link>
              ) : (
                <span className={cn("truncate", last ? "font-medium text-ink" : "text-ink-3")} aria-current={last ? "page" : undefined}>
                  {c.label}
                </span>
              )}
            </Fragment>
          );
        })}
      </nav>

      {tabs && tabs.length > 0 && (
        <nav aria-label="Sections" className="ml-2 hidden items-center gap-0.5 lg:flex">
          {tabs.map((t) => (
            <Link
              key={t.href}
              href={t.href}
              aria-current={t.active ? "page" : undefined}
              onClick={() => !t.active && haptic("tap")}
              className={cn(
                "inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-[12.5px] transition-colors",
                t.active ? "border-line bg-raised font-medium text-ink" : "border-transparent text-ink-3 hover:text-ink",
              )}
            >
              {t.label}
              {t.count !== undefined && <span className="num text-ink-3">{t.count}</span>}
            </Link>
          ))}
        </nav>
      )}

      <div className="ml-auto flex shrink-0 items-center gap-2">{actions}</div>
    </header>
  );
}
