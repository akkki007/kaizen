"use client";

import { ChartColumn, ChevronDown, Flame, Network, Workflow, FolderGit2, LayoutGrid, LogOut, Menu, Settings2, Siren, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { LogoMark } from "@/components/landing/logo";
import { useIncidents, useSignOut } from "@/lib/api/hooks";
import type { components } from "@/lib/api/schema";
import { EASE, gsap, haptic, useLenis } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { ago, toWorkspace, type Workspace } from "@/lib/workspace";
import { Button, ServiceDot, Skeleton } from "./kit";

type User = components["schemas"]["User"];

type Ctx = { me: User; ws: Workspace; openNav: () => void };
const WorkspaceContext = createContext<Ctx | null>(null);

export function useWorkspace() {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace must be used inside <AppShell>");
  return ctx;
}

export const avatarUrl = (login: string, size = 64) => `https://github.com/${encodeURIComponent(login)}.png?size=${size}`;

/* ───────── Sidebar ───────── */

function NavLink({ href, active, icon: Icon, label, count }: { href: string; active: boolean; icon: typeof Siren; label: string; count?: number }) {
  return (
    <Link
      href={href}
      data-nav-item
      data-active={active}
      aria-current={active ? "page" : undefined}
      onClick={() => haptic("tap")}
      className={cn(
        "relative z-10 flex h-[30px] items-center gap-2.5 rounded-md px-2 text-[13px] transition-colors",
        active ? "font-medium text-ink" : "text-ink-2 hover:text-ink",
      )}
    >
      <Icon size={15} strokeWidth={1.8} className={active ? "text-ink" : "text-ink-3"} />
      {label}
      {count !== undefined && count > 0 && <span className="num ml-auto text-[12px] text-ink-3">{count}</span>}
    </Link>
  );
}

function AccountMenu({ me }: { me: User }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const signOut = useSignOut();

  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", esc);
    gsap.fromTo(menu.current, { autoAlpha: 0, y: -4, scale: 0.97 }, { autoAlpha: 1, y: 0, scale: 1, duration: 0.18, ease: EASE });
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-label={`${me.login}, account menu`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex h-8 items-center gap-2 rounded-md px-1.5 text-[13px] font-medium text-ink transition-colors hover:bg-hover"
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- GitHub avatars, no optimizer needed */}
        <img src={avatarUrl(me.login)} alt="" className="size-5 rounded-full bg-raised" />
        {me.login}
        <ChevronDown size={13} className="text-ink-3" />
      </button>
      {open && (
        <div ref={menu} role="menu" aria-label="Account" className="absolute left-0 top-9 z-50 w-56 rounded-lg bg-raised p-1 shadow-pop">
          <div className="px-2 py-1.5">
            <p className="text-[13px] font-medium text-ink">{me.login}</p>
            <p className="truncate text-[12px] text-ink-3">{me.login}@users.noreply.github.com</p>
          </div>
          <div className="my-1 h-px bg-line" />
          <Link role="menuitem" href="/settings" onClick={() => setOpen(false)} className="flex h-7 items-center gap-2 rounded-md px-2 text-[13px] text-ink-2 hover:bg-hover hover:text-ink">
            <Settings2 size={14} /> Settings
          </Link>
          <button
            role="menuitem"
            type="button"
            disabled={signOut.isPending}
            onClick={() => signOut.mutate()}
            className="flex h-7 w-full items-center gap-2 rounded-md px-2 text-[13px] text-red hover:bg-red/10"
          >
            <LogOut size={14} /> {signOut.isPending ? "Signing out…" : "Sign out"}
          </button>
        </div>
      )}
    </div>
  );
}

/** "Synced 9s ago", ticking; red while the last refresh failed. */
function SyncStatus({ at, failed }: { at: number; failed: boolean }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  const s = Math.max(0, Math.round((now - at) / 1000));
  return (
    <p className="flex items-center gap-2 px-2 text-[12px] text-ink-3" aria-live="off">
      <span className={cn("relative size-1.5 rounded-full", failed ? "bg-red" : "bg-green")}>
        {!failed && <span className="absolute inset-0 animate-ping rounded-full bg-green opacity-40 [animation-duration:2.4s]" />}
      </span>
      {failed ? "Offline, retrying" : at ? `Synced ${s < 60 ? `${s}s` : ago(now - at)} ago` : "Syncing…"}
    </p>
  );
}

function Sidebar({ me, ws, syncedAt, failed, onNavigate }: { me: User; ws: Workspace; syncedAt: number; failed: boolean; onNavigate?: () => void }) {
  const path = usePathname();
  const params = useSearchParams();
  const nav = useRef<HTMLDivElement>(null);
  const indicator = useRef<HTMLSpanElement>(null);

  // The active highlight glides between items instead of jumping.
  useLayoutEffect(() => {
    const active = nav.current?.querySelector<HTMLElement>("[data-nav-item][data-active=true]");
    if (!indicator.current) return;
    if (!active) return void gsap.to(indicator.current, { autoAlpha: 0, duration: 0.15 });
    gsap.to(indicator.current, { y: active.offsetTop, height: active.offsetHeight, autoAlpha: 1, duration: 0.35, ease: "power3.out" });
  }, [path]);

  const service = path === "/incidents" ? params.get("service") : null;

  return (
    <div className="flex h-full flex-col gap-5 px-2.5 pb-3 pt-3" onClick={(e) => (e.target as HTMLElement).closest("a") && onNavigate?.()}>
      <div className="flex items-center justify-between">
        <AccountMenu me={me} />
        <Link href="/" aria-label="Kaizen home" className="grid size-7 place-items-center rounded-md text-accent hover:bg-hover">
          <LogoMark className="size-4" />
        </Link>
      </div>

      <nav ref={nav} aria-label="Primary" className="relative flex flex-col gap-px">
        <span ref={indicator} aria-hidden="true" className="invisible absolute inset-x-0 top-0 rounded-md bg-ink/[0.07]" />
        <NavLink href="/overview" active={path === "/overview"} icon={LayoutGrid} label="Overview" />
        <NavLink href="/incidents" active={path.startsWith("/incidents") && !service} icon={Siren} label="Incidents" count={ws.open.length} />
        <NavLink href="/repositories" active={path.startsWith("/repositories")} icon={FolderGit2} label="Repositories" count={ws.repos.length} />
        <NavLink href="/graph" active={path.startsWith("/graph")} icon={Network} label="Live graph" />
        <NavLink href="/map" active={path.startsWith("/map")} icon={Workflow} label="Graph map" />
        <NavLink href="/hotspots" active={path.startsWith("/hotspots")} icon={Flame} label="Hotspots" />
        <NavLink href="/evaluation" active={path.startsWith("/evaluation")} icon={ChartColumn} label="Evaluation" />
        <NavLink href="/settings" active={path === "/settings"} icon={Settings2} label="Settings" />
      </nav>

      {ws.services.length > 0 && (
        <section aria-labelledby="side-services">
          <h2 id="side-services" className="px-2 pb-1 text-[12px] text-ink-3">
            Services
          </h2>
          <ul className="flex flex-col gap-px">
            {ws.services.map((s) => (
              <li key={s.name}>
                <Link
                  href={`/incidents?service=${encodeURIComponent(s.name)}`}
                  className={cn(
                    "flex h-[30px] items-center gap-2.5 rounded-md px-2 text-[13px] transition-colors hover:bg-hover",
                    service === s.name ? "bg-ink/[0.07] font-medium text-ink" : "text-ink-2 hover:text-ink",
                  )}
                >
                  <ServiceDot color={s.color} className="mx-[3px]" />
                  <span className="truncate">{s.name}</span>
                  {s.open > 0 ? (
                    <span className="num ml-auto text-[12px] text-red">{s.open} open</span>
                  ) : (
                    <span className="num ml-auto text-[12px] text-ink-4">{s.total}</span>
                  )}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {ws.repos.length > 0 && (
        <section aria-labelledby="side-repos" className="min-h-0 overflow-y-auto scrollbar-none">
          <h2 id="side-repos" className="px-2 pb-1 text-[12px] text-ink-3">
            Your repositories
          </h2>
          <ul className="flex flex-col gap-px">
            {ws.repos.map((r) => {
              const href = `/repositories/${r.name}`;
              const [owner, name] = r.name.split("/");
              return (
                <li key={r.name}>
                  <Link
                    href={href}
                    title={r.name}
                    className={cn(
                      "flex h-[30px] items-center gap-2.5 rounded-md px-2 text-[13px] transition-colors hover:bg-hover",
                      path === href ? "bg-ink/[0.07] font-medium text-ink" : "text-ink-2 hover:text-ink",
                    )}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element -- owner avatar from GitHub */}
                    <img src={avatarUrl(owner, 32)} alt="" className="size-4 rounded-[4px] bg-raised" />
                    <span className="truncate">{name}</span>
                    {r.open > 0 && <span className="num ml-auto text-[12px] text-red">{r.open}</span>}
                  </Link>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <div className="mt-auto">
        <SyncStatus at={syncedAt} failed={failed} />
      </div>
    </div>
  );
}

/* ───────── Shell ───────── */

/**
 * The signed-in app: a labeled sidebar on the dark frame and an inset work panel.
 * Pages render their content inside the panel, which scrolls with Lenis.
 */
export function AppShell({ me, children }: { me: User; children: ReactNode }) {
  const q = useIncidents();
  const path = usePathname();
  const [navOpen, setNavOpen] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const lenis = useLenis(scroller, content);
  const ws = useMemo(() => toWorkspace(q.data?.items ?? [], me.repos as Record<string, "viewer" | "approver">), [q.data, me.repos]);
  const ready = Boolean(q.data);

  // Each route starts at the top and plays a short entrance for its blocks.
  useLayoutEffect(() => {
    if (lenis.current) lenis.current.scrollTo(0, { immediate: true });
    else scroller.current?.scrollTo({ top: 0 });
    setNavOpen(false);
    const mm = gsap.matchMedia();
    mm.add("(prefers-reduced-motion: no-preference)", () => {
      gsap.fromTo("[data-enter]", { autoAlpha: 0, y: 6 }, { autoAlpha: 1, y: 0, duration: 0.4, ease: EASE, stagger: 0.035, clearProps: "transform,opacity,visibility" });
    });
    return () => mm.revert();
  }, [path, lenis, ready]);

  const ctx = useMemo(() => ({ me, ws, openNav: () => setNavOpen(true) }), [me, ws]);

  return (
    <WorkspaceContext value={ctx}>
      <div className="flex h-dvh bg-bg text-ink">
        <aside className="hidden w-[232px] shrink-0 md:block">
          <Sidebar me={me} ws={ws} syncedAt={q.dataUpdatedAt} failed={q.isError} />
        </aside>

        {/* Below md the sidebar slides over the panel. */}
        {navOpen && (
          <div className="fixed inset-0 z-40 md:hidden">
            <button type="button" aria-label="Close navigation" className="absolute inset-0 bg-black/50 animate-in fade-in" onClick={() => setNavOpen(false)} />
            <aside className="relative h-full w-[260px] border-r border-line bg-bg animate-in slide-in-from-left duration-200">
              <button type="button" aria-label="Close navigation" onClick={() => setNavOpen(false)} className="absolute right-2 top-3 grid size-7 place-items-center rounded-md text-ink-3 hover:bg-hover">
                <X size={15} />
              </button>
              <Sidebar me={me} ws={ws} syncedAt={q.dataUpdatedAt} failed={q.isError} onNavigate={() => setNavOpen(false)} />
            </aside>
          </div>
        )}

        <main className="flex min-w-0 flex-1 flex-col md:py-2 md:pr-2">
          {/* The sidebar is the navigation; below md it opens from this bar. */}
          <div className="flex h-11 shrink-0 items-center gap-2 border-b border-line bg-panel px-4 md:hidden">
            <NavButton />
            <Link href="/overview" aria-label="Kaizen home" className="text-accent">
              <LogoMark className="size-4" />
            </Link>
          </div>
          <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto overscroll-contain border-line bg-panel scrollbar-thin md:rounded-[10px] md:border">
            <div ref={content} className="flex min-h-full flex-col">
              {q.data ? children : q.isError ? <PanelError error={q.error} onRetry={() => q.refetch()} /> : <PanelSkeleton />}
            </div>
          </div>
        </main>
      </div>
    </WorkspaceContext>
  );
}

/** Opens the slide-over navigation on small screens. */
function NavButton() {
  const { openNav } = useWorkspace();
  return (
    <button type="button" aria-label="Open navigation" onClick={openNav} className="-ml-1 grid size-7 place-items-center rounded-md text-ink-2 hover:bg-hover md:hidden">
      <Menu size={16} />
    </button>
  );
}

function PanelSkeleton() {
  return (
    <div role="status" aria-label="Loading" className="flex flex-col">
      <div className="flex items-center gap-4 border-b border-line px-6 py-7">
        <Skeleton className="size-14 rounded-xl" />
        <div className="flex flex-col gap-2">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-3 w-64" />
        </div>
      </div>
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className="flex h-[42px] items-center gap-3 border-b border-line px-6">
          <Skeleton className="size-4 rounded-full" />
          <Skeleton className="h-3" style={{ width: `${30 + ((i * 17) % 40)}%` }} />
        </div>
      ))}
    </div>
  );
}

function PanelError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <div role="alert" className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
      <p className="text-[14px] font-medium">Could not load incidents</p>
      <p className="max-w-md text-[13px] text-ink-3">{error instanceof Error ? error.message : "Unknown error"}</p>
      <Button onClick={onRetry} className="mt-1">
        Try again
      </Button>
    </div>
  );
}
