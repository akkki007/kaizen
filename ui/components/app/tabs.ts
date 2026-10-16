import type { Workspace } from "@/lib/workspace";
import type { Tab } from "./topbar";

/** The workspace-level tabs shown in the top bar of the list pages. */
export function workspaceTabs(ws: Workspace, active: "overview" | "incidents" | "repositories"): Tab[] {
  return [
    { label: "Overview", href: "/overview", active: active === "overview" },
    { label: "Incidents", href: "/incidents", count: ws.incidents.length, active: active === "incidents" },
    { label: "Repositories", href: "/repositories", count: ws.repos.length, active: active === "repositories" },
  ];
}
