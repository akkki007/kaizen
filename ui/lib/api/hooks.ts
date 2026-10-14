"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, MOCK, api, redirectToLogin, unwrap } from "./client";

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => unwrap(await api.GET("/auth/me")),
    retry: false,
  });
}

export function useSignOut() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => unwrap(await api.POST("/auth/logout")),
    onSettled: () => {
      qc.clear();
      // Mock mode has no real session; mock /auth/me always succeeds, so just walk to /login.
      window.location.assign("/login");
    },
  });
}

export function useInstallUrl() {
  return useQuery({
    queryKey: ["install-url"],
    queryFn: async () => unwrap(await api.GET("/auth/install-url")),
    staleTime: Infinity,
  });
}

export function useIncidents() {
  return useQuery({
    queryKey: ["incidents"],
    queryFn: async () => unwrap(await api.GET("/api/v1/incidents", { params: { query: { limit: 100 } } })),
    refetchInterval: 5_000, // incidents show up within seconds of detection (issue #93)
  });
}

export function useEvidenceGraph(incidentId: string, expand?: string) {
  return useQuery({
    queryKey: ["evidence-graph", incidentId, expand ?? null],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/incidents/{incident_id}/evidence-graph", {
          params: { path: { incident_id: incidentId }, query: expand ? { expand } : {} },
        }),
      ),
    placeholderData: keepPreviousData,
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 2,
  });
}

export type RankMethod = "graph" | "b0" | "b1";

/** Incident, deterministic ranking (with the Cypher behind each feature) and the agent trace. */
export function useIncidentDetail(incidentId: string) {
  return useQuery({
    queryKey: ["incident-detail", incidentId],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/incidents/{incident_id}", { params: { path: { incident_id: incidentId } } })),
    refetchInterval: (q) => (q.state.data?.investigation.status === "not_run" ? 4_000 : false),
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 2,
  });
}

/** Baseline rankings (no graph traversal) for the "graph off" comparison. */
export function useBaseline(incidentId: string, method: Exclude<RankMethod, "graph">, enabled: boolean) {
  return useQuery({
    queryKey: ["rank", incidentId, method],
    enabled,
    queryFn: async () =>
      unwrap(
        await api.POST("/api/v1/incidents/{incident_id}/rank", {
          params: { path: { incident_id: incidentId }, query: { method } },
        }),
      ),
  });
}

export function useLlmCost(incidentId: string) {
  return useQuery({
    queryKey: ["llm-cost", incidentId],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/incidents/{incident_id}/llm-cost", { params: { path: { incident_id: incidentId } } })),
  });
}

type Key = { id: number; label: string; created_at: string; revoked_at: string | null };

function split(repo: string) {
  const [owner, name] = repo.split("/");
  return { owner, name };
}

export function useApiKeys(repo: string) {
  return useQuery({
    queryKey: ["api-keys", repo],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/repos/{owner}/{name}/api-keys", { params: { path: split(repo) } })) as unknown as Key[],
  });
}

export function useCreateKey(repo: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (label: string) =>
      unwrap(await api.POST("/api/v1/repos/{owner}/{name}/api-keys", { params: { path: split(repo), query: { label } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api-keys", repo] }),
  });
}

export function useRevokeKey(repo: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (keyId: number) =>
      unwrap(await api.DELETE("/api/v1/repos/{owner}/{name}/api-keys/{key_id}", { params: { path: { ...split(repo), key_id: keyId } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api-keys", repo] }),
  });
}

export type BackfillJob = {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "dead" | string;
  attempts: number;
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type BackfillStatus = {
  repo: string;
  installed: boolean;
  backfilled_at: string | null;
  job: BackfillJob | null;
};

/** Import state for a repository; polls while a backfill is in flight. */
export function useBackfillStatus(repo: string) {
  return useQuery({
    queryKey: ["backfill", repo],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/repos/{owner}/{name}/backfill", { params: { path: split(repo) } })) as unknown as BackfillStatus,
    refetchInterval: (query) =>
      query.state.data?.job && ["queued", "running"].includes(query.state.data.job.status)
        ? 3_000
        : false,
  });
}

export function useReimport(repo: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/v1/repos/{owner}/{name}/backfill", { params: { path: split(repo) } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["backfill", repo] }),
    onError: (err) => {
      if (err instanceof ApiError && err.status === 401) redirectToLogin();
    },
  });
}
