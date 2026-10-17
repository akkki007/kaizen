"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ServiceDot, SectionHead, Tag } from "@/components/app/kit";
import { useWorkspace } from "@/components/app/shell";
import { workspaceTabs } from "@/components/app/tabs";
import { Topbar } from "@/components/app/topbar";
import { LogoMark } from "@/components/landing/logo";
import { useHotspots } from "@/lib/api/hooks-dashboard";
import { ago, serviceColor } from "@/lib/workspace";

function IncidentLinks({ ids }: { ids: string[] }) {
  return (
    <span className="flex flex-wrap gap-x-2 gap-y-0.5">
      {ids.slice(0, 6).map((id) => (
        <Link key={id} href={`/incidents/${encodeURIComponent(id)}`} className="font-mono text-[12px] text-link hover:underline">
          {id}
        </Link>
      ))}
      {ids.length > 6 && <span className="text-[12px] text-ink-3">+{ids.length - 6} more</span>}
    </span>
  );
}

/** Functions and dependencies most often blamed for incidents, per repository. */
export default function HotspotsPage() {
  const { ws } = useWorkspace();
  const router = useRouter();
  const params = useSearchParams();
  const repo = params.get("repo") ?? ws.repos[0]?.name;
  const q = useHotspots(repo);
  const now = Date.now();

  return (
    <div className="flex flex-1 flex-col">
      <Topbar
        crumbs={[{ label: <span className="inline-flex items-center gap-1.5"><LogoMark className="size-3.5 text-accent" />Kaizen</span>, href: "/overview" }, { label: "Hotspots" }]}
        tabs={workspaceTabs(ws, "hotspots")}
      />
      <header data-enter className="flex flex-wrap items-end gap-3 border-b border-line px-6 py-6">
        <div className="min-w-0 flex-1">
          <h1 className="text-[20px] font-semibold tracking-[-0.015em]">Hotspots</h1>
          <p className="mt-1 text-[13px] text-ink-2">Code and dependencies most often behind incidents: the likely or confirmed cause modified the function or changed the dependency.</p>
        </div>
        {ws.repos.length > 0 && (
          <label className="flex items-center gap-1.5 text-[12.5px] text-ink-3">
            Repository
            <select value={repo} onChange={(e) => router.replace(`/hotspots?repo=${encodeURIComponent(e.target.value)}`)} className="h-7 rounded-md border border-line bg-panel-2 px-1.5 text-ink">
              {ws.repos.map((r) => (
                <option key={r.name} value={r.name}>
                  {r.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </header>

      {!repo ? (
        <p className="px-6 py-6 text-[13px] text-ink-3">Connect a repository to see its hotspots.</p>
      ) : q.isError ? (
        <p role="alert" className="px-6 py-6 text-[13px] text-red">Could not load hotspots: {q.error.message}</p>
      ) : !q.data ? (
        <p role="status" className="px-6 py-6 text-[13px] text-ink-3">Loading hotspots</p>
      ) : (
        <>
          <section data-enter aria-labelledby="hs-fn">
            <SectionHead id="hs-fn" title="Functions" count={q.data.functions.length} />
            {q.data.functions.length === 0 ? (
              <p className="border-b border-line px-6 py-4 text-[13px] text-ink-3">No function has been blamed yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-left text-[13px]">
                  <caption className="sr-only">Functions most often blamed for incidents</caption>
                  <thead className="text-[12px] text-ink-3">
                    <tr className="border-b border-line">
                      <th scope="col" className="px-6 py-2 font-medium">Function</th>
                      <th scope="col" className="px-3 py-2 font-medium">Service</th>
                      <th scope="col" className="px-3 py-2 text-right font-medium">Incidents</th>
                      <th scope="col" className="px-3 py-2 text-right font-medium">Confirmed</th>
                      <th scope="col" className="px-3 py-2 font-medium">Last</th>
                      <th scope="col" className="px-6 py-2 font-medium">Incidents</th>
                    </tr>
                  </thead>
                  <tbody>
                    {q.data.functions.map((f) => (
                      <tr key={f.id} className="border-b border-line">
                        <td className="px-6 py-2">
                          <code className="font-mono text-ink" title={f.id}>{f.name}</code>
                          <span className="block truncate font-mono text-[11.5px] text-ink-3">{f.id.split(":").slice(1, -1).join(":")}</span>
                        </td>
                        <td className="px-3 py-2">
                          <span className="flex items-center gap-1.5 text-ink-2">
                            <ServiceDot color={serviceColor(f.service)} />
                            {f.service}
                          </span>
                        </td>
                        <td className="num px-3 py-2 text-right">{f.incidents}</td>
                        <td className="px-3 py-2 text-right">{f.confirmed > 0 ? <Tag tone="red">{f.confirmed} confirmed</Tag> : <span className="text-ink-4">0</span>}</td>
                        <td className="num px-3 py-2 text-ink-3">{f.last_onset ? `${ago(now - Date.parse(f.last_onset))} ago` : "–"}</td>
                        <td className="px-6 py-2">
                          <IncidentLinks ids={f.incident_ids} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          <section data-enter aria-labelledby="hs-dep" className="mt-6">
            <SectionHead id="hs-dep" title="Dependencies" count={q.data.dependencies.length} />
            {q.data.dependencies.length === 0 ? (
              <p className="border-b border-line px-6 py-4 text-[13px] text-ink-3">No dependency change has been blamed yet.</p>
            ) : (
              <ul aria-label="Dependencies most often blamed">
                {q.data.dependencies.map((d) => (
                  <li key={d.name} className="flex flex-wrap items-center gap-3 border-b border-line px-6 py-2 text-[13px]">
                    <code className="font-mono text-ink">{d.name}</code>
                    <span className="num text-ink-3">
                      {d.incidents} {d.incidents === 1 ? "incident" : "incidents"}
                    </span>
                    <span className="ml-auto">
                      <IncidentLinks ids={d.incident_ids} />
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}
