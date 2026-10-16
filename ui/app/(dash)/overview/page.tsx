"use client";

import { ArrowUpRight, Clock3, FolderGit2, ShieldCheck, Siren } from "lucide-react";
import Link from "next/link";
import { useLayoutEffect, useRef, type CSSProperties } from "react";
import { CountUp, EmptyLine, SectionHead, ServiceDot, Sparkline, StatusGlyph, Tag, buttonClass } from "@/components/app/kit";
import { avatarUrl, useWorkspace } from "@/components/app/shell";
import { workspaceTabs } from "@/components/app/tabs";
import { Topbar } from "@/components/app/topbar";
import { LogoMark } from "@/components/landing/logo";
import { useInstallUrl } from "@/lib/api/hooks";
import { EASE, gsap } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { ago, serviceColor, type Incident } from "@/lib/workspace";

const WEEKDAY = (offset: number) => new Date(Date.now() - offset * 86_400_000).toLocaleDateString("en", { weekday: "short" });

export default function OverviewPage() {
  const { me, ws } = useWorkspace();
  const install = useInstallUrl();
  const root = useRef<HTMLDivElement>(null);
  const now = Date.now();
  const approverOn = ws.repos.filter((r) => r.role === "approver").length;
  const last = ws.incidents[0];
  const thisWeek = ws.week.reduce((a, b) => a + b, 0);
  const thisMonth = ws.month.reduce((a, b) => a + b, 0);
  const mttd = ws.mttdMinutes;

  // Detection blocks rise and the sparkline wipes in, once per visit.
  useLayoutEffect(() => {
    const mm = gsap.matchMedia();
    mm.add("(prefers-reduced-motion: no-preference)", () => {
      gsap.fromTo("[data-block]", { scaleY: 0, transformOrigin: "50% 100%" }, { scaleY: 1, duration: 0.5, ease: EASE, stagger: 0.04, delay: 0.1 });
      gsap.fromTo("[data-spark]", { clipPath: "inset(0 100% 0 0)" }, { clipPath: "inset(0 0% 0 0)", duration: 1.1, ease: "power2.inOut", delay: 0.15 });
      gsap.fromTo("[data-share]", { scaleX: 0 }, { scaleX: 1, duration: 0.7, ease: EASE, stagger: 0.05, delay: 0.2 });
    }, root);
    return () => mm.revert();
  }, []);

  return (
    <div ref={root} className="flex flex-1 flex-col">
      <Topbar
        crumbs={[{ label: <span className="inline-flex items-center gap-1.5"><LogoMark className="size-3.5 text-accent" />Kaizen</span>, href: "/overview" }, { label: "Overview" }]}
        tabs={workspaceTabs(ws, "overview")}
        actions={
          install.data?.url && (
            <a href={install.data.url} target="_blank" rel="noreferrer" className={buttonClass("secondary")}>
              Connect repository <ArrowUpRight size={13} />
            </a>
          )
        }
      />

      {/* Header */}
      <section data-enter className="flex flex-col gap-5 border-b border-line px-6 py-7 sm:flex-row sm:items-start">
        {/* eslint-disable-next-line @next/next/no-img-element -- GitHub avatar */}
        <img src={avatarUrl(me.login, 112)} alt="" className="size-14 shrink-0 rounded-xl border border-line bg-raised" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="text-[24px] font-semibold tracking-[-0.02em]">{me.login}</h1>
            <Tag tone="outline">Workspace</Tag>
          </div>
          <p className="mt-1 text-[14px] text-ink-2">Production failures traced to the changes behind them.</p>
          <ul className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[12.5px] text-ink-2">
            <li className="flex items-center gap-1.5">
              <Siren size={13} className="text-ink-3" /> {ws.services.length} {ws.services.length === 1 ? "service" : "services"}
            </li>
            <li className="flex items-center gap-1.5">
              <FolderGit2 size={13} className="text-ink-3" /> {ws.repos.length} {ws.repos.length === 1 ? "repository" : "repositories"}
            </li>
            <li className="flex items-center gap-1.5">
              <ShieldCheck size={13} className="text-ink-3" /> Approver on {approverOn}
            </li>
            {last && (
              <li className="flex items-center gap-1.5">
                <Clock3 size={13} className="text-ink-3" /> Last incident {ago(now - Date.parse(last.detected_at))} ago
              </li>
            )}
          </ul>
        </div>
        <Link href="/incidents?status=open" className="inline-flex h-7 shrink-0 self-start items-stretch overflow-hidden rounded-md border border-line text-[12.5px] font-medium transition-colors hover:bg-hover">
          <span className="flex items-center gap-1.5 px-2.5">
            <Siren size={13} className={ws.open.length ? "text-red" : "text-ink-3"} /> Open
          </span>
          <span className="num flex items-center border-l border-line px-2.5 text-ink-2">{ws.open.length}</span>
        </Link>
      </section>

      {/* Stat strip */}
      <section data-enter aria-label="Key metrics" className="grid grid-cols-2 border-b border-line lg:grid-cols-[1fr_1fr_1fr_1.6fr] [&>*]:border-line [&>*]:px-6 [&>*]:py-5 [&>*:not(:last-child)]:border-r max-lg:[&>*:nth-child(2)]:border-r-0 max-lg:[&>*:nth-child(-n+2)]:border-b">
        <div>
          <p className="text-[12.5px] text-ink-3">Open incidents</p>
          <p className={cn("mt-2 text-[26px] font-medium leading-none", ws.open.length ? "text-ink" : "text-ink-2")}>
            <CountUp value={ws.open.length} />
          </p>
        </div>
        <div>
          <p className="text-[12.5px] text-ink-3">Detection, last 7 days</p>
          <div className="mt-2.5 flex items-center gap-2.5">
            <div className="flex h-[18px] items-end gap-[3px]" role="img" aria-label={`${thisWeek} incidents detected this week`}>
              {ws.week.map((n, i) => (
                <span
                  key={i}
                  data-block
                  title={`${WEEKDAY(6 - i)}: ${n} ${n === 1 ? "incident" : "incidents"}`}
                  className={cn("h-full w-[7px] rounded-[2px]", n ? "bg-red" : "bg-green")}
                />
              ))}
            </div>
            <span className={cn("text-[13px] font-medium", thisWeek ? "text-red" : "text-green")}>
              {thisWeek ? `${thisWeek} detected` : "Quiet"}
            </span>
          </div>
        </div>
        <div className="max-lg:col-span-2 max-lg:border-b max-lg:!border-r-0">
          <p className="text-[12.5px] text-ink-3">Mean time to detect</p>
          <p className="mt-2 flex items-baseline gap-1.5">
            {mttd === null ? (
              <span className="text-[26px] font-medium leading-none text-ink-3">–</span>
            ) : (
              <>
                <span className="font-mono text-[26px] font-medium leading-none">
                  <CountUp value={Number(mttd.toFixed(1))} decimals={1} />
                </span>
                <span className="text-[12.5px] text-ink-3">min, 30 days</span>
              </>
            )}
          </p>
        </div>
        <div className="col-span-2 lg:col-span-1">
          <div className="flex items-center justify-between">
            <p className="text-[12.5px] text-ink-3">Incidents, last 30 days</p>
            <p className="text-[12.5px] font-medium text-orange">{thisMonth} this month</p>
          </div>
          <Sparkline data={ws.month} className="mt-2" />
        </div>
      </section>

      {/* Body */}
      <div className="grid flex-1 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 lg:border-r lg:border-line">
          <section data-enter aria-labelledby="ov-open">
            <SectionHead id="ov-open" title="Open incidents" count={ws.open.length} href="/incidents?status=open" linkLabel="All open" />
            {ws.open.length ? <IncidentRows items={ws.open.slice(0, 5)} now={now} /> : <EmptyLine>No open incidents.</EmptyLine>}
          </section>
          <section data-enter aria-labelledby="ov-recent" className="mt-6">
            <SectionHead id="ov-recent" title="Recent incidents" extra={<code className="font-mono text-[12px] text-ink-3">all services</code>} href="/incidents" linkLabel="History" />
            {ws.incidents.length ? (
              <IncidentRows items={ws.incidents.slice(0, 8)} now={now} />
            ) : (
              <EmptyLine>No incidents yet. They appear here within seconds of detection.</EmptyLine>
            )}
          </section>
        </div>

        <aside data-enter className="flex flex-col gap-7 border-t border-line px-6 py-6 lg:border-t-0">
          <section aria-labelledby="ov-about">
            <h2 id="ov-about" className="text-[13px] font-semibold">About</h2>
            <p className="mt-2 text-[13px] leading-[1.55] text-ink-2">
              Kaizen links each incident to the deployment, commit and function behind it, and checks a fix before anyone approves it.
            </p>
            <div className="mt-3 flex flex-wrap gap-1.5">
              <Tag tone="accent">root cause</Tag>
              <Tag tone="accent">evidence graph</Tag>
              <Tag tone="accent">verified revert</Tag>
            </div>
          </section>

          {ws.services.length > 0 && <ServiceShare />}

          <section aria-labelledby="ov-repos">
            <h2 id="ov-repos" className="text-[13px] font-semibold">
              Repositories <span className="num font-normal text-ink-3">{ws.repos.length}</span>
            </h2>
            <ul className="mt-2.5 flex flex-col gap-2">
              {ws.repos.map((r) => (
                <li key={r.name}>
                  <Link href={`/repositories/${r.name}`} className="group flex items-center gap-2 text-[13px]">
                    {/* eslint-disable-next-line @next/next/no-img-element -- owner avatar */}
                    <img src={avatarUrl(r.name.split("/")[0], 32)} alt="" className="size-4 rounded-[4px] bg-raised" />
                    <span className="truncate text-ink-2 transition-colors group-hover:text-ink">{r.name}</span>
                    <Tag tone={r.role === "approver" ? "green" : "neutral"} className="ml-auto">
                      {r.role}
                    </Tag>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        </aside>
      </div>
    </div>
  );
}

/** Share of incidents per service, as a stacked bar with a legend (like a languages bar). */
function ServiceShare() {
  const { ws } = useWorkspace();
  const total = ws.services.reduce((a, s) => a + s.total, 0) || 1;
  return (
    <section aria-labelledby="ov-services">
      <h2 id="ov-services" className="text-[13px] font-semibold">
        Services
      </h2>
      <div className="mt-3 flex h-2 gap-[2px] overflow-hidden rounded-full" role="img" aria-label="Share of incidents per service">
        {ws.services.map((s) => (
          <span key={s.name} data-share className="h-full origin-left" style={{ flexGrow: s.total, background: s.color } as CSSProperties} />
        ))}
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-[12.5px]">
        {ws.services.map((s) => (
          <li key={s.name}>
            <Link href={`/incidents?service=${encodeURIComponent(s.name)}`} className="flex items-center gap-1.5 text-ink-2 hover:text-ink">
              <ServiceDot color={s.color} />
              {s.name}
              <span className="num text-ink-3">{Math.round((s.total / total) * 100)}%</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** One line per incident: status, id, what failed, service, age. Opens the incident. */
function IncidentRows({ items, now }: { items: Incident[]; now: number }) {
  return (
    <ul>
      {items.map((i) => (
        <li key={i.id}>
          <Link
            href={`/incidents/${encodeURIComponent(i.id)}`}
            className="group grid h-[42px] grid-cols-[16px_64px_minmax(0,1fr)_auto] items-center gap-3 border-b border-line px-6 text-[13px] transition-colors hover:bg-hover sm:grid-cols-[16px_64px_minmax(0,1fr)_150px_44px]"
          >
            <StatusGlyph status={i.status} />
            <code className="truncate font-mono text-[12px] text-ink-3">{i.id}</code>
            <span className="truncate text-ink">
              Elevated failures in <span className="font-mono text-[12.5px]">{i.service}</span>
              <span className="ml-2 text-ink-3">{i.endpoint}</span>
            </span>
            <span className="hidden items-center gap-2 truncate text-ink-2 sm:flex">
              <ServiceDot color={serviceColor(i.service)} />
              <span className="truncate">{i.repo.split("/").pop()}</span>
            </span>
            <span className="num text-right text-[12px] text-ink-3">{ago(now - Date.parse(i.detected_at))}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
