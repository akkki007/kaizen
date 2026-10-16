"use client";

import { ArrowDownUp, ArrowUpRight, Link2, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AskIncident, CauseSummary, IncidentFacts } from "@/components/app/incident-brief";
import { IconButton, Kbd, ServiceDot, StatusBadge, StatusGlyph, Tag, buttonClass } from "@/components/app/kit";
import { useWorkspace } from "@/components/app/shell";
import { workspaceTabs } from "@/components/app/tabs";
import { Topbar } from "@/components/app/topbar";
import { LogoMark } from "@/components/landing/logo";
import { EASE, gsap, haptic } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { ago, isOpen, serviceColor, type Incident } from "@/lib/workspace";

type StatusFilter = "open" | "all" | "resolved";

export default function IncidentsPage() {
  const { ws } = useWorkspace();
  const router = useRouter();
  const path = usePathname();
  const params = useSearchParams();
  const status = (params.get("status") as StatusFilter | null) ?? "all";
  const service = params.get("service");
  // Selection is local so rapid j/k presses never wait on the URL; the URL follows for deep links.
  const urlSelected = params.get("selected");
  const [selectedId, setSelectedId] = useState(urlSelected);
  const written = useRef(urlSelected);
  useEffect(() => {
    if (urlSelected !== written.current) setSelectedId((written.current = urlSelected));
  }, [urlSelected]);
  const [newestFirst, setNewestFirst] = useState(true);
  const list = useRef<HTMLUListElement>(null);

  const setParam = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(params.toString());
      for (const [k, v] of Object.entries(patch)) (v === null ? next.delete(k) : next.set(k, v));
      const qs = next.toString();
      router.replace(qs ? `${path}?${qs}` : path, { scroll: false });
    },
    [params, path, router],
  );

  const byService = service ? ws.incidents.filter((i) => i.service === service) : ws.incidents;
  const counts = { all: byService.length, open: byService.filter(isOpen).length, resolved: byService.filter((i) => !isOpen(i)).length };
  const items = useMemo(() => {
    const filtered = byService.filter((i) => (status === "open" ? isOpen(i) : status === "resolved" ? !isOpen(i) : true));
    return newestFirst ? filtered : [...filtered].reverse();
  }, [byService, status, newestFirst]);
  const selected = items.find((i) => i.id === selectedId) ?? null;

  const select = useCallback(
    (id: string | null) => {
      if (id !== selectedId) haptic("tap");
      setSelectedId(id);
      written.current = id;
      setParam({ selected: id });
    },
    [selectedId, setParam],
  );

  // ↑/↓ or j/k move through the list, Enter opens, Esc closes the preview.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, [contenteditable=true]") || e.metaKey || e.ctrlKey || e.altKey) return;
      const idx = items.findIndex((i) => i.id === selectedId);
      if (e.key === "ArrowDown" || e.key === "j") {
        e.preventDefault();
        const next = items[Math.min(items.length - 1, idx + 1)];
        if (next) select(next.id);
      } else if (e.key === "ArrowUp" || e.key === "k") {
        e.preventDefault();
        const prev = items[Math.max(0, idx - 1)];
        if (prev) select(prev.id);
      } else if (e.key === "Enter" && selected) {
        e.preventDefault();
        haptic("confirm");
        router.push(`/incidents/${encodeURIComponent(selected.id)}`);
      } else if (e.key === "Escape" && selected) {
        select(null);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, selectedId, selected, select, router]);

  // Keep the selected row in view while moving with the keyboard.
  useEffect(() => {
    list.current?.querySelector(`[data-id="${CSS.escape(selectedId ?? "")}"]`)?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);

  return (
    <div className="flex flex-1 flex-col">
      <Topbar
        crumbs={[{ label: <span className="inline-flex items-center gap-1.5"><LogoMark className="size-3.5 text-accent" />Kaizen</span>, href: "/overview" }, { label: service ? `Incidents in ${service}` : "Incidents" }]}
        tabs={workspaceTabs(ws, "incidents")}
      />

      {/* Filters */}
      <div data-enter className="sticky top-[46px] z-10 flex h-11 shrink-0 items-center gap-2 overflow-x-auto border-b border-line bg-panel/90 px-4 backdrop-blur-md scrollbar-none sm:px-6">
        <div role="radiogroup" aria-label="Status" className="flex items-center gap-0.5">
          {(["all", "open", "resolved"] as const).map((s) => (
            <button
              key={s}
              type="button"
              role="radio"
              aria-checked={status === s}
              onClick={() => {
                haptic("tap");
                setParam({ status: s === "all" ? null : s, selected: null });
              }}
              className={cn(
                "inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-[12.5px] capitalize transition-colors",
                status === s ? "border-line bg-raised font-medium text-ink" : "border-transparent text-ink-3 hover:text-ink",
              )}
            >
              {s}
              <span className="num text-ink-3">{counts[s]}</span>
            </button>
          ))}
        </div>
        <span className="mx-1 h-4 w-px shrink-0 bg-line" aria-hidden="true" />
        <ul className="flex items-center gap-0.5" aria-label="Filter by service">
          {ws.services.map((s) => {
            const on = service === s.name;
            return (
              <li key={s.name}>
                <button
                  type="button"
                  aria-pressed={on}
                  onClick={() => {
                    haptic("tap");
                    setParam({ service: on ? null : s.name, selected: null });
                  }}
                  className={cn(
                    "inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-[12.5px] whitespace-nowrap transition-colors",
                    on ? "border-line bg-raised text-ink" : "border-transparent text-ink-2 hover:text-ink",
                  )}
                >
                  <ServiceDot color={s.color} />
                  {s.name}
                  <span className="num text-ink-3">{s.total}</span>
                  {on && <X size={12} className="text-ink-3" />}
                </button>
              </li>
            );
          })}
        </ul>
        <button
          type="button"
          onClick={() => {
            haptic("tap");
            setNewestFirst((n) => !n);
          }}
          className={cn(buttonClass("ghost"), "ml-auto")}
        >
          <ArrowDownUp size={13} /> {newestFirst ? "Newest first" : "Oldest first"}
        </button>
      </div>

      <div className={cn("grid flex-1", selected && "xl:grid-cols-[minmax(0,1fr)_minmax(380px,440px)]")}>
        <div className="relative flex min-w-0 flex-col">
          {items.length === 0 ? (
            <div className="px-6 py-16 text-center">
              <p className="text-[14px] font-medium">No {status === "all" ? "" : `${status} `}incidents{service ? ` in ${service}` : ""}</p>
              <p className="mt-1 text-[13px] text-ink-3">When error rate or latency leaves its baseline, the incident shows up here within seconds.</p>
            </div>
          ) : (
            <ul ref={list} aria-label="Incidents" className="pb-4">
              {items.map((i) => (
                <IncidentRow key={i.id} incident={i} selected={i.id === selectedId} onSelect={() => select(i.id === selectedId ? null : i.id)} onOpen={() => router.push(`/incidents/${encodeURIComponent(i.id)}`)} />
              ))}
            </ul>
          )}
          {items.length > 0 && <HintBar selected={selected} />}
        </div>

        {selected && <Preview incident={selected} onClose={() => select(null)} />}
      </div>
    </div>
  );
}

function IncidentRow({ incident: i, selected, onSelect, onOpen }: { incident: Incident; selected: boolean; onSelect: () => void; onOpen: () => void }) {
  return (
    <li data-id={i.id} className={cn("relative border-b border-line transition-colors", selected ? "bg-sel" : "hover:bg-hover")}>
      {selected && <span aria-hidden="true" className="absolute inset-y-0 left-0 w-[2px] bg-accent" />}
      <button
        type="button"
        aria-pressed={selected}
        aria-label={`${i.id}: elevated failures in ${i.service}`}
        onClick={onSelect}
        onDoubleClick={onOpen}
        className="grid w-full grid-cols-[16px_minmax(0,1fr)_auto] items-start gap-3 px-4 py-2.5 text-left sm:px-6"
      >
        <span className="pt-[2px]">
          <StatusGlyph status={i.status} />
        </span>
        <span className="min-w-0">
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate text-[13px] font-medium text-ink">
              Elevated failures in <span className="font-mono text-[12.5px]">{i.service}</span>
            </span>
            <Tag tone={i.env === "production" ? "red" : "neutral"} className="hidden sm:inline-flex">
              {i.env}
            </Tag>
          </span>
          <span className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[12px] text-ink-3">
            <code className="font-mono">{i.id}</code>
            <span>·</span>
            <span className="truncate">{i.endpoint}</span>
            <span className="hidden sm:inline">·</span>
            <span className="hidden truncate sm:inline">{i.repo}</span>
          </span>
        </span>
        <span className="flex items-center gap-3 pt-[1px] text-[12px] text-ink-3">
          <span className="hidden items-center gap-1.5 md:flex">
            <ServiceDot color={serviceColor(i.service)} />
            {i.service}
          </span>
          <span className="num w-8 text-right">{ago(Date.now() - Date.parse(i.detected_at))}</span>
        </span>
      </button>
    </li>
  );
}

/** Floating shortcut bar, like a command hint: what is selected and how to act on it. */
function HintBar({ selected }: { selected: Incident | null }) {
  return (
    <div className="pointer-events-none sticky bottom-4 z-10 mt-auto flex justify-center px-4 pt-4">
      <div className="pointer-events-auto flex items-center gap-3 rounded-lg border border-line bg-raised/95 py-1 pl-3 pr-1 text-[12px] text-ink-3 shadow-pop backdrop-blur">
        {selected ? <code className="font-mono text-ink-2">{selected.id}</code> : <span>Select an incident</span>}
        <span className="hidden items-center gap-1 sm:flex">
          <Kbd>↑</Kbd>
          <Kbd>↓</Kbd> Navigate
        </span>
        {selected && (
          <span className="hidden items-center gap-1 sm:flex">
            <Kbd>Esc</Kbd> Close
          </span>
        )}
        {selected ? (
          <Link href={`/incidents/${encodeURIComponent(selected.id)}`} className={buttonClass("primary", "h-6 gap-1.5 px-2")}>
            Open <Kbd className="h-4 border-on-primary/30 text-on-primary/70">↵</Kbd>
          </Link>
        ) : (
          <span className="h-6" />
        )}
      </div>
    </div>
  );
}

function Preview({ incident, onClose }: { incident: Incident; onClose: () => void }) {
  const body = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState(false);
  const href = `/incidents/${encodeURIComponent(incident.id)}`;

  // Each new selection settles into place.
  useLayoutEffect(() => {
    const mm = gsap.matchMedia();
    mm.add("(prefers-reduced-motion: no-preference)", () => {
      gsap.fromTo(body.current, { autoAlpha: 0, y: 8 }, { autoAlpha: 1, y: 0, duration: 0.3, ease: EASE });
    });
    return () => mm.revert();
  }, [incident.id]);

  return (
    <aside
      aria-label={`Preview of ${incident.id}`}
      className="fixed inset-x-0 bottom-0 z-30 flex max-h-[85dvh] flex-col rounded-t-xl border-t border-line bg-panel shadow-pop xl:sticky xl:top-[90px] xl:z-0 xl:h-[calc(100dvh-16px-92px)] xl:max-h-none xl:rounded-none xl:border-l xl:border-t-0 xl:shadow-none"
      data-lenis-prevent
    >
      <div className="flex h-11 shrink-0 items-center gap-2 border-b border-line px-5 text-[12.5px] text-ink-3">
        <span className="truncate">{incident.repo.split("/").pop()}</span>
        <code className="font-mono">{incident.id}</code>
        <span className="ml-auto flex items-center">
          <IconButton
            label={copied ? "Link copied" : "Copy link"}
            onClick={() => {
              void navigator.clipboard?.writeText(`${window.location.origin}${href}`);
              haptic("confirm");
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1400);
            }}
          >
            <Link2 size={14} className={copied ? "text-green" : undefined} />
          </IconButton>
          <Link href={href} aria-label="Open investigation" title="Open investigation" className="grid size-7 place-items-center rounded-md text-ink-3 hover:bg-hover hover:text-ink">
            <ArrowUpRight size={14} />
          </Link>
          <IconButton label="Close preview" onClick={onClose}>
            <X size={14} />
          </IconButton>
        </span>
      </div>

      <div ref={body} className="min-h-0 flex-1 overflow-y-auto px-5 py-5 scrollbar-thin">
        <h2 className="text-[17px] font-semibold leading-[1.3] tracking-[-0.01em]">
          Elevated failures in <span className="font-mono text-[16px]">{incident.service}</span>
        </h2>
        <div className="mt-2.5 flex items-center gap-2 text-[12.5px] text-ink-3">
          <StatusBadge status={incident.status} />
          detected {ago(Date.now() - Date.parse(incident.detected_at))} ago
        </div>
        <div className="mt-5 border-y border-line py-4">
          <IncidentFacts incident={incident} />
        </div>
        <div className="mt-5">
          <CauseSummary incidentId={incident.id} />
        </div>
      </div>

      <div className="shrink-0 border-t border-line px-5 py-3">
        <AskIncident incidentId={incident.id} />
      </div>
    </aside>
  );
}
