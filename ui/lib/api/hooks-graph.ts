"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "./client";

const split = (repo: string) => {
  const [owner, name] = repo.split("/");
  return { owner, name };
};

/** The repository's code graph (services, functions, calls, recent commits) with failing/suspect state. */
export function useCodeGraph(repo: string | undefined, fast: boolean) {
  return useQuery({
    queryKey: ["code-graph", repo],
    enabled: !!repo,
    queryFn: async () => unwrap(await api.GET("/api/v1/repos/{owner}/{name}/code-graph", { params: { path: split(repo!) } })),
    refetchInterval: fast ? 2_000 : 15_000,
  });
}

/** Demo only: simulate a bad deploy; the real detector, graph queries and alerts then run. */
export function useInjectProblem(repo: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/repos/{owner}/{name}/demo/inject", { params: { path: split(repo) } })),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["code-graph", repo] });
      qc.invalidateQueries({ queryKey: ["incidents"] });
    },
  });
}

export function useResetDemo(repo: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/repos/{owner}/{name}/demo/reset", { params: { path: split(repo) } })),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["code-graph", repo] });
      qc.invalidateQueries({ queryKey: ["incidents"] });
    },
  });
}
