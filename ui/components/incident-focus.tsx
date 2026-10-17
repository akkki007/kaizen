"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Graph selection shared by the whole incident page: the evidence graph, its inspector and
 * every citation (explanation claims, causal chain, feature evidence) read and set it.
 * `nonce` changes on every select so the graph re-centres even when the id is unchanged.
 */
type Focus = {
  selected: string | null;
  nonce: number;
  select: (id: string | null, opts?: { reveal?: boolean }) => void;
  /** Ids in the rendered graph (null until it loads): citations outside it are shown struck out. */
  citable: Set<string> | null;
  setCitable: (ids: Set<string> | null) => void;
};

const FocusContext = createContext<Focus | null>(null);

export function IncidentFocusProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ selected: string | null; nonce: number }>({ selected: null, nonce: 0 });
  const select = useCallback((id: string | null, opts?: { reveal?: boolean }) => {
    setState((s) => ({ selected: id, nonce: s.nonce + 1 }));
    if (id && opts?.reveal) document.getElementById("evidence-graph")?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, []);
  const [citable, setCitable] = useState<Set<string> | null>(null);
  const value = useMemo(() => ({ ...state, select, citable, setCitable }), [state, select, citable]);
  return <FocusContext.Provider value={value}>{children}</FocusContext.Provider>;
}

export function useIncidentFocus(): Focus {
  const ctx = useContext(FocusContext);
  // Outside the provider (e.g. a component rendered on its own) selection is local and inert.
  const [local, setLocal] = useState<string | null>(null);
  return ctx ?? { selected: local, nonce: 0, select: (id) => setLocal(id), citable: null, setCitable: () => {} };
}

/** Short text for a cited id: "Commit 9f2c1ab", "Function toCents", "includes" for an edge. */
export function citationText(id: string): string {
  if (id.includes("|")) {
    const [src, rel, dst] = id.split("|");
    return `${short(src)} ${rel} ${short(dst)}`;
  }
  return short(id);
}

function short(id: string): string {
  const [label, ...rest] = id.split(":");
  const key = rest.join(":");
  if (label === "Commit") return `${label} ${key.slice(0, 7)}`;
  if (label === "Function") return `${label} ${key.split(":").pop()}`;
  return `${label} ${key.length > 30 ? key.slice(0, 29) + "…" : key}`;
}

/** A cited node or edge id as a button: clicking it focuses and highlights it in the graph. */
export function Citation({ id, className }: { id: string; className?: string }) {
  const { selected, select, citable } = useIncidentFocus();
  const known = citable === null || citable.has(id);
  const active = selected === id;
  return (
    <button
      type="button"
      data-citation={id}
      aria-pressed={active}
      disabled={!known}
      title={known ? `Show ${id} in the evidence graph` : `${id} is not in the current evidence graph`}
      onClick={() => select(id, { reveal: true })}
      className={cn(
        "inline-flex h-[20px] max-w-full items-center truncate rounded-full border px-2 font-mono text-[11px] transition-colors",
        active ? "border-accent bg-accent/20 text-ink" : "border-accent/30 bg-accent/10 text-link hover:bg-accent/20",
        !known && "cursor-not-allowed border-line bg-transparent text-ink-4 line-through",
        className,
      )}
    >
      {citationText(id)}
    </button>
  );
}
