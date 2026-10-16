"""Builds one graph per scenario variant from the ShopLite scenario ground truth.

This is a *replay* benchmark: the culprit, the failing function and the affected services come
from `eval/scenarios/S*.yaml` (copied from the ShopLite repo, verified there against live load),
and each variant is rendered into the graph shape CausalDeploy would have ingested, with
realistic distractors (a later unrelated commit, a keyword-bait commit, a docs commit, a sibling
function edit). Culprit position in the deployment rotates with the variant so the latest-commit
baseline is right only some of the time.
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.graph.store import Edge

SCENARIO_DIR = Path(__file__).resolve().parents[4] / "eval" / "scenarios"

# Known functions of ShopLite, so non-culprit commits touch plausible code.
FN = {
    "gateway_post": "gateway:services/gateway/src/app.js:POST /orders",
    "orders_post": "orders:services/orders/src/app.js:POST /orders",
    "orders_price": "orders:services/orders/src/pricing.js:priceOrder",
    "orders_line": "orders:services/orders/src/pricing.js:lineCents",
    "pay_post": "payments:services/payments/src/app.js:POST /payments",
    "pay_validate": "payments:services/payments/src/payment/validation.js:validatePayment",
    "pay_amount": "payments:services/payments/src/payment/validation.js:validateAmount",
    "pay_currency": "payments:services/payments/src/payment/validation.js:validateCurrency",
    "pay_method": "payments:services/payments/src/payment/validation.js:validateMethod",
    "m_cents": "money-utils:lib/money-utils/src/index.js:toCents",
    "m_format": "money-utils:lib/money-utils/src/index.js:formatMoney",
}
CALLS = [
    ("orders_post", "orders_price"),
    ("orders_price", "orders_line"),
    ("orders_line", "m_cents"),
    ("pay_post", "pay_validate"),
    ("pay_validate", "pay_amount"),
    ("pay_validate", "pay_currency"),
    ("pay_validate", "pay_method"),
    ("pay_validate", "m_cents"),
    ("pay_post", "m_format"),
]
HANDLES = {
    "gateway_post": "gateway:POST /orders",
    "orders_post": "orders:POST /orders",
    "pay_post": "payments:POST /payments",
}
T_DEPLOY, T_ONSET = 10_000, 10_090


@dataclass
class Case:
    scenario: str
    variant: int
    title: str
    split: str
    incident_id: str
    culprit_sha: str | None  # None: not a code change (S6)
    culprit_function: str | None
    affected_services: list[str]
    code_bug: bool
    commits: list[str] = field(default_factory=list)
    resembles: bool = False
    culprit_id: str | None = None  # key of the true suspect of any kind (sha or config id)
    fix_verified: bool | None = None  # only when the scenario reports a verification outcome


def load_scenarios() -> list[dict[str, Any]]:
    return [
        yaml.safe_load(p.read_text())
        for p in sorted(SCENARIO_DIR.glob("S*.yaml"), key=lambda p: int(p.stem[1:]))
    ]


def _sha(role: str, tag: str) -> str:
    """Neutral, deterministic commit id. Names like "c<tag>" for the culprit would sort first
    and win every score tie (the scorer breaks ties by key), flattering any method that ties."""
    return hashlib.sha1(f"{role}:{tag}".encode()).hexdigest()[:12]


def _service_of(fn: str) -> str:
    return fn.split(":", 1)[0]


def _ensure_fn(b: GraphBuilder, fn: str) -> None:
    service, path, name = fn.split(":", 2)
    b.store.upsert_nodes("Service", [{"id": service}])
    b.store.upsert_nodes("File", [{"id": f"{service}:{path}", "path": path, "service": service}])
    b.store.upsert_nodes("Function", [{"id": fn, "name": name, "path": path, "service": service}])
    b.store.upsert_edges(
        [
            Edge("File", f"{service}:{path}", "contains", "Function", fn),
            Edge("Function", fn, "belongs_to", "Service", service),
        ]
    )


def build_case(b: GraphBuilder, spec: dict[str, Any], variant: int) -> Case:
    sid = spec["id"]
    gt = spec["ground_truth"]
    code_bug = gt.get("code_bug", True) is not False
    culprit_fn = gt.get("culprit_function")
    store = b.store
    for fn in FN.values():
        _ensure_fn(b, fn)
    if culprit_fn:
        _ensure_fn(b, culprit_fn)
    store.upsert_edges([Edge("Function", FN[a], "calls", "Function", FN[c]) for a, c in CALLS])
    for key, ep in HANDLES.items():
        svc = _service_of(FN[key])
        store.upsert_nodes("Endpoint", [{"id": ep, "route": ep.split(":", 1)[1]}])
        store.upsert_edges(
            [
                Edge("Function", FN[key], "handles", "Endpoint", ep),
                Edge("Endpoint", ep, "belongs_to", "Service", svc),
            ]
        )
    store.upsert_edges(
        [
            Edge("Service", "gateway", "calls", "Service", "orders"),
            Edge("Service", "orders", "calls", "Service", "payments"),
        ]
    )

    tag = f"{sid.lower()}v{variant}"
    culprit_sha = _sha("c", tag) if code_bug else None
    # where the failure is observed
    failing_service = "payments"
    failing_endpoint = "POST /payments"
    thrown: list[str] = [culprit_fn] if culprit_fn else []
    if sid == "S4":
        thrown = []  # a 422 response, not an exception: only the endpoint is observed
    if sid in ("S3", "S7", "S8", "S10"):
        failing_service, failing_endpoint = "orders", "POST /orders"
    if not code_bug:
        # S6: orders can no longer reach payments. The ground truth lists orders and gateway as
        # affected (payments itself is healthy), so the failure is observed on orders' endpoint.
        failing_service, failing_endpoint = "orders", "POST /orders"
    if sid == "S5":
        thrown = [FN["m_format"], FN["pay_post"]]
    elif thrown and culprit_fn and failing_service == "payments" and culprit_fn != FN["pay_post"]:
        thrown.append(FN["pay_post"])
    elif thrown and failing_service == "orders" and culprit_fn != FN["orders_post"]:
        thrown.append(FN["orders_post"])

    # candidate commits of the deployment, rotated so the culprit is not always the latest
    busy = {f for f in [culprit_fn, *thrown] if f}
    pool = [
        f
        for f in (
            FN["gateway_post"],
            FN["orders_price"],
            FN["pay_method"],
            FN["m_format"],
            FN["orders_line"],
            FN["pay_amount"],
            FN["orders_post"],
        )
        if f not in busy
    ]
    keywords = "fix currency amount validation edge case in payment handler"
    bait = (
        variant % 2 == 0
    )  # even variants: a decoy mentions the error words; odd: the culprit does
    noise: list[tuple[str, str, list[str], list[str]]] = [  # (sha, message, functions, files)
        (_sha("d", tag), "docs: update README", [], ["README.md"]),
        (_sha("k", tag), keywords if bait else "chore: bump logging", [pool[0]], []),
        (_sha("r", tag), "refactor: tidy helpers", [pool[1 % len(pool)]], []),
        (_sha("g", tag), "chore: log request ids", [pool[2 % len(pool)]], []),
    ]
    if sid == "S2":  # sibling function of the culprit in the same file
        noise.append((_sha("s", tag), "refactor: normalise payment method", [FN["pay_method"]], []))
    if sid == "S3":
        noise.append((_sha("n1", tag), "chore: tweak constants", [pool[3 % len(pool)]], []))
    if sid == "S10":  # the unrelated flaky endpoint change
        noise.append(
            (
                _sha("f", tag),
                "feat(gateway): serve /orders from a short-lived cache",
                [FN["gateway_post"]],
                [],
            )
        )

    if culprit_fn:
        culprit_msg = (
            keywords if (code_bug and not bait) else f"change in {culprit_fn.split(':')[-1]}"
        )
    else:
        culprit_msg = "chore(deploy): config change"
    entries: list[tuple[str, str, list[str], list[str]]] = list(noise)
    pos = variant % 3  # 0 -> culprit is the latest commit, 1 -> middle, 2 -> earliest
    cm = (
        culprit_sha or _sha("x", tag),
        culprit_msg,
        [culprit_fn] if (culprit_fn and code_bug) else [],
        ["docker-compose.prod.yml"] if not code_bug else [],
    )
    idx = {0: len(entries), 1: len(entries) // 2, 2: 0}[pos]
    entries.insert(idx, cm)

    shas = []
    for i, (sha, msg, fns, files) in enumerate(entries):
        b.commit(sha, T_DEPLOY - 1000 + i * 10, modifies_fn=fns, files=files or None, message=msg)
        shas.append(sha)
    if (
        sid == "S5"
    ):  # dependency bump: the commit changes money-utils, a dependency of the failing code
        dep = b.dependency("money-utils", f"2.{variant}.0")
        store.upsert_edges(
            [
                Edge("Commit", culprit_sha or "", "changes_dep", "Dependency", dep),
                Edge("Function", FN["m_format"], "depends_on", "Dependency", dep),
                Edge("Function", FN["pay_post"], "depends_on", "Dependency", dep),
            ]
        )
    if sid == "S7":  # latent: culprit deployed long before; a later unrelated deploy carries decoys
        early = culprit_sha or ""
        late = [s for s in shas if s != early]
        b.deployment("dep-early", "prod", [early], T_DEPLOY - 5000)
        b.deployment("dep-late", "prod", late, T_DEPLOY - 100)
        deps_trigger = "dep-early"  # the signature first appears on a traffic shift, not a deploy
    else:
        b.deployment("dep-1", "prod", shas, T_DEPLOY)
        deps_trigger = "dep-1"
    config_id = None
    overrides: dict[str, dict[str, Any]] = spec.get("env_override") or {}
    if not code_bug and overrides:  # the deployment also ships the changed env value
        target = sorted(overrides)[0]
        key = sorted(overrides[target])[0]
        config_id = b.config_change(
            "dep-1",
            target,
            key,
            file=gt.get("culprit_file") or "",
            commit=culprit_sha or _sha("x", tag),
        )

    if thrown:
        err_msg = "currency amount validation failed in payment handler"
    elif not code_bug:
        err_msg = "fetch failed: connect ECONNREFUSED"
    else:
        err_msg = "amount mismatch"
    err = b.error(
        f"fp-{tag}",
        thrown_in=thrown,
        message=err_msg,
        service=failing_service,
        first_seen=T_ONSET,
        endpoint=f"{failing_service}:{failing_endpoint}",
    )
    if sid == "S10":  # unrelated flaky signature on another function
        b.error(
            f"fp-flaky-{tag}",
            thrown_in=[FN["gateway_post"]],
            message="orders cache backend timeout",
            service="gateway",
            first_seen=T_ONSET,
        )
    inc = b.incident(
        f"inc-{tag}",
        service=failing_service,
        deployment=deps_trigger,
        errors=[err] + ([f"fp-flaky-{tag}"] if sid == "S10" else []),
        onset_ts=T_ONSET,
        endpoint=failing_endpoint,
    )
    if sid == "S8" and culprit_fn:  # recurrence: a resolved incident blamed the same function
        old = b.commit(
            _sha("old", tag), 100, modifies_fn=[culprit_fn], message="earlier regression"
        )
        store.upsert_nodes("Incident", [{"id": f"old-{tag}", "status": "resolved"}])
        store.upsert_edges(
            [
                Edge("Incident", f"old-{tag}", "likely_caused_by", "Commit", old),
                Edge("Incident", inc, "resembles", "Incident", f"old-{tag}", {"similarity": 0.9}),
            ]
        )
    return Case(
        scenario=sid,
        variant=variant,
        title=spec["title"],
        split=spec["split"],
        incident_id=inc,
        culprit_sha=culprit_sha,
        culprit_function=culprit_fn if code_bug else None,
        affected_services=list(gt.get("affected_services", [])),
        code_bug=code_bug,
        commits=shas,
        resembles=sid == "S8",
        culprit_id=culprit_sha or config_id,
        fix_verified=gt.get("fix_verified"),
    )
