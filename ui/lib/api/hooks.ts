"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, unwrap } from "./client";

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => unwrap(await api.GET("/auth/me")),
    retry: false,
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
