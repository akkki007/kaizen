"""Citation validator: every citation must name a node a tool returned in this run that exists.

Applies to claims, causal_chain steps and alternatives. The explanation must also agree with the
deterministic ranking: never contradict a confident one, and abstain on an ambiguous one.
"""

from collections.abc import Iterable

from causaldeploy.agent.models import Explanation, ToolStep
from causaldeploy.graph.schema import NODE_KEYS
from causaldeploy.graph.store import GraphStore


def _cited(explanation: Explanation) -> Iterable[tuple[str, list[str]]]:
    for i, claim in enumerate(explanation.claims, start=1):
        yield f"claim {i}", claim.cited_node_ids
    for i, step in enumerate(explanation.causal_chain, start=1):
        yield f"causal_chain step {i}", step.cited_node_ids
    for i, alt in enumerate(explanation.alternatives, start=1):
        yield f"alternative {i}", alt.cited_node_ids


def validate(
    explanation: Explanation,
    steps: list[ToolStep],
    store: GraphStore,
    also_returned: Iterable[str] = (),
) -> list[str]:
    errors: list[str] = []
    returned = {nid for s in steps for nid in s.node_ids} | set(also_returned)
    exists: dict[str, bool] = {}
    for where, ids in _cited(explanation):
        for nid in ids:
            label, _, key = nid.partition(":")
            if label not in NODE_KEYS or not key:
                errors.append(f"{where}: '{nid}' is not a node id (expected Label:key)")
            elif nid not in returned:
                errors.append(f"{where}: '{nid}' was not returned by any tool call in this run")
            else:
                if nid not in exists:
                    exists[nid] = bool(
                        store.read(
                            f"MATCH (n:{label} {{{NODE_KEYS[label]}: $k}}) RETURN 1 LIMIT 1",
                            {"k": key},
                        )
                    )
                if not exists[nid]:
                    errors.append(f"{where}: '{nid}' does not exist in the graph")
    # the agent explains the deterministic ranking; it must not contradict a confident one
    ranked = [s for s in steps if s.name in ("rank_candidates", "get_candidates")]
    if ranked:
        res = ranked[-1].result
        cands = res.get("candidates") or []
        if (
            explanation.culprit_sha
            and res.get("verdict") == "confident"
            and cands
            and cands[0]["sha"] != explanation.culprit_sha
        ):
            errors.append(
                f"culprit_sha {explanation.culprit_sha} contradicts the confident ranking "
                f"(top is {cands[0]['sha']}); explain the ranking, do not override it"
            )
        if res.get("verdict") == "ambiguous":
            if explanation.verdict == "confident":
                errors.append(
                    "the ranking is ambiguous: set verdict 'ambiguous', state the ambiguity in "
                    "confidence_statement and list the tied candidates in alternatives"
                )
            elif not explanation.alternatives:
                errors.append("the ranking is ambiguous: list the tied candidates in alternatives")
    return errors
