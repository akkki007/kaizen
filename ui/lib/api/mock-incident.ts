// Mock API for the incident page (#93), used by mock.ts in NEXT_PUBLIC_API_MOCK=1 mode.
// Every incident is generated from one scenario, so the evidence graph, the explanation's
// citations, the ranking's evidence ids, the node inspector and the overlays all agree.
//   inc-1  S1, resolved: money-utils 2.0 made toCents return floats; reverted (PR #9), merged
//   inc-2  payments, fix verified in staging and waiting for an approver
//   inc-3  orders, live: walks detect -> investigate -> explain -> fix -> verify while you watch
//   inc-4  inventory, ambiguous ranking: the agent abstains and lists the top 3
//   inc-8  S8 recurrence in checkout: resembles inc-1, reuses its fix
import { mockIncidents } from "./mock";
import type { components } from "./schema";

type S = components["schemas"];
type Graph = S["EvidenceGraph"];
type GNode = S["EvidenceNode"];
type GEdge = S["EvidenceEdge"];
type Candidate = S["RankedCandidate"];
type Remediation = S["RemediationView"];
type Transition = S["IncidentTransitionOut"];

const T0 = Date.now();
const iso = (ms: number) => new Date(ms).toISOString();
const minAgo = (m: number) => T0 - m * 60_000;
const REPO = "yashchandnani07/shoplite";
const PR = (n: number) => `https://github.com/${REPO}/pull/${n}`;
const RUN = (n: number) => `https://github.com/${REPO}/actions/runs/${n}`;

type Suspect = { sha: string; message: string; author: string; kind?: "commit" | "config" | "dependency"; nodeId?: string };

type Scenario = {
  id: string;
  service: string;
  endpoint: string;
  onsetMin: number; // minutes ago
  deployment: string;
  handler: string; // fn in the service that handles the endpoint
  caller: string; // fn between the handler and toCents
  culprit: Suspect;
  others: Suspect[];
  verdict: "confident" | "ambiguous";
  margin: number;
  error: { fp: string; type: string; message: string; count: number };
  fixPr: number;
  similar: { id: string; similarity: number; basis: string[] }[];
  explanationSource: "model" | "template";
};

const TO_CENTS = "Function:money-utils:lib/index.js:toCents";

const SCENARIOS: Record<string, Scenario> = {
  "inc-1": {
    id: "inc-1", service: "payments", endpoint: "POST /payments", onsetMin: 2900, deployment: "d-38",
    handler: "handlePayment", caller: "createCharge",
    culprit: { sha: "3e7b210", message: "Upgrade money-utils to 2.0 (toCents returns float)", author: "bruno" },
    others: [{ sha: "77ad0c3", message: "docs: payments README", author: "alice" }],
    verdict: "confident", margin: 0.61,
    error: { fp: "fp-4a2", type: "RangeError", message: "amount must be an integer number of cents", count: 288 },
    fixPr: 9, similar: [], explanationSource: "model",
  },
  "inc-2": {
    id: "inc-2", service: "payments", endpoint: "POST /payments", onsetMin: 190, deployment: "d-41",
    handler: "handlePayment", caller: "createCharge",
    culprit: { sha: "9f2c1ab", message: "Round amounts in toCents", author: "bruno" },
    others: [
      { sha: "4be0d77", message: "docs: update README", author: "alice" },
      { sha: "a11c9e0", message: "chore: bump express", author: "dana" },
    ],
    verdict: "confident", margin: 0.57,
    error: { fp: "fp-7c1", type: "RangeError", message: "amount must be an integer number of cents", count: 312 },
    fixPr: 14,
    similar: [{ id: "inc-1", similarity: 0.64, basis: ["error_family:rangeerror:amount must be an integer number of cents", "thrown_in:money-utils:lib/index.js:toCents", "service:payments"] }],
    explanationSource: "model",
  },
  "inc-3": {
    id: "inc-3", service: "orders", endpoint: "POST /orders", onsetMin: 14, deployment: "d-44",
    handler: "createOrder", caller: "orderTotal",
    culprit: { sha: "e4a91f0", message: "orders: compute totals with toCents", author: "chen" },
    others: [{ sha: "0b3d2c9", message: "test: order fixtures", author: "alice" }],
    verdict: "confident", margin: 0.48,
    error: { fp: "fp-91e", type: "RangeError", message: "amount must be an integer number of cents", count: 97 },
    fixPr: 17, similar: [], explanationSource: "model",
  },
  "inc-4": {
    id: "inc-4", service: "inventory", endpoint: "PUT /stock", onsetMin: 60, deployment: "d-43",
    handler: "updateStock", caller: "reserve",
    culprit: { sha: "c2f00d1", message: "inventory: batch reservations", author: "dana" },
    others: [
      { sha: "d9e8f7a", message: "inventory: retry on lock timeout", author: "chen" },
      { sha: "inventory:LOCK_TIMEOUT_MS@d-43", message: "LOCK_TIMEOUT_MS 5000 -> 500", author: "ops", kind: "config", nodeId: "ConfigChange:inventory:LOCK_TIMEOUT_MS@d-43" },
    ],
    verdict: "ambiguous", margin: 0.03,
    error: { fp: "fp-b20", type: "LockTimeoutError", message: "could not obtain row lock", count: 64 },
    fixPr: 0, similar: [], explanationSource: "model",
  },
  "inc-8": {
    id: "inc-8", service: "checkout", endpoint: "POST /checkout", onsetMin: 25, deployment: "d-45",
    handler: "submitCheckout", caller: "cartTotal",
    culprit: { sha: "b81d4c2", message: "checkout: adopt money-utils 2.0", author: "chen" },
    others: [
      { sha: "money-utils:1.9.0->2.0.0@d-45", message: "money-utils 1.9.0 -> 2.0.0", author: "renovate", kind: "dependency", nodeId: "DependencyChange:money-utils:1.9.0->2.0.0@d-45" },
      { sha: "5aa01be", message: "checkout: copy tweaks", author: "alice" },
    ],
    verdict: "confident", margin: 0.52,
    error: { fp: "fp-c33", type: "RangeError", message: "amount must be an integer number of cents", count: 141 },
    fixPr: 21,
    similar: [{ id: "inc-1", similarity: 0.82, basis: ["error_family:rangeerror:amount must be an integer number of cents", "thrown_in:money-utils:lib/index.js:toCents", "dependency:money-utils", "changed_fn:money-utils:lib/index.js:toCents"] }],
    explanationSource: "template",
  },
};

const SUMMARY_EXTRA: S["IncidentSummary"][] = [
  { id: "inc-8", repo: REPO, service: "checkout", env: "production", endpoint: "POST /checkout", status: "verifying", onset_ts: iso(minAgo(25)), detected_at: iso(minAgo(23)) },
  { id: "inc-4", repo: REPO, service: "inventory", env: "production", endpoint: "PUT /stock", status: "identified", onset_ts: iso(minAgo(60)), detected_at: iso(minAgo(57)) },
];

// -- mutable state (approve/reject/actions/root cause) ------------------------------------

type RemStatus = "proposing" | "verifying" | "verified" | "failed" | "needs_manual" | "approved" | "merged" | "rejected";
type State = {
  status: string;
  acknowledgedBy: string | null;
  history: Transition[];
  rem: { status: RemStatus; approvedBy: string | null; approvedAt: number | null; rejectedBy: string | null; rejectReason: string | null } | null;
  rootCause: { sha: string; status: "confirmed" | "rejected" } | null;
  liveSince: number | null; // inc-3: when the page first asked for it
};

const state = new Map<string, State>();

function initial(id: string): State {
  const sc = SCENARIOS[id];
  const t = (m: number) => iso(minAgo(sc.onsetMin - m));
  const tr = (from: string | null, to: string, m: number, actor = "system", reason = ""): Transition => ({ from_status: from, to_status: to, actor, reason, at: t(m) });
  const opened = [tr(null, "open", 2, "system", "detected")];
  switch (id) {
    case "inc-1":
      return {
        status: "resolved", acknowledgedBy: "bruno",
        history: [...opened, tr("open", "investigating", 2), tr("investigating", "explained", 3), tr("explained", "remediating", 4), tr("remediating", "verifying", 6), tr("verifying", "resolved", 21, "bruno", "fix merged and verified")],
        rem: { status: "merged", approvedBy: "bruno", approvedAt: minAgo(sc.onsetMin - 18), rejectedBy: null, rejectReason: null },
        rootCause: { sha: sc.culprit.sha, status: "confirmed" }, liveSince: null,
      };
    case "inc-2":
      return {
        status: "verifying", acknowledgedBy: null,
        history: [...opened, tr("open", "investigating", 2), tr("investigating", "explained", 3), tr("explained", "remediating", 5), tr("remediating", "verifying", 7)],
        rem: { status: "verified", approvedBy: null, approvedAt: null, rejectedBy: null, rejectReason: null },
        rootCause: null, liveSince: null,
      };
    case "inc-4":
      return { status: "identified", acknowledgedBy: null, history: [...opened, tr("open", "investigating", 3), tr("investigating", "identified", 4)], rem: null, rootCause: null, liveSince: null };
    case "inc-8":
      return {
        status: "verifying", acknowledgedBy: "chen",
        history: [...opened, tr("open", "investigating", 2), tr("investigating", "explained", 3), tr("explained", "remediating", 3), tr("remediating", "verifying", 5)],
        rem: { status: "verified", approvedBy: null, approvedAt: null, rejectedBy: null, rejectReason: null },
        rootCause: null, liveSince: null,
      };
    default: // inc-3 starts live on first view
      return { status: "open", acknowledgedBy: null, history: opened, rem: null, rootCause: null, liveSince: null };
  }
}

// inc-3 timeline, seconds after the page first loads it
const LIVE: { at: number; status: string; inv: string; rem: RemStatus | null }[] = [
  { at: 0, status: "investigating", inv: "not_run", rem: null },
  { at: 6, status: "explained", inv: "explained", rem: null },
  { at: 12, status: "remediating", inv: "explained", rem: "proposing" },
  { at: 18, status: "verifying", inv: "explained", rem: "verifying" },
  { at: 26, status: "verifying", inv: "explained", rem: "verified" },
];

function get(id: string): State {
  let s = state.get(id);
  if (!s) {
    s = initial(id);
    state.set(id, s);
  }
  if (id === "inc-3") advanceLive(s);
  if (s.rem?.status === "approved" && s.rem.approvedAt && Date.now() - s.rem.approvedAt > 4_000) {
    s.rem.status = "merged"; // "app" merge policy: Kaizen merged it; the merge webhook resolves
    move(s, "resolved", "system", "fix merged and verified");
  }
  return s;
}

function move(s: State, to: string, actor = "system", reason = "") {
  if (s.status === to) return;
  s.history.push({ from_status: s.status, to_status: to, actor, reason, at: iso(Date.now()) });
  s.status = to;
}

function advanceLive(s: State) {
  if (s.liveSince === null) s.liveSince = Date.now();
  if (s.rem && ["approved", "merged", "rejected"].includes(s.rem.status)) return; // a person took over
  if (s.status === "closed" || s.status === "resolved") return;
  const secs = (Date.now() - s.liveSince) / 1000;
  const step = [...LIVE].reverse().find((x) => secs >= x.at)!;
  if (step.status !== s.status) {
    for (const x of LIVE) if (x.at <= step.at && x.status !== s.status && !s.history.some((h) => h.to_status === x.status)) move(s, x.status);
  }
  if (step.rem) s.rem = { status: step.rem, approvedBy: null, approvedAt: null, rejectedBy: null, rejectReason: null };
}

const investigationStatus = (id: string, s: State): string => {
  if (id === "inc-3") {
    const secs = (Date.now() - (s.liveSince ?? Date.now())) / 1000;
    return secs >= 6 || s.status !== "investigating" ? "explained" : "not_run";
  }
  return SCENARIOS[id].explanationSource === "template" ? "fallback" : "explained";
};

// -- graph -----------------------------------------------------------------------------------

const fnId = (service: string, path: string, name: string) => `Function:${service}:${path}:${name}`;
const handlerId = (sc: Scenario) => fnId(sc.service, "src/routes.js", sc.handler);
const callerId = (sc: Scenario) => fnId(sc.service, `src/${sc.caller}.js`, sc.caller);
const endpointId = (sc: Scenario) => `Endpoint:${sc.service}:${sc.endpoint}`;
const suspectNode = (x: Suspect) => x.nodeId ?? `Commit:${x.sha}`;

function baseGraph(sc: Scenario): Graph {
  const s = get(sc.id);
  const inc = `Incident:${sc.id}`;
  const dep = `Deployment:${sc.deployment}`;
  const culprit = `Commit:${sc.culprit.sha}`;
  const err = `Error:${sc.error.fp}`;
  const onset = minAgo(sc.onsetMin);
  const nodes: GNode[] = [
    { id: inc, label: "Incident", props: { id: sc.id, service: sc.service, env: "production", status: s.status, endpoint: sc.endpoint }, highlight: true },
    { id: dep, label: "Deployment", props: { id: sc.deployment, env: "production", status: "success", started_at: Math.round(onset / 1000) - 120, sha_to: sc.culprit.sha }, highlight: true },
    { id: culprit, label: "Commit", props: { sha: sc.culprit.sha, message: sc.culprit.message, ts: Math.round(onset / 1000) - 3600, loc_added: 9, loc_removed: 4 }, highlight: true },
    { id: `File:money-utils:lib/index.js`, label: "File", props: { id: "money-utils:lib/index.js", path: "lib/index.js", service: "money-utils" }, highlight: false },
    { id: TO_CENTS, label: "Function", props: { name: "toCents", path: "lib/index.js", service: "money-utils", start_line: 12, end_line: 31 }, highlight: true },
    { id: callerId(sc), label: "Function", props: { name: sc.caller, path: `src/${sc.caller}.js`, service: sc.service, start_line: 4, end_line: 40 }, highlight: true },
    { id: handlerId(sc), label: "Function", props: { name: sc.handler, path: "src/routes.js", service: sc.service, start_line: 18, end_line: 66 }, highlight: true },
    { id: endpointId(sc), label: "Endpoint", props: { id: `${sc.service}:${sc.endpoint}`, route: sc.endpoint }, highlight: true },
    { id: `Service:${sc.service}`, label: "Service", props: { id: sc.service }, highlight: false },
    { id: err, label: "Error", props: { fingerprint: sc.error.fp, error_type: sc.error.type, message: sc.error.message, count: sc.error.count, service: sc.service }, highlight: true },
  ];
  const e = (source: string, rel: string, target: string, highlight = false, props: Record<string, unknown> = {}): GEdge => ({ id: `${source}|${rel}|${target}`, source, target, rel, props, highlight });
  const edges: GEdge[] = [
    e(dep, "triggers", inc, true),
    e(dep, "includes", culprit, true),
    e(inc, "evidenced_by", err, true),
    e(inc, "affects", `Service:${sc.service}`),
    e(inc, "likely_caused_by", culprit, true, { verdict: sc.verdict, tentative: sc.verdict === "ambiguous" }),
    e(err, "thrown_in", TO_CENTS, true, { frame_depth: 0 }),
    e(err, "observed_on", endpointId(sc), true),
    e(culprit, "modifies_fn", TO_CENTS, true),
    e(culprit, "modifies", `File:money-utils:lib/index.js`),
    e(`File:money-utils:lib/index.js`, "contains", TO_CENTS),
    e(handlerId(sc), "calls", callerId(sc), true),
    e(callerId(sc), "calls", TO_CENTS, true),
    e(handlerId(sc), "handles", endpointId(sc), true),
    e(handlerId(sc), "belongs_to", `Service:${sc.service}`),
    e(inc, "candidate", culprit, false, { rank: 1 }),
  ];
  sc.others.forEach((o, i) => {
    const id = suspectNode(o);
    const label = o.kind === "config" ? "ConfigChange" : o.kind === "dependency" ? "DependencyChange" : "Commit";
    const props: Record<string, unknown> =
      label === "Commit" ? { sha: o.sha, message: o.message } : label === "ConfigChange" ? { id: o.sha, key: o.message.split(" ")[0], service: sc.service } : { id: o.sha, name: "money-utils", from_version: "1.9.0", to_version: "2.0.0" };
    nodes.push({ id, label, props, highlight: false });
    edges.push(e(dep, label === "Commit" ? "includes" : label === "ConfigChange" ? "changes_config" : "bumps", id));
    edges.push(e(inc, "candidate", id, false, { rank: i + 2 }));
  });
  if (s.rem && s.rem.status !== "proposing") {
    const fix = `Fix:fix-rem-${sc.id}`;
    const test = `Test:${sc.service}:test/totals.test.js:rounds to integer cents`;
    nodes.push({ id: fix, label: "Fix", props: { id: `fix-rem-${sc.id}`, kind: "revert", pr_url: PR(sc.fixPr), verified: ["verified", "approved", "merged"].includes(s.rem.status) }, highlight: false });
    nodes.push({ id: test, label: "Test", props: { id: test.slice(5), name: "rounds to integer cents", status: "passed" }, highlight: false });
    edges.push(e(inc, "fixed_by", fix), e(fix, "reverts", culprit), e(fix, "verified_by", test));
  }
  for (const m of sc.similar) {
    nodes.push({ id: `Incident:${m.id}`, label: "Incident", props: { id: m.id, service: SCENARIOS[m.id].service, status: get(m.id).status }, highlight: false });
    edges.push(e(inc, "resembles", `Incident:${m.id}`, false, { similarity: m.similarity, basis: m.basis }));
  }
  return { incident_id: sc.id, nodes, edges };
}

function overlayGraph(sc: Scenario, overlay: string | null): Graph {
  const g = baseGraph(sc);
  if (overlay !== "blast" && overlay !== "history") return g;
  const have = new Set(g.nodes.map((n) => n.id));
  const add = (n: GNode) => !have.has(n.id) && (have.add(n.id), g.nodes.push({ ...n, overlay }));
  const edge = (source: string, rel: string, target: string, props: Record<string, unknown> = {}) =>
    g.edges.push({ id: `${source}|${rel}|${target}`, source, target, rel, props, highlight: false, overlay });
  if (overlay === "blast") {
    const reach: [string, string, string][] = [
      ["orders", "orderTotal", "POST /orders"],
      ["payments", "refund", "POST /refunds"],
      ["checkout", "cartTotal", "POST /checkout"],
    ].filter(([svc]) => svc !== sc.service) as [string, string, string][];
    for (const [svc, fn, ep] of reach) {
      const f = fnId(svc, `src/${fn}.js`, fn);
      add({ id: f, label: "Function", props: { name: fn, path: `src/${fn}.js`, service: svc }, highlight: false });
      add({ id: `Endpoint:${svc}:${ep}`, label: "Endpoint", props: { id: `${svc}:${ep}`, route: ep, impact: "potential", via: ["caller"] }, highlight: false });
      add({ id: `Service:${svc}`, label: "Service", props: { id: svc, impact: "potential", via: ["caller", "shared_dependency"] }, highlight: false });
      edge(f, "calls", TO_CENTS);
      edge(f, "handles", `Endpoint:${svc}:${ep}`);
      edge(f, "belongs_to", `Service:${svc}`);
    }
  } else {
    for (const m of sc.similar) {
      const prior = SCENARIOS[m.id];
      const fix = `Fix:fix-rem-${m.id}`;
      const commit = `Commit:${prior.culprit.sha}`;
      add({ id: fix, label: "Fix", props: { id: `fix-rem-${m.id}`, kind: "revert", pr_url: PR(prior.fixPr), accepted: true }, highlight: false });
      add({ id: commit, label: "Commit", props: { sha: prior.culprit.sha, message: prior.culprit.message }, highlight: false });
      edge(`Incident:${m.id}`, "fixed_by", fix);
      edge(fix, "reverts", commit);
      edge(commit, "modifies_fn", TO_CENTS);
    }
  }
  return g;
}

// -- ranking ---------------------------------------------------------------------------------

const FEATURES = ["temporal", "path", "endpoint", "service", "dependency", "msg_sim", "history", "sequence", "centrality", "blast"] as const;
const WEIGHTS: Record<(typeof FEATURES)[number], number> = { temporal: 0.15, path: 0.25, endpoint: 0.15, service: 0.1, dependency: 0.1, msg_sim: 0.05, history: 0.1, sequence: 0.05, centrality: 0.05, blast: -0.1 };

function features(sc: Scenario, x: Suspect, v: Record<string, number>): S["FeatureValue"][] {
  const node = suspectNode(x);
  const why: Record<string, [string, string[]]> = {
    temporal: ["Deployed 2 minutes before the onset", [`Deployment:${sc.deployment}`, `Incident:${sc.id}`]],
    path: [v.path ? `Changes toCents, which ${sc.error.type} is thrown in (distance 0)` : "No call path from the changed code to the failing frames", v.path ? [node, TO_CENTS, `Error:${sc.error.fp}`] : []],
    endpoint: [v.endpoint ? `${sc.handler} handles ${sc.endpoint}, the failing endpoint` : "Touches no code behind the failing endpoint", v.endpoint ? [handlerId(sc), endpointId(sc)] : []],
    service: [v.service ? `Changed code runs in ${sc.service}` : "Different service", v.service ? [`Service:${sc.service}`] : []],
    dependency: [v.dependency ? "Changes money-utils, a dependency of the failing code" : "No dependency change", []],
    msg_sim: ["Commit message shares words with the error", []],
    history: [v.history ? "A similar past incident blamed this function" : "No history", v.history && sc.similar[0] ? [`Incident:${sc.similar[0].id}`] : []],
    sequence: ["Last change to the failing function before the onset", v.sequence ? [node] : []],
    centrality: ["toCents is called from 3 services", v.centrality ? [TO_CENTS] : []],
    blast: ["Wide blast radius lowers certainty slightly", []],
  };
  return FEATURES.map((name) => ({
    name,
    value: v[name] ?? 0,
    weight: WEIGHTS[name],
    contribution: Math.round(WEIGHTS[name] * (v[name] ?? 0) * 10000) / 10000,
    query: `feature.${name}`,
    explanation: why[name][0],
    evidence_ids: why[name][1],
    cypher: `// ${name}\nMATCH (c:Commit {sha: $sha})-[:modifies_fn]->(f:Function)\nWITH f, shortestPath((f)-[:calls*1..6]->(g)) AS p\nRETURN 1.0 / (1 + min(length(p)))`,
  }));
}

function ranking(sc: Scenario, method: "graph" | "b0" | "b1" = "graph"): S["RankResult"] {
  const top = sc.verdict === "ambiguous" ? 0.46 : 0.78;
  const cands: Candidate[] = [
    {
      kind: "commit", node_id: suspectNode(sc.culprit), sha: sc.culprit.sha, message: sc.culprit.message, author: sc.culprit.author, deployment: sc.deployment, rank: 1, score: top, confidence: sc.verdict === "ambiguous" ? 0.41 : 0.86,
      features: features(sc, sc.culprit, { temporal: 0.9, path: 1, endpoint: 1, service: 1, dependency: sc.id === "inc-8" ? 1 : 0, msg_sim: 0.2, history: sc.similar.length ? 0.8 : 0, sequence: 1, centrality: 0.8, blast: 0.3 }),
      evidence: [{ changed_function: TO_CENTS.slice(9), thrown_function: TO_CENTS.slice(9), error: sc.error.fp }],
    },
    ...sc.others.map((o, i): Candidate => {
      const close = sc.verdict === "ambiguous" && i < 2;
      return {
        kind: o.kind ?? "commit", node_id: suspectNode(o), sha: o.sha, message: o.message, author: o.author, deployment: sc.deployment, rank: i + 2,
        score: close ? top - 0.03 * (i + 1) : 0.21 - i * 0.09, confidence: close ? 0.38 - i * 0.05 : 0.09,
        features: features(sc, o, close ? { temporal: 0.9, path: 0.5, endpoint: 1, service: 1, sequence: 0.5 } : { temporal: 0.9, sequence: 1 }),
        evidence: [],
      };
    }),
  ];
  if (method === "graph") return { incident_id: sc.id, method, verdict: sc.verdict, margin: sc.margin, weights_version: "2026-10-12.fit-s1s3s5s8", candidates: cands };
  const base = [...cands].reverse().map((c, i) => ({ ...c, rank: i + 1, score: 1 - i * 0.4, confidence: 0, features: [], evidence: [] }));
  return { incident_id: sc.id, method, verdict: "confident", margin: 0, weights_version: "2026-10-12.fit-s1s3s5s8", candidates: base };
}

// -- explanation + trace -------------------------------------------------------------------

function explanation(sc: Scenario): Record<string, unknown> {
  const c = `Commit:${sc.culprit.sha}`;
  const dep = `Deployment:${sc.deployment}`;
  const inc = `Incident:${sc.id}`;
  const err = `Error:${sc.error.fp}`;
  if (sc.verdict === "ambiguous") {
    return {
      verdict: "ambiguous", culprit_sha: null, source: "model",
      claims: [
        { text: `Three changes shipped in ${sc.deployment} touch the code behind ${sc.endpoint}; none of them is clearly ahead.`, cited_node_ids: [dep, inc] },
        { text: `${sc.error.type} is thrown while reserving stock, which both ${sc.culprit.sha} and d9e8f7a modify.`, cited_node_ids: [err, c, "Commit:d9e8f7a"] },
      ],
      causal_chain: [],
      confidence_statement: `Margin between the top two suspects is ${sc.margin.toFixed(2)}, below the 0.10 needed to name one. Kaizen abstains and lists the top three for a person to decide.`,
      alternatives: sc.others.map((o) => ({ sha: o.sha, reason: o.kind === "config" ? "Shorter lock timeout would surface as lock errors under load." : "Also changes the reservation path.", cited_node_ids: [suspectNode(o)] })),
      responders: ["dana", "chen"],
    };
  }
  const prior = sc.similar[0];
  return {
    verdict: "confident", culprit_sha: sc.culprit.sha, culprit_function: TO_CENTS.slice(9), source: sc.explanationSource,
    claims: [
      { text: `${sc.culprit.sha} changed toCents, the function ${sc.error.type} is thrown in.`, cited_node_ids: [c, TO_CENTS, `${c}|modifies_fn|${TO_CENTS}`] },
      { text: `The failures began 2 minutes after ${sc.deployment}, which shipped ${sc.culprit.sha}.`, cited_node_ids: [`${dep}|includes|${c}`, inc] },
      ...(prior ? [{ text: `This matches ${prior.id} (similarity ${prior.similarity.toFixed(2)}), which was resolved by reverting the same kind of change.`, cited_node_ids: [`Incident:${prior.id}`] }] : []),
    ],
    causal_chain: [
      { step: `${sc.deployment} ships ${sc.culprit.sha} to production`, cited_node_ids: [`${dep}|includes|${c}`] },
      { step: `${sc.culprit.sha} makes toCents return a float`, cited_node_ids: [`${c}|modifies_fn|${TO_CENTS}`] },
      { step: `${sc.handler} → ${sc.caller} → toCents on every ${sc.endpoint}`, cited_node_ids: [handlerId(sc), callerId(sc), TO_CENTS] },
      { step: `${sc.error.type}: ${sc.error.message}`, cited_node_ids: [err, `${err}|observed_on|${endpointId(sc)}`] },
    ],
    confidence_statement: `Ranked first by a margin of ${sc.margin.toFixed(2)}; the changed function is the one that throws, on the failing endpoint.`,
    alternatives: sc.others.slice(0, 1).map((o) => ({ sha: o.sha, reason: "Shipped in the same deployment but touches no code on the failing path.", cited_node_ids: [suspectNode(o)] })),
    suggested_fix: `Revert ${sc.culprit.sha}`,
    responders: [sc.culprit.author],
  };
}

function steps(sc: Scenario) {
  return [
    { n: 1, name: "get_incident", args: { incident_id: sc.id }, summary: "get_incident: errors=1 item(s)", latency_ms: 38, node_ids: [`Incident:${sc.id}`, `Error:${sc.error.fp}`] },
    { n: 2, name: "rank_candidates", args: { incident_id: sc.id }, summary: `rank_candidates: verdict=${sc.verdict}, candidates=${sc.others.length + 1} item(s)`, latency_ms: 212, node_ids: [`Commit:${sc.culprit.sha}`] },
    { n: 3, name: "shortest_call_path", args: { sha: sc.culprit.sha, incident_id: sc.id }, summary: "shortest_call_path: paths=1 item(s)", latency_ms: 64, node_ids: [TO_CENTS] },
    { n: 4, name: "similar_incidents", args: { incident_id: sc.id }, summary: `similar_incidents: similar=${sc.similar.length} item(s)`, latency_ms: 31, node_ids: sc.similar.map((m) => `Incident:${m.id}`) },
  ];
}

// -- remediation -----------------------------------------------------------------------------

const DIFF = (sc: Scenario) => `diff --git a/lib/index.js b/lib/index.js
index 4c1d2e0..9a7b3f1 100644
--- a/lib/index.js
+++ b/lib/index.js
@@ -12,9 +12,9 @@ const SCALE = 100;
 /** Convert a decimal amount to integer cents. */
 function toCents(amount) {
   if (typeof amount !== "number") throw new TypeError("amount must be a number");
-  const cents = amount * SCALE;
-  return Math.round(cents * 100) / 100;
+  // revert of ${sc.culprit.sha}: cents are integers
+  return Math.round(amount * SCALE);
 }

 module.exports = { toCents, SCALE };
`;

function remediation(sc: Scenario, s: State): Remediation | null {
  if (!s.rem) return null;
  const r = s.rem;
  const verifiedish = ["verified", "approved", "merged", "failed"].includes(r.status);
  const prior = sc.similar.map((m) => ({ incident_id: m.id, similarity: m.similarity, basis: m.basis, fix_kind: "revert", fix_pr_url: PR(SCENARIOS[m.id].fixPr), fix_sha: SCENARIOS[m.id].culprit.sha }));
  const reused = prior[0];
  return {
    id: `rem-${sc.id}`,
    kind: "revert",
    status: r.status,
    sha: sc.culprit.sha,
    branch: `kaizen/fix-${sc.id}`,
    pr_url: r.status === "proposing" ? null : PR(sc.fixPr),
    pr_number: r.status === "proposing" ? null : sc.fixPr,
    approved_by: r.approvedBy,
    rejected_by: r.rejectedBy,
    reject_reason: r.rejectReason,
    labels: ["kaizen", `incident:${sc.id}`, "revert"],
    diff: r.status === "proposing" ? null : DIFF(sc),
    rationale:
      `Revert of ${sc.culprit.sha} (${sc.culprit.message}): ranked #1 by the deterministic graph score with margin ${sc.margin.toFixed(2)}; it changes toCents, the function the error is thrown in.` +
      (reused ? ` The same fix resolved ${reused.incident_id} (similarity ${reused.similarity.toFixed(2)}, ${reused.fix_pr_url}).` : ""),
    check_status: !verifiedish ? (r.status === "verifying" ? "in_progress" : null) : r.status === "failed" ? "failure" : "success",
    merge: r.status === "approved" || r.status === "merged" ? { policy: "app", status: r.status === "merged" ? "merged" : "requested", at: iso(r.approvedAt ?? Date.now()) } : null,
    prior_fixes: prior,
    verification: verifiedish
      ? {
          passed: r.status !== "failed",
          conclusion: "success",
          run_url: RUN(4100 + Number(sc.id.split("-")[1])),
          error_rate_before: 0.42,
          error_rate_after: 0.004,
          baseline_error_rate: 0.005,
          p95_ms: 92,
          metrics: {
            failing: { error_rate: 0.42, p95_ms: 880, samples: 600 },
            fixed: { error_rate: 0.004, p95_ms: 92, samples: 600 },
            baseline: { error_rate: 0.005, p95_ms: 88, samples: 600 },
          },
          verdict: {
            passed: true,
            checks: { workflow: true, tests: true, error_rate: true, samples: true, p95: true },
            reasons: [],
          },
          tests: [{ name: "rounds to integer cents", status: "passed" }],
          check: { conclusion: "success", posted: true },
        }
      : null,
  };
}

// -- metrics ---------------------------------------------------------------------------------

function metrics(sc: Scenario, minutes: number): S["IncidentMetrics"] {
  const onset = minAgo(sc.onsetMin);
  const since = onset - Math.floor((minutes * 2) / 3) * 60_000;
  const until = since + minutes * 60_000;
  const s = get(sc.id);
  const fixedAt = s.status === "resolved" ? onset + 21 * 60_000 : Infinity;
  const series: S["MetricPoint"][] = [];
  for (let t = since; t < Math.min(until, Date.now()); t += 60_000) {
    const k = (t - since) / 60_000;
    const noise = ((k * 7919) % 13) / 13; // deterministic jitter
    const failing = t >= onset && t < fixedAt;
    const requests = 180 + Math.round(noise * 40);
    const rate = failing ? 0.36 + noise * 0.1 : 0.003 + noise * 0.004;
    const errors = Math.round(requests * rate);
    series.push({ ts: iso(t), requests, errors, error_rate: Math.round((errors / requests) * 10000) / 10000, p95_ms: failing ? 820 + Math.round(noise * 140) : 80 + Math.round(noise * 18) });
  }
  return {
    incident_id: sc.id, onset: iso(onset), since: iso(since), until: iso(until), service: sc.service, env: "production", endpoint: sc.endpoint, series,
    deployments: [
      { id: "d-" + (Number(sc.deployment.slice(2)) - 1), sha: sc.others[0]?.sha ?? null, started_at: iso(onset - 52 * 60_000), status: "success" },
      { id: sc.deployment, sha: sc.culprit.sha, started_at: iso(onset - 2 * 60_000), status: "success" },
    ].filter((d) => Date.parse(d.started_at) >= since),
  };
}

// -- node inspector --------------------------------------------------------------------------

function nodeDetail(sc: Scenario, nodeId: string): S["NodeDetail"] | null {
  const g = mergeAll(sc);
  const n = g.nodes.find((x) => x.id === nodeId);
  if (!n) return null;
  const nb = g.edges
    .filter((e) => e.source === nodeId || e.target === nodeId)
    .map((e) => {
      const other = g.nodes.find((x) => x.id === (e.source === nodeId ? e.target : e.source))!;
      const p = other.props as Record<string, unknown>;
      return { id: other.id, label: other.label, rel: e.rel, direction: (e.source === nodeId ? "out" : "in") as "out" | "in", name: String(p.name ?? p.message ?? p.route ?? other.id.split(":").slice(1).join(":")) };
    });
  const out: S["NodeDetail"] = { id: n.id, label: n.label, props: n.props, neighbours: nb.slice(0, 40), neighbours_total: nb.length, commit: null, function: null, error_samples: [] };
  const p = n.props as Record<string, unknown>;
  if (n.label === "Commit") {
    const culprit = Object.values(SCENARIOS).find((x) => x.culprit.sha === p.sha);
    out.commit = {
      sha: String(p.sha), message: String(p.message ?? ""), author: culprit?.culprit.author ?? Object.values(SCENARIOS).flatMap((x) => x.others).find((o) => o.sha === p.sha)?.author ?? null,
      ts: iso(minAgo((culprit?.onsetMin ?? 60) + 60)),
      changed_functions: culprit ? [{ function_id: TO_CENTS.slice(9), path: "lib/index.js", start_line: 12, end_line: 31, lines_added: 2, lines_removed: 1, moved_from: null }] : [],
      changed_files: culprit ? [{ file_id: "money-utils:lib/index.js", path: "lib/index.js", status: "modified" }] : [{ file_id: "docs:README.md", path: "README.md", status: "modified" }],
    };
  }
  if (n.label === "Function") {
    out.function = {
      path: (p.path as string) ?? null, service: (p.service as string) ?? null, start_line: (p.start_line as number) ?? null, end_line: (p.end_line as number) ?? null,
      callers: g.edges.filter((e) => e.rel === "calls" && e.target === nodeId).map((e) => e.source.slice(9)),
      callees: g.edges.filter((e) => e.rel === "calls" && e.source === nodeId).map((e) => e.target.slice(9)),
    };
  }
  if (n.label === "Error") {
    out.error_samples = Array.from({ length: 5 }, (_, i) => ({ ts: iso(Date.now() - (i * 47 + 20) * 1000), error_type: sc.error.type, message: `${sc.error.message} (got ${(12.5 + i * 0.37).toFixed(2)})`, endpoint: sc.endpoint, status_code: 500 }));
  }
  return out;
}

function mergeAll(sc: Scenario): Graph {
  const a = overlayGraph(sc, "blast");
  const b = overlayGraph(sc, "history");
  const ids = new Set(a.nodes.map((n) => n.id));
  const eids = new Set(a.edges.map((e) => e.id));
  return { incident_id: sc.id, nodes: [...a.nodes, ...b.nodes.filter((n) => !ids.has(n.id))], edges: [...a.edges, ...b.edges.filter((e) => !eids.has(e.id))] };
}

// -- dispatch --------------------------------------------------------------------------------

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const err = (status: number, message: string) => json({ error: { code: status === 409 ? "conflict" : "invalid", message, request_id: "mock" } }, status);

function summary(id: string): S["IncidentSummary"] {
  const listed = mockIncidents.items.find((i) => i.id === id) ?? SUMMARY_EXTRA.find((i) => i.id === id)!;
  return { ...listed, status: get(id).status };
}

function syncList() {
  for (const extra of SUMMARY_EXTRA) {
    if (!mockIncidents.items.some((i) => i.id === extra.id)) {
      mockIncidents.items.unshift({ ...extra });
      mockIncidents.total = mockIncidents.items.length;
    }
  }
  for (const item of mockIncidents.items) if (SCENARIOS[item.id] && state.has(item.id)) item.status = get(item.id).status;
}

async function body(init?: RequestInit, input?: RequestInfo | URL): Promise<Record<string, unknown>> {
  try {
    const raw = init?.body ?? (input instanceof Request ? await input.clone().text() : null);
    return raw ? (JSON.parse(String(raw)) as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

/** Handles the incident-page routes; null hands the request back to mock.ts. */
export async function mockIncidentFetch(url: URL, method: string, init?: RequestInit, input?: RequestInfo | URL): Promise<Response | null> {
  syncList();
  const p = url.pathname;
  const rem = /^\/api\/v1\/remediations\/rem-(inc-\d+)\/(approve|reject)$/.exec(p);
  if (rem && method === "POST" && SCENARIOS[rem[1]]) {
    const s = get(rem[1]);
    if (!s.rem) return err(404, "remediation not found");
    if (rem[2] === "approve") {
      if (s.rem.status !== "verified") return err(409, `remediation is ${s.rem.status}; only verified fixes can be approved`);
      Object.assign(s.rem, { status: "approved", approvedBy: "demo", approvedAt: Date.now() });
      return json({ id: `rem-${rem[1]}`, status: "approved", approved_by: "demo", merge: "requested" });
    }
    const reason = String((await body(init, input)).reason ?? "").trim();
    if (!reason) return err(422, "a reason is required to reject a fix");
    if (!["verifying", "verified", "failed", "needs_manual"].includes(s.rem.status)) return err(409, `remediation is ${s.rem.status}`);
    Object.assign(s.rem, { status: "rejected", rejectedBy: "demo", rejectReason: reason });
    move(s, "needs_human", "demo", `fix rejected: ${reason}`);
    return json({ id: `rem-${rem[1]}`, status: "rejected", rejected_by: "demo", reject_reason: reason, incident_status: s.status });
  }

  const m = /^\/api\/v1\/incidents\/([^/]+)(?:\/(.*))?$/.exec(p);
  if (!m) return null;
  const id = decodeURIComponent(m[1]);
  const sc = SCENARIOS[id];
  if (!sc) return null;
  const sub = m[2] ?? "";
  const s = get(id);

  if (sub === "" && method === "GET") {
    const inv = investigationStatus(id, s);
    return json({
      incident: summary(id),
      ranking: ranking(sc),
      investigation: { id: inv === "not_run" ? null : 1, status: inv, explanation: inv === "not_run" ? null : explanation(sc), steps: inv === "not_run" ? [] : steps(sc), validation_errors: inv === "fallback" ? ["claim 2 cites Commit:ffffff0, which is not in the evidence graph"] : [] },
      remediation: remediation(sc, s),
      severity: sc.id === "inc-4" ? "sev3" : "sev2",
      acknowledged_by: s.acknowledgedBy,
      confirmed_root_cause: s.rootCause?.status === "confirmed" ? s.rootCause.sha : null,
      root_cause_status: s.rootCause?.status ?? null,
    } satisfies S["IncidentDetail"]);
  }
  if (sub === "rank" && method === "POST") return json(ranking(sc, (url.searchParams.get("method") ?? "graph") as "graph" | "b0" | "b1"));
  if (sub === "evidence-graph") return json(overlayGraph(sc, url.searchParams.get("overlay")));
  if (sub === "metrics") return json(metrics(sc, Number(url.searchParams.get("minutes") ?? 120)));
  if (sub === "history") return json(s.history);
  if (sub === "similar") {
    return json(
      sc.similar.map((x) => ({ incident_id: x.id, similarity: x.similarity, basis: x.basis, status: get(x.id).status, fix: { id: `fix-rem-${x.id}`, kind: "revert", pr_url: PR(SCENARIOS[x.id].fixPr) }, confirmed_root_cause: SCENARIOS[x.id].culprit.sha })),
    );
  }
  if (sub.startsWith("nodes/")) {
    const d = nodeDetail(sc, decodeURIComponent(sub.slice(6)));
    return d ? json(d) : err(404, "node not found in this repository's graph");
  }
  if (sub === "llm-cost") return json({ incident_id: id, calls: sc.explanationSource === "template" ? 3 : 4, input_tokens: 5120, output_tokens: 610, cost_usd: 0.0214, latency_ms_total: 6400 });
  if (sub === "actions" && method === "POST") {
    const b = await body(init, input);
    const action = String(b.action);
    const reason = String(b.reason ?? "");
    if (action === "acknowledge") {
      if (s.status === "resolved" || s.status === "closed") return err(409, `incident is ${s.status}; reopen it to acknowledge`);
      s.acknowledgedBy = s.acknowledgedBy ?? "demo";
    } else if (action === "close" || action === "reopen") {
      const to = action === "close" ? "closed" : "open";
      if (s.status === to) return err(409, `incident is already ${to}`);
      if (action === "close" && s.status === "resolved") return err(409, "resolved -> closed is not allowed");
      move(s, to, "demo", reason);
      if (to === "open") s.acknowledgedBy = null;
    } else return err(422, "unknown action");
    return json({ id, status: s.status, severity: "sev2", acknowledged_by: s.acknowledgedBy });
  }
  if (sub === "root-cause" && method === "POST") {
    const b = await body(init, input);
    s.rootCause = { sha: String(b.sha), status: b.confirmed ? "confirmed" : "rejected" };
    return json({ incident_id: id, sha: s.rootCause.sha, root_cause_status: s.rootCause.status, confirmed_root_cause: b.confirmed ? s.rootCause.sha : null });
  }
  return null;
}
