"""Deterministic explanation from the ranking and its evidence (no model involved).

Used when the model's answer is invalid twice or the run stops on a budget: the incident still
gets an explanation whose every citation comes from the same read-only tools the agent uses.
"""

from typing import Any

from causaldeploy.agent.models import Alternative, CausalStep, Claim, Explanation
from causaldeploy.mcp_server import tools as T


def build(store: Any, incident_id: str) -> tuple[Explanation, set[str]]:
    """The explanation and the node ids the template's own lookups returned (citable)."""
    inc = T.get_incident(store, incident_id)
    ranked = T.rank_candidates(store, incident_id)
    returned = set(inc.get("node_ids", [])) | set(ranked.get("node_ids", []))
    incident_ref = [f"Incident:{incident_id}"] if f"Incident:{incident_id}" in returned else []
    cands: list[dict[str, Any]] = ranked.get("candidates") or []
    verdict = ranked.get("verdict")
    margin = ranked.get("margin", 0.0)

    if not cands or verdict not in ("confident", "ambiguous"):
        cite = incident_ref or sorted(returned)[:1]
        claim_text = (
            "No commit deployed before this incident is linked to the failing code "
            f"(deterministic ranking verdict: {verdict}); this does not look like a code change."
        )
        return (
            Explanation(
                verdict="not_code",
                claims=[Claim(text=claim_text, cited_node_ids=cite or ["Incident:" + incident_id])],
                confidence_statement="Kaizen abstains: there is no code evidence to cite.",
                source="template",
            ),
            returned,
        )

    top = cands[0]
    top_ref = f"Commit:{top['sha']}"
    ev = top.get("evidence") or []
    first = ev[0] if ev else None
    path = T.shortest_call_path(store, top["sha"], incident_id)
    returned |= set(path.get("node_ids", []))
    deps = T.get_deployment_commits(store, incident_id)
    returned |= set(deps.get("node_ids", []))
    dep_ids = [
        f"Deployment:{d['deployment_id']}"
        for d in deps.get("deployments", [])
        if top["sha"] in d.get("commits", [])
    ]

    claims = [
        Claim(
            text=(
                f"Commit {top['sha'][:8]} ranks first of {len(cands)} candidate(s) "
                f"(score {top['score']}, margin {margin})."
            ),
            cited_node_ids=[top_ref],
        )
    ]
    chain: list[CausalStep] = []
    if dep_ids:
        chain.append(
            CausalStep(
                step=f"{dep_ids[0].split(':', 1)[1]} shipped commit {top['sha'][:8]}.",
                cited_node_ids=[dep_ids[0], top_ref],
            )
        )
    if first:
        fn_changed = f"Function:{first['changed_function']}"
        fn_thrown = f"Function:{first['thrown_function']}"
        cited = [top_ref] + [n for n in (fn_changed, fn_thrown) if n in returned]
        claims.append(
            Claim(
                text=(
                    f"It changed {first['changed_function']}, linked by the call graph to "
                    f"{first['thrown_function']} where the error was thrown."
                ),
                cited_node_ids=list(dict.fromkeys(cited)),
            )
        )
        chain.append(
            CausalStep(
                step=f"The commit modified {first['changed_function']}.",
                cited_node_ids=[n for n in (top_ref, fn_changed) if n in returned] or [top_ref],
            )
        )
        hops = (path.get("paths") or [{}])[0].get("path") or []
        hop_ids = [f"Function:{f}" for f in hops if f"Function:{f}" in returned]
        if hop_ids:
            chain.append(
                CausalStep(
                    step="Call path to the failure: " + " -> ".join(hops) + ".",
                    cited_node_ids=hop_ids,
                )
            )
    if incident_ref:
        chain.append(
            CausalStep(step="The incident's errors were raised there.", cited_node_ids=incident_ref)
        )

    others = cands[1:3]
    if verdict == "ambiguous":
        tied = [c for c in cands if top["score"] - c["score"] <= max(margin, 0.0) + 1e-9]
        alternatives = [
            Alternative(
                sha=c["sha"],
                reason=f"ranked #{c['rank']} with score {c['score']}, within the ambiguity margin",
                cited_node_ids=[f"Commit:{c['sha']}"],
            )
            for c in (tied if len(tied) > 1 else cands[:2])
        ]
        return (
            Explanation(
                verdict="ambiguous",
                culprit_sha=None,
                claims=claims,
                causal_chain=chain,
                confidence_statement=(
                    f"The ranking is ambiguous (margin {margin}): Kaizen does not name a single "
                    "culprit. The candidates below are equally plausible."
                ),
                alternatives=alternatives,
                source="template",
            ),
            returned,
        )
    return (
        Explanation(
            verdict="confident",
            culprit_sha=top["sha"],
            culprit_function=first["changed_function"] if first else None,
            claims=claims,
            causal_chain=chain,
            confidence_statement=(
                f"The deterministic ranking is confident (confidence {top['confidence']}, "
                f"margin {margin})."
            ),
            alternatives=[
                Alternative(
                    sha=c["sha"],
                    reason=f"ranked #{c['rank']} with score {c['score']}",
                    cited_node_ids=[f"Commit:{c['sha']}"],
                )
                for c in others
            ],
            suggested_fix=f"revert {top['sha'][:7]}",
            source="template",
        ),
        returned,
    )
