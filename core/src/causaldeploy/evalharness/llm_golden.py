"""Golden tests for the investigation agent: S1-S5 graphs, judged by a rubric.

Rubric per case: the explanation cites the culprit commit and names it, every claim and causal
step is cited (and the run validated), and tokens / cost / model latency stay within budget.

Modes:
  replay           recorded replies only (CI; `pytest tests/test_llm_golden.py`), zero network
  live             the real OpenAI model (needs CD_OPENAI_API_KEY + CD_OPENAI_MODEL), recording
                   its replies into --dir so a good live run can become the replay fixtures
  record-scripted  regenerate fixtures from the deterministic scripted investigator below

  make eval-live                    # live, writes eval/llm-live
  python -m causaldeploy.evalharness.llm_golden --mode record-scripted   # refresh CI fixtures
"""

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from causaldeploy.agent.llm import LlmClient, LlmReply, LlmToolCall
from causaldeploy.agent.loop import Budget, run_agent
from causaldeploy.agent.models import AgentRun
from causaldeploy.agent.replay import ReplayClient
from causaldeploy.config import get_settings
from causaldeploy.graph.builder import GraphBuilder, Scenario
from causaldeploy.graph.store import GraphStore

Mode = Literal["replay", "live", "record-scripted"]
SCENARIOS = (
    "s1_payment_validation",
    "s2_two_functions_one_file",
    "s3_five_commit_deployment",
    "s4_cross_service",
    "s5_dependency_bump",
)
SCRIPTED_MODEL = "golden-scripted"
FIXTURES = Path("tests/fixtures/llm/golden")
MAX_LATENCY_MS = 120_000.0  # model time per run


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class GoldenResult:
    scenario: str
    status: str
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


def rubric(
    run: AgentRun, sc: Scenario, budget: Budget, max_latency_ms: float = MAX_LATENCY_MS
) -> list[Check]:
    expl = run.explanation
    cited = (
        {n for c in expl.claims for n in c.cited_node_ids}
        | {n for s in expl.causal_chain for n in s.cited_node_ids}
        if expl
        else set()
    )
    tokens = sum(run.usage.values())
    latency = sum(c.latency_ms for c in run.llm_calls)
    every_cited = bool(
        expl
        and run.status == "explained"
        and not run.validation_errors
        and all(c.cited_node_ids for c in expl.claims)
        and all(s.cited_node_ids for s in expl.causal_chain)
    )
    return [
        Check(
            "cites_culprit",
            bool(expl and expl.culprit_sha == sc.culprit_commit)
            and f"Commit:{sc.culprit_commit}" in cited,
            f"culprit {sc.culprit_commit}, named {expl.culprit_sha if expl else None}",
        ),
        Check("every_claim_cited", every_cited, f"status {run.status}"),
        Check("tokens_within_budget", tokens <= budget.max_tokens, f"{tokens} tokens"),
        Check("cost_within_budget", run.cost_usd <= budget.max_cost_usd, f"${run.cost_usd:.4f}"),
        Check("latency_within_budget", latency <= max_latency_ms, f"{latency:.0f} ms"),
    ]


class ScriptedInvestigator:
    """A deterministic stand-in for the model: follows the prompt's procedure and answers only
    from node ids the tools returned. Used to (re)generate replay fixtures without a key."""

    model = SCRIPTED_MODEL

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LlmReply:
        results = [_unwrap(m["content"]) for m in messages if m.get("role") == "tool"]
        incident_id = messages[1]["content"].split("incident ", 1)[1].split(".", 1)[0]
        n = len(results)
        if n == 0:
            return _call(n, "get_incident", incident_id=incident_id)
        if n == 1:
            return _call(n, "rank_candidates", incident_id=incident_id)
        ranked = results[1]
        cands = ranked.get("candidates") or []
        if n == 2 and cands:
            if cands[0]["evidence"]:
                return _call(n, "shortest_call_path", sha=cands[0]["sha"], incident_id=incident_id)
            return _call(n, "dependency_impact", sha=cands[0]["sha"])
        if n == 3 and cands:
            return _call(n, "get_deployment_commits", incident_id=incident_id)
        return _final(incident_id, results, messages)


def _unwrap(content: str) -> dict[str, Any]:
    body = content.split("\n", 1)[1].rsplit("\n", 1)[0] if content.startswith("<") else content
    out: dict[str, Any] = json.loads(body)
    return out


def _usage(messages: list[dict[str, Any]], out: str) -> dict[str, int]:
    return {
        "input_tokens": len(json.dumps(messages)) // 4,
        "output_tokens": max(1, len(out) // 4),
    }


def _call(i: int, name: str, **args: Any) -> LlmReply:
    tc = LlmToolCall(f"call_{i + 1}", name, args)
    raw = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }
    return LlmReply(None, [tc], raw, {"input_tokens": 0, "output_tokens": 0})


def _final(
    incident_id: str, results: list[dict[str, Any]], messages: list[dict[str, Any]]
) -> LlmReply:
    ranked = results[1] if len(results) > 1 else {}
    returned = {n for r in results for n in r.get("node_ids", [])}
    cands = ranked.get("candidates") or []
    inc_ref = f"Incident:{incident_id}"
    if not cands or ranked.get("verdict") not in ("confident", "ambiguous"):
        answer: dict[str, Any] = {
            "verdict": "not_code",
            "claims": [
                {"text": "No code change is linked to the failure.", "cited_node_ids": [inc_ref]}
            ],
        }
    else:
        top = cands[0]
        commit = f"Commit:{top['sha']}"
        claims = [
            {
                "text": f"{top['sha'][:8]} ranks first (verdict {ranked['verdict']}).",
                "cited_node_ids": [commit],
            }
        ]
        chain = []
        deps = [r for r in results if "deployments" in r]
        for d in deps[0]["deployments"] if deps else []:
            if top["sha"] in d["commits"]:
                chain.append(
                    {
                        "step": f"{d['deployment_id']} shipped {top['sha'][:8]}.",
                        "cited_node_ids": [f"Deployment:{d['deployment_id']}", commit],
                    }
                )
        fn = None
        if top["evidence"]:
            ev = top["evidence"][0]
            fn = ev["changed_function"]
            ids = [
                x for x in (f"Function:{fn}", f"Function:{ev['thrown_function']}") if x in returned
            ]
            claims.append(
                {
                    "text": f"It changed {fn}, which reaches {ev['thrown_function']} (threw).",
                    "cited_node_ids": [commit, *ids],
                }
            )
            chain.append(
                {"step": f"The commit changed {fn}.", "cited_node_ids": [commit, *ids[:1]]}
            )
        else:
            dep_ids = sorted(n for n in returned if n.startswith("Dependency:"))
            if dep_ids:
                claims.append(
                    {
                        "text": "It changed a dependency the failing code uses.",
                        "cited_node_ids": dep_ids,
                    }
                )
        chain.append({"step": "The incident followed.", "cited_node_ids": [inc_ref]})
        ambiguous = ranked["verdict"] == "ambiguous"
        answer = {
            "verdict": ranked["verdict"],
            "culprit_sha": None if ambiguous else top["sha"],
            "culprit_function": None if ambiguous else fn,
            "claims": claims,
            "causal_chain": chain,
            "confidence_statement": f"Ranking {ranked['verdict']}, margin {ranked['margin']}.",
            "alternatives": [
                {
                    "sha": c["sha"],
                    "reason": f"rank {c['rank']}",
                    "cited_node_ids": [f"Commit:{c['sha']}"],
                }
                for c in cands[1:3]
            ],
            "suggested_fix": None if ambiguous else f"revert {top['sha'][:7]}",
            "responders": [],
        }
    text = json.dumps(answer)
    return LlmReply(text, [], {"role": "assistant", "content": text}, _usage(messages, text))


def _client(mode: Mode, directory: Path, model: str | None) -> LlmClient:
    if mode == "replay":
        return ReplayClient(None, mode="replay", directory=directory, model=model or SCRIPTED_MODEL)
    if mode == "record-scripted":
        return ReplayClient(ScriptedInvestigator(), mode="record", directory=directory)
    from causaldeploy.agent.llm import OpenAIClient

    return ReplayClient(OpenAIClient(), mode="record", directory=directory)


async def run_case(name: str, llm: LlmClient, budget: Budget) -> GoldenResult:
    from mcp import Client

    from causaldeploy.mcp_server.server import build_server

    store = GraphStore(f"golden/{uuid.uuid4().hex[:12]}")
    try:
        sc: Scenario = getattr(GraphBuilder(store), name)()
        async with Client(build_server(store.repo, store)) as client:
            run = await run_agent(client, store, sc.incident_id, llm, budget=budget)
        return GoldenResult(sc.name, run.status, rubric(run, sc, budget))
    finally:
        store.drop()


async def run_golden(
    mode: Mode = "replay",
    directory: Path = FIXTURES,
    model: str | None = None,
    scenarios: tuple[str, ...] = SCENARIOS,
) -> list[GoldenResult]:
    budget = Budget.from_settings()
    llm = _client(mode, directory, model)
    return [await run_case(name, llm, budget) for name in scenarios]


def report(results: list[GoldenResult]) -> str:
    lines = ["| scenario | status | " + " | ".join(c.name for c in results[0].checks) + " |"]
    lines.append("|" + "---|" * (2 + len(results[0].checks)))
    for r in results:
        cells = [("pass" if c.passed else "FAIL") + f" ({c.detail})" for c in r.checks]
        lines.append(f"| {r.scenario} | {r.status} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="causaldeploy.evalharness.llm_golden")
    ap.add_argument("--mode", choices=["replay", "live", "record-scripted"], default="replay")
    ap.add_argument("--dir", type=Path, default=None, help="fixtures directory")
    ap.add_argument("--model", default=None, help="model name the fixtures were recorded with")
    args = ap.parse_args(argv)
    mode: Mode = args.mode
    if mode == "live":
        s = get_settings()
        if not (s.openai_api_key.get_secret_value() and s.openai_model):
            print("eval-live needs CD_OPENAI_API_KEY and CD_OPENAI_MODEL", file=sys.stderr)
            return 2
    directory = args.dir or (Path("../eval/llm-live") if mode == "live" else FIXTURES)
    results = asyncio.run(run_golden(mode, directory, args.model))
    print(report(results))
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
