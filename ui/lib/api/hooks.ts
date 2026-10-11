"use client";

import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "./client";

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
