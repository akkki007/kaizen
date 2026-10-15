"""Citation validator: every claim must cite nodes a tool returned in this run and that exist."""

from causaldeploy.agent.models import Explanation, ToolStep
from causaldeploy.graph.schema import NODE_KEYS
from causaldeploy.graph.store import GraphStore


def validate(explanation: Explanation, steps: list[ToolStep], store: GraphStore) -> list[str]:
    errors: list[str] = []
    returned = {nid for s in steps for nid in s.node_ids}
    for i, claim in enumerate(explanation.claims, start=1):
        for nid in claim.cited_node_ids:
            label, _, key = nid.partition(":")
            if label not in NODE_KEYS or not key:
                errors.append(f"claim {i}: '{nid}' is not a node id (expected Label:key)")
            elif nid not in returned:
                errors.append(f"claim {i}: '{nid}' was not returned by any tool call in this run")
            elif not store.read(
                f"MATCH (n:{label} {{{NODE_KEYS[label]}: $k}}) RETURN 1 LIMIT 1", {"k": key}
            ):
                errors.append(f"claim {i}: '{nid}' does not exist in the graph")
    # the agent explains the deterministic ranking; it must not contradict a confident one
    ranked = [s for s in steps if s.name == "rank_candidates"]
    if ranked and explanation.culprit_sha:
        res = ranked[-1].result
        cands = res.get("candidates") or []
        if (
            res.get("verdict") == "confident"
            and cands
            and cands[0]["sha"] != explanation.culprit_sha
        ):
            errors.append(
                f"culprit_sha {explanation.culprit_sha} contradicts the confident ranking "
                f"(top is {cands[0]['sha']}); explain the ranking, do not override it"
            )
    return errors
