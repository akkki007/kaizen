"use client";

import Link from "next/link";
import { useId, useLayoutEffect, useRef, type ComponentProps, type CSSProperties, type KeyboardEvent, type ReactNode } from "react";
import { gsap, haptic } from "@/lib/motion";
import { STATUS_META, isIncidentStatus, severityMeta, statusMeta, type IncidentStatus } from "@/lib/status";
import { cn } from "@/lib/utils";

/* ───────── Buttons ───────── */

const BUTTON = {
  primary: "bg-primary text-on-primary hover:opacity-90 shadow-[inset_0_-1px_0_rgb(0_0_0/0.15)]",
  secondary: "border border-line bg-panel-2 text-ink hover:bg-raised",
  ghost: "text-ink-2 hover:bg-hover hover:text-ink",
} as const;

export function buttonClass(variant: keyof typeof BUTTON = "secondary", className?: string) {
  return cn(
    "inline-flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-md px-2.5 text-[12.5px] font-medium whitespace-nowrap transition-[background-color,opacity,transform] duration-150 active:scale-[0.97] disabled:pointer-events-none disabled:opacity-45",
    BUTTON[variant],
    className,
  );
}

export function Button({ variant = "secondary", className, ...props }: ComponentProps<"button"> & { variant?: keyof typeof BUTTON }) {
  return <button type="button" className={buttonClass(variant, className)} {...props} />;
}

export function IconButton({ label, className, children, ...props }: ComponentProps<"button"> & { label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={cn("grid size-7 place-items-center rounded-md text-ink-3 transition-colors hover:bg-hover hover:text-ink", className)}
      {...props}
    >
      {children}
    </button>
  );
}

/* ───────── Labels ───────── */

const TAG = {
  neutral: "bg-ink/[0.08] text-ink-2",
  red: "bg-red/15 text-red",
  green: "bg-green/15 text-green",
  amber: "bg-amber/15 text-amber",
  accent: "bg-accent/15 text-link",
  outline: "border border-line text-ink-2",
} as const;

export type TagTone = keyof typeof TAG;

export function Tag({ tone = "neutral", className, children }: { tone?: TagTone; className?: string; children: ReactNode }) {
  return (
    <span className={cn("inline-flex h-[18px] shrink-0 items-center gap-1 rounded-full px-[7px] text-[11px] font-medium leading-none whitespace-nowrap", TAG[tone], className)}>
      {children}
    </span>
  );
}

export function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <kbd className={cn("inline-grid h-[18px] min-w-[18px] place-items-center rounded-[4px] border border-b-2 border-line px-1 font-mono text-[10.5px] text-ink-3", className)}>
      {children}
    </kbd>
  );
}

/** A service is shown as a colored dot and its name, like a GitHub label. */
export function ServiceDot({ color, className }: { color: string; className?: string }) {
  return <i className={cn("inline-block size-2 shrink-0 rounded-full", className)} style={{ background: color }} aria-hidden="true" />;
}

/* ───────── Incident status ───────── */

// Labels, tones and icons for every lifecycle status live in lib/status.ts.
export type StatusKind = IncidentStatus;

export const STATUS = STATUS_META;

export const statusOf = (s: string): StatusKind => (isIncidentStatus(s) ? s : "open");

export function StatusGlyph({ status, size = 15 }: { status: string; size?: number }) {
  const s = statusMeta(status);
  return <s.Icon size={size} strokeWidth={1.8} className={cn("shrink-0", s.text)} aria-label={s.label} role="img" />;
}

export function StatusBadge({ status }: { status: string }) {
  const s = statusMeta(status);
  return (
    <span title={s.description} className={cn("inline-flex h-[22px] items-center gap-1.5 rounded-full border px-2 text-[11.5px] font-medium", s.text)} style={{ borderColor: "color-mix(in srgb, currentColor 35%, transparent)" }}>
      <s.Icon size={12} strokeWidth={2} aria-hidden="true" />
      {s.label}
    </span>
  );
}

/** Severity as a small tag; nothing for incidents without one. */
export function SeverityTag({ severity, className }: { severity: string | null | undefined; className?: string }) {
  const m = severityMeta(severity);
  if (!m) return null;
  return (
    <Tag tone={m.tone} className={className}>
      <span title={m.description}>{m.label}</span>
    </Tag>
  );
}

/* ───────── Layout pieces ───────── */

/** Section header: title, muted count, and an optional link on the right. Hairline below. */
export function SectionHead({ title, count, extra, href, linkLabel, id }: { title: string; count?: number | string; extra?: ReactNode; href?: string; linkLabel?: string; id?: string }) {
  return (
    <div className="flex h-11 items-center gap-2 border-b border-line px-6">
      <h2 id={id} className="text-[13px] font-semibold text-ink">
        {title}
      </h2>
      {count !== undefined && <span className="num text-[12px] text-ink-3">{count}</span>}
      {extra}
      {href && (
        <Link href={href} className="ml-auto text-[12px] text-ink-3 transition-colors hover:text-ink">
          {linkLabel ?? "View all"} <span aria-hidden="true">↗</span>
        </Link>
      )}
    </div>
  );
}

export function EmptyLine({ children }: { children: ReactNode }) {
  return <p className="border-b border-line px-6 py-4 text-[13px] text-ink-3">{children}</p>;
}

export function Skeleton({ className, style }: { className?: string; style?: CSSProperties }) {
  return <div className={cn("animate-pulse rounded bg-ink/[0.06]", className)} style={style} />;
}

/** Monochrome segmented control in the top-bar tab style; keyboard arrows move the choice. */
export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly { id: T; label: ReactNode }[];
  value: T;
  onChange: (next: T) => void;
}) {
  const index = Math.max(0, options.findIndex((o) => o.id === value));
  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = (index + step + options.length) % options.length;
    haptic("tap");
    onChange(options[next].id);
    e.currentTarget.querySelectorAll<HTMLButtonElement>("[role=radio]")[next]?.focus();
  }
  return (
    <div role="radiogroup" aria-label={label} onKeyDown={onKeyDown} className="inline-flex items-center gap-0.5">
      {options.map((o) => {
        const checked = o.id === value;
        return (
          <button
            key={o.id}
            type="button"
            role="radio"
            aria-checked={checked}
            tabIndex={checked ? 0 : -1}
            onClick={() => {
              if (!checked) haptic("tap");
              onChange(o.id);
            }}
            className={cn(
              "inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-[12.5px] font-medium transition-colors",
              checked ? "border-line bg-raised text-ink" : "border-transparent text-ink-3 hover:text-ink",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** Area sparkline in one color, with the last point marked. */
export function Sparkline({ data, color = "var(--orange)", className }: { data: number[]; color?: string; className?: string }) {
  const id = useId().replace(/[^a-zA-Z0-9]/g, "");
  if (data.length < 2) return null;
  const w = 300;
  const h = 40;
  const max = Math.max(1, ...data);
  const pts = data.map((v, i) => [(i / (data.length - 1)) * w, h - 2 - (v / max) * (h - 6)] as const);
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  return (
    <svg data-spark viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className={cn("h-10 w-full overflow-visible", className)} aria-hidden="true">
      <defs>
        <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity="0.28" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${line} L${w} ${h} L0 ${h} Z`} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/**
 * A number that counts up to its value (and tweens between later values). The text is
 * written by the tween, not by React, so the two never fight over the same DOM node.
 */
export function CountUp({ value, decimals = 0, className }: { value: number; decimals?: number; className?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const shown = useRef(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const write = (v: number) => (el.textContent = v.toFixed(decimals));
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      shown.current = value;
      return void write(value);
    }
    const o = { v: shown.current };
    write(o.v);
    const tween = gsap.to(o, { v: value, duration: 0.9, ease: "power2.out", onUpdate: () => ((shown.current = o.v), write(o.v)) });
    return () => void tween.kill();
  }, [value, decimals]);
  return (
    <>
      <span ref={ref} aria-hidden="true" className={cn("num", className)} />
      <span className="sr-only">{value.toFixed(decimals)}</span>
    </>
  );
}
