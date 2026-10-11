"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

const KEY = "cd.repo";
type Ctx = { repo: string | null; setRepo: (repo: string) => void };
const RepoContext = createContext<Ctx>({ repo: null, setRepo: () => {} });

/** The repository the user is looking at (per-repo scoping is enforced by the API as well). */
export function RepoProvider({ children }: { children: ReactNode }) {
  const [repo, setRepoState] = useState<string | null>(null);
  useEffect(() => {
    try {
      setRepoState(localStorage.getItem(KEY));
    } catch {
      /* storage unavailable: selection just is not remembered */
    }
  }, []);
  const setRepo = useCallback((next: string) => {
    setRepoState(next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* ignore */
    }
  }, []);
  const value = useMemo(() => ({ repo, setRepo }), [repo, setRepo]);
  return <RepoContext.Provider value={value}>{children}</RepoContext.Provider>;
}

export const useRepo = () => useContext(RepoContext);
