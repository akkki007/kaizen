"use client";

import { BarChart3, GitBranch, LayoutDashboard, Moon, Siren, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import type { ReactNode } from "react";
import { AuthGate } from "@/components/auth-gate";
import { Button } from "@/components/ui/button";
import { useRepo } from "@/components/repo-context";
import type { components } from "@/lib/api/schema";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/incidents", label: "Incidents", icon: Siren },
  { href: "/dashboard", label: "Analytics", icon: BarChart3 },
] as const;

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label="Toggle theme"
      onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
    >
      <Sun className="size-4 dark:hidden" />
      <Moon className="hidden size-4 dark:block" />
    </Button>
  );
}

function RepoSwitcher({ repos }: { repos: string[] }) {
  const { repo, setRepo } = useRepo();
  const current = repo && repos.includes(repo) ? repo : repos[0];
  if (repos.length === 0) return <span className="text-sm text-muted-foreground">No repositories</span>;
  return (
    <label className="flex items-center gap-2 text-sm">
      <GitBranch className="size-4 text-muted-foreground" aria-hidden />
      <span className="sr-only">Repository</span>
      <select
        className="rounded-md border bg-card px-2 py-1"
        value={current}
        onChange={(e) => setRepo(e.target.value)}
      >
        {repos.map((r) => (
          <option key={r}>{r}</option>
        ))}
      </select>
    </label>
  );
}

/** Protected shell: redirects to /login without a session, shows nav, repo switcher and theme. */
export function AppShell({ children }: { children: ReactNode }) {
  const path = usePathname();
  return <AuthGate>{(me) => <Shell me={me} path={path}>{children}</Shell>}</AuthGate>;
}

function Shell({ me, path, children }: { me: components["schemas"]["User"]; path: string; children: ReactNode }) {
  const repos = Object.keys(me.repos);
  return (
    <div className="flex min-h-dvh">
      <aside className="hidden w-56 shrink-0 flex-col gap-1 border-r bg-card p-4 md:flex">
        <div className="mb-4 text-lg font-semibold">CausalDeploy</div>
        <nav aria-label="Main" className="flex flex-col gap-1">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-2 rounded-md px-3 py-2 text-sm",
                  active ? "bg-muted font-medium" : "text-muted-foreground hover:bg-muted",
                )}
              >
                <Icon className="size-4" aria-hidden />
                {label}
              </Link>
            );
          })}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-4 border-b bg-card px-6 py-3">
          <RepoSwitcher repos={repos} />
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">{me.login}</span>
            <ThemeToggle />
          </div>
        </header>
        <main className="flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}
