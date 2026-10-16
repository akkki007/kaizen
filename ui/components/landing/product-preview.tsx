import {
  Bell,
  ChevronDown,
  CircleCheck,
  Clock3,
  GitBranch,
  GitCommitHorizontal,
  LayoutGrid,
  MessageSquare,
  Plus,
  Search,
  Settings,
  Share2,
  Siren,
  Waypoints,
} from "lucide-react";
import { Logo } from "./logo";

const NAV = [
  { label: "Overview", icon: LayoutGrid },
  { label: "Incidents", icon: Siren, active: true },
  { label: "Repositories", icon: GitBranch },
  { label: "Evidence graph", icon: Waypoints },
  { label: "Settings", icon: Settings },
];

const SERVICES = [
  { name: "payments", color: "bg-green" },
  { name: "orders", color: "bg-accent" },
  { name: "gateway", color: "bg-amber" },
];

const STATUS = {
  investigating: "bg-amber/12 text-amber",
  open: "bg-red/12 text-red",
  resolved: "bg-green/12 text-green",
} as const;

const ROWS = [
  { title: "Elevated failures in orders", cause: "toCents · 9f2c1ab", comments: 4, links: 3, owner: "Maya Chen", status: "investigating" },
  { title: "Latency spike on POST /payments", cause: "validateAmount · 41b0e2c", comments: 7, links: 2, owner: "Leo Park", status: "open" },
  { title: "5xx after money-utils 1.4.0", cause: "money-utils 1.4.0", comments: 2, links: 5, owner: "Ines Duarte", status: "resolved" },
] as const;

/**
 * Static, decorative picture of the product for the landing page (not interactive, hidden from
 * assistive tech). Layout follows the hero design: sidebar, command bar, greeting, stats, table.
 */
export function ProductPreview() {
  return (
    <div
      aria-hidden="true"
      className="grid select-none grid-cols-1 overflow-hidden rounded-t-[10px] bg-panel-2 text-left text-ink shadow-[0_0_0_1px_var(--line),0_30px_80px_-20px_rgba(15,23,42,0.35)] md:grid-cols-[170px_1fr]"
    >
      <aside className="hidden border-r border-line bg-bg px-2.5 pt-5 md:block">
        <div className="px-1.5 pb-5">
          <Logo size="sm" />
        </div>
        <ul className="space-y-1 text-[11px] text-ink-2">
          {NAV.map(({ label, icon: Icon, active }) => (
            <li
              key={label}
              className={`flex items-center gap-2 rounded-[5px] px-2 py-[7px] ${active ? "bg-panel-2 text-ink font-medium shadow-[0_0_0_1px_var(--line)]" : ""}`}
            >
              <Icon className="size-3.5" strokeWidth={1.7} />
              {label}
            </li>
          ))}
        </ul>
        <div className="mt-6 flex items-center justify-between px-2 text-[10px] text-ink-3">
          Services <Plus className="size-3" />
        </div>
        <ul className="mt-2 space-y-2.5 px-2 text-[10.5px] text-ink-2">
          {SERVICES.map((s) => (
            <li key={s.name} className="flex items-center gap-2">
              <span className={`size-2 rounded-[2px] ${s.color}`} />
              {s.name}
            </li>
          ))}
        </ul>
      </aside>

      <div className="min-w-0">
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <div className="flex h-8 min-w-0 flex-1 items-center gap-2 rounded-[6px] border border-line bg-raised px-2.5 text-[10.5px] text-ink-3">
            <Search className="size-3" />
            <span className="truncate">Search incidents, commits or functions</span>
            <span className="ml-auto rounded border border-line bg-panel-2 px-1.5 py-0.5 font-mono text-[8.5px] text-ink-3">⌘K</span>
          </div>
          <div className="hidden h-8 items-stretch overflow-hidden rounded-[6px] bg-accent text-[10.5px] text-white sm:flex">
            <span className="flex items-center gap-1 px-3">
              <Plus className="size-3" /> Connect repository
            </span>
            <span className="flex items-center border-l border-white/25 px-1.5">
              <ChevronDown className="size-3" />
            </span>
          </div>
          <span className="relative flex size-8 items-center justify-center rounded-full border border-line">
            <Bell className="size-3.5" />
            <span className="absolute right-2 top-2 size-1.5 rounded-full bg-red" />
          </span>
          <span className="flex size-8 items-center justify-center rounded-full bg-ink text-[10px] font-medium text-panel">
            MC
          </span>
        </div>

        <div className="px-4 pb-5 pt-5">
          <p className="text-[9.5px] text-ink-3">Monday, 12th October</p>
          <div className="mt-1 flex items-center justify-between gap-3">
            <h3 className="text-[20px] font-medium tracking-[-0.02em]">Good evening, Maya</h3>
            <div className="hidden items-center gap-2 text-[10.5px] sm:flex">
              <span className="flex items-center gap-1 rounded-[6px] border border-line px-2.5 py-1.5">
                <Share2 className="size-3" /> Share
              </span>
              <span className="flex items-center gap-1 rounded-[6px] bg-ink px-2.5 py-1.5 text-panel">
                <Plus className="size-3" /> Investigate
              </span>
            </div>
          </div>

          <div className="mt-4 inline-flex flex-wrap items-center gap-x-4 gap-y-1 rounded-[6px] border border-line px-3 py-2 text-[10px] text-ink-3">
            <span className="flex items-center gap-1.5">
              <Clock3 className="size-3 text-ink-2" /> <b className="font-medium text-ink">3m</b> Mean time to detect
            </span>
            <span className="hidden h-3 w-px bg-line sm:block" />
            <span className="flex items-center gap-1.5">
              <CircleCheck className="size-3 text-ink-2" /> <b className="font-medium text-ink">12</b> Causes confirmed
            </span>
            <span className="hidden h-3 w-px bg-line sm:block" />
            <span className="flex items-center gap-1.5">
              <GitCommitHorizontal className="size-3 text-ink-2" /> <b className="font-medium text-ink">81</b> Commits mapped
            </span>
          </div>

          <div className="mt-4 overflow-hidden rounded-[8px] border border-line">
            <div className="flex items-center justify-between px-3.5 py-2.5 text-[11px]">
              <span className="font-medium">Recent incidents</span>
              <span className="flex items-center gap-3 text-[10px] text-ink-3">
                <span className="flex items-center gap-1 rounded-[5px] border border-line px-2 py-1 text-ink">
                  This week <ChevronDown className="size-3" />
                </span>
                See all
              </span>
            </div>
            <div className="grid grid-cols-[1.6fr_1fr_0.8fr] border-y border-line bg-raised px-3.5 py-2 text-[9.5px] text-ink-3 sm:grid-cols-[1.6fr_1fr_0.8fr_0.8fr]">
              <span>Incident</span>
              <span>Suspected cause</span>
              <span className="hidden sm:block">Owner</span>
              <span>Status</span>
            </div>
            {ROWS.map((r) => (
              <div
                key={r.title}
                className="grid grid-cols-[1.6fr_1fr_0.8fr] items-center border-b border-line px-3.5 py-3 text-[10.5px] last:border-b-0 sm:grid-cols-[1.6fr_1fr_0.8fr_0.8fr]"
              >
                <span className="flex min-w-0 items-center gap-2">
                  <Siren className="size-3 shrink-0 text-ink-3" />
                  <span className="truncate font-medium">{r.title}</span>
                  <span className="ml-auto hidden shrink-0 items-center gap-2 pr-3 text-[9px] text-ink-3 lg:flex">
                    <span className="flex items-center gap-0.5">
                      <MessageSquare className="size-2.5" /> {r.comments}
                    </span>
                    <span className="flex items-center gap-0.5">
                      <Waypoints className="size-2.5" /> {r.links}
                    </span>
                  </span>
                </span>
                <span className="truncate font-mono text-[9.5px] text-ink-2">{r.cause}</span>
                <span className="hidden sm:block">{r.owner}</span>
                <span>
                  <span className={`rounded-[4px] px-2 py-1 text-[9.5px] font-medium capitalize ${STATUS[r.status]}`}>{r.status}</span>
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
