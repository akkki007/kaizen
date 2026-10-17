"use client";

// Dashboard data (#93): filtered incident search, service health, deployment timeline,
// hotspots and the latest evaluation result. Live views poll every 5 s so a new incident shows
// up within seconds of detection.
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api, unwrap } from "./client";
import type { components, operations } from "./schema";

export type IncidentSummary = components["schemas"]["IncidentSummary"];
export type ServiceHealth = components["schemas"]["ServiceHealth"];
export type DeploymentView = components["schemas"]["DeploymentView"];
export type Hotspots = components["schemas"]["Hotspots"];
export type EvalResult = components["schemas"]["EvalResult"];
export type EvalSummaryRow = components["schemas"]["EvalSummaryRow"];
export type EvalScenario = components["schemas"]["EvalScenario"];
type ListQuery = operations["list_incidents_api_v1_incidents_get"]["parameters"]["query"];

export const LIVE_POLL_MS = 5_000;

export type IncidentFilters = {
  status?: components["schemas"]["IncidentStatus"][];
  severity?: ("sev1" | "sev2" | "sev3")[];
  service?: string;
  repo?: string;
  /** ISO timestamps bounding the onset. */
  since?: string;
  until?: string;
  q?: string;
  limit?: number;
  offset?: number;
};

/** Drops empty values so the query key and the URL only carry real filters. */
export function cleanFilters(f: IncidentFilters): IncidentFilters {
  const out: IncidentFilters = {};
  if (f.status?.length) out.status = [...f.status].sort();
  if (f.severity?.length) out.severity = [...f.severity].sort();
  if (f.service) out.service = f.service;
  if (f.repo) out.repo = f.repo;
  if (f.since) out.since = f.since;
  if (f.until) out.until = f.until;
  if (f.q?.trim()) out.q = f.q.trim();
  if (f.limit) out.limit = f.limit;
  if (f.offset) out.offset = f.offset;
  return out;
}

/** Server-side filtered incident list (status incl. the archive, severity, service, date, text). */
export function useIncidentSearch(filters: IncidentFilters) {
  const query = cleanFilters({ limit: 200, ...filters });
  return useQuery({
    queryKey: ["incidents", "search", query],
    queryFn: async () => unwrap(await api.GET("/api/v1/incidents", { params: { query: query as ListQuery } })),
    placeholderData: keepPreviousData,
    refetchInterval: LIVE_POLL_MS,
  });
}

export function useServicesHealth(repo?: string) {
  return useQuery({
    queryKey: ["services-health", repo ?? null],
    queryFn: async () => unwrap(await api.GET("/api/v1/services/health", { params: { query: repo ? { repo } : {} } })),
    placeholderData: keepPreviousData,
    refetchInterval: LIVE_POLL_MS,
  });
}

function split(repo: string) {
  const [owner, name] = repo.split("/");
  return { owner, name };
}

export function useDeployments(repo: string | undefined, hours = 72) {
  return useQuery({
    queryKey: ["deployments", repo ?? null, hours],
    enabled: Boolean(repo),
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/repos/{owner}/{name}/deployments", { params: { path: split(repo!), query: { hours } } })),
    placeholderData: keepPreviousData,
    refetchInterval: LIVE_POLL_MS,
  });
}

export function useHotspots(repo: string | undefined) {
  return useQuery({
    queryKey: ["hotspots", repo ?? null],
    enabled: Boolean(repo),
    queryFn: async () => unwrap(await api.GET("/api/v1/repos/{owner}/{name}/hotspots", { params: { path: split(repo!) } })),
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  });
}

export function useEvalLatest() {
  return useQuery({
    queryKey: ["eval-latest"],
    queryFn: async () => unwrap(await api.GET("/api/v1/eval/latest")),
    staleTime: 60_000,
  });
}
