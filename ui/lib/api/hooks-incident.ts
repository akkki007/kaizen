"use client";

// Incident page data (#93): metrics, similar incidents, history, node inspector, overlays,
// the live status stream and the remediation / incident actions.
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ApiError, MOCK, api, unwrap } from "./client";
import type { components } from "./schema";

export type IncidentMetrics = components["schemas"]["IncidentMetrics"];
export type SimilarIncident = components["schemas"]["SimilarIncidentView"];
export type NodeDetail = components["schemas"]["NodeDetail"];
export type Transition = components["schemas"]["IncidentTransitionOut"];
export type StatusEvent = { status: string; updated_at: string; remediation_status: string | null; investigation_status: string };
export type IncidentAction = "acknowledge" | "close" | "reopen";

const retry = (count: number, err: unknown) => !(err instanceof ApiError && err.status === 404) && count < 2;
const path = (incidentId: string) => ({ path: { incident_id: incidentId } });

/** Every query that shows incident state; refreshed together when the status changes. */
export const incidentKeys = (id: string) => [
  ["incident-detail", id],
  ["incident-history", id],
  ["incident-similar", id],
  ["incident-metrics", id],
  ["evidence-graph", id],
  ["evidence-overlay", id],
  ["incidents"],
];

export function useIncidentMetrics(incidentId: string, minutes = 120) {
  return useQuery({
    queryKey: ["incident-metrics", incidentId, minutes],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/incidents/{incident_id}/metrics", { params: { ...path(incidentId), query: { minutes } } })),
    retry,
  });
}

export function useSimilarIncidents(incidentId: string) {
  return useQuery({
    queryKey: ["incident-similar", incidentId],
    queryFn: async () => unwrap(await api.GET("/api/v1/incidents/{incident_id}/similar", { params: path(incidentId) })),
    retry,
  });
}

export function useIncidentHistory(incidentId: string) {
  return useQuery({
    queryKey: ["incident-history", incidentId],
    queryFn: async () => unwrap(await api.GET("/api/v1/incidents/{incident_id}/history", { params: path(incidentId) })),
    retry,
  });
}

export function useNodeDetail(incidentId: string, nodeId: string | null) {
  return useQuery({
    queryKey: ["incident-node", incidentId, nodeId],
    enabled: !!nodeId,
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/incidents/{incident_id}/nodes/{node_id}", {
          params: { path: { incident_id: incidentId, node_id: nodeId! } },
        }),
      ),
    retry,
    staleTime: 30_000,
  });
}

/** The evidence graph with one overlay (blast radius or history); only fetched when toggled on. */
export function useEvidenceOverlay(incidentId: string, overlay: "blast" | "history", enabled: boolean) {
  return useQuery({
    queryKey: ["evidence-overlay", incidentId, overlay],
    enabled,
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/incidents/{incident_id}/evidence-graph", { params: { ...path(incidentId), query: { overlay } } })),
    placeholderData: keepPreviousData,
    retry,
  });
}

function useInvalidateIncident(incidentId: string) {
  const qc = useQueryClient();
  return () => Promise.all(incidentKeys(incidentId).map((queryKey) => qc.invalidateQueries({ queryKey })));
}

export type ApproveResult = { id: string; status: string; approved_by: string; merge: string };

export function useApproveRemediation(incidentId: string) {
  const refresh = useInvalidateIncident(incidentId);
  return useMutation({
    mutationFn: async (remediationId: string) =>
      unwrap(await api.POST("/api/v1/remediations/{remediation_id}/approve", { params: { path: { remediation_id: remediationId } } })) as unknown as ApproveResult,
    onSuccess: () => refresh(),
  });
}

export function useRejectRemediation(incidentId: string) {
  const refresh = useInvalidateIncident(incidentId);
  return useMutation({
    mutationFn: async ({ remediationId, reason }: { remediationId: string; reason: string }) =>
      unwrap(
        await api.POST("/api/v1/remediations/{remediation_id}/reject", {
          params: { path: { remediation_id: remediationId } },
          body: { reason },
        }),
      ),
    onSuccess: () => refresh(),
  });
}

export function useIncidentAction(incidentId: string) {
  const refresh = useInvalidateIncident(incidentId);
  return useMutation({
    mutationFn: async ({ action, reason }: { action: IncidentAction; reason?: string }) =>
      unwrap(await api.POST("/api/v1/incidents/{incident_id}/actions", { params: path(incidentId), body: { action, reason: reason || null } })),
    onSuccess: () => refresh(),
  });
}

export function useRootCause(incidentId: string) {
  const refresh = useInvalidateIncident(incidentId);
  return useMutation({
    mutationFn: async (body: { sha: string; confirmed: boolean; reason?: string }) =>
      unwrap(await api.POST("/api/v1/incidents/{incident_id}/root-cause", { params: path(incidentId), body: { ...body, reason: body.reason || null } })),
    onSuccess: () => refresh(),
  });
}

export type LiveMode = "connecting" | "live" | "polling";

const TERMINAL = new Set(["resolved", "closed"]);

/**
 * Keeps the incident page current without a refresh: Server-Sent Events from /stream refresh
 * every incident query when the status, agent run or remediation changes. Without a stream
 * (mock mode, a proxy that buffers, an error) it falls back to polling the same queries.
 */
export function useLiveIncident(incidentId: string, status?: string) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<LiveMode>(MOCK || typeof EventSource === "undefined" ? "polling" : "connecting");
  const [last, setLast] = useState<StatusEvent | null>(null);

  useEffect(() => {
    if (MOCK || typeof EventSource === "undefined") return;
    const es = new EventSource(`/api/v1/incidents/${encodeURIComponent(incidentId)}/stream`, { withCredentials: true });
    let previous: string | null = null;
    es.addEventListener("status", (e) => {
      setMode("live");
      const data = (e as MessageEvent<string>).data;
      try {
        setLast(JSON.parse(data) as StatusEvent);
      } catch {
        return;
      }
      if (previous !== null && previous !== data) {
        for (const queryKey of incidentKeys(incidentId)) void qc.invalidateQueries({ queryKey });
      }
      previous = data;
    });
    es.onerror = () => setMode("polling"); // the browser keeps retrying; polling covers the gap
    return () => es.close();
  }, [incidentId, qc]);

  const interval = mode === "live" ? 30_000 : status && TERMINAL.has(status) ? 15_000 : 3_000;
  useEffect(() => {
    const t = window.setInterval(() => {
      for (const queryKey of [["incident-detail", incidentId], ["incident-history", incidentId]]) void qc.invalidateQueries({ queryKey });
    }, interval);
    return () => window.clearInterval(t);
  }, [incidentId, qc, interval]);

  return { mode, last };
}
