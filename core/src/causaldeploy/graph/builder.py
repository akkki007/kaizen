"""Fluent builder for small, hand-checkable graphs (tests, eval fixtures, S1-S5 shapes)."""

from dataclasses import dataclass, field

from causaldeploy.graph.store import Edge, GraphStore


def fn_id(service: str, path: str, qualified_name: str) -> str:
    return f"{service}:{path}:{qualified_name}"


@dataclass
class Scenario:
    """A fixture graph plus its ground truth, usable by investigation and eval tests."""

    name: str
    incident_id: str
    culprit_commit: str
    culprit_functions: list[str]
    affected_services: list[str]
    commits: list[str] = field(default_factory=list)


class GraphBuilder:
    def __init__(self, store: GraphStore) -> None:
        self.store = store
        store.ensure_schema()

    def function(
        self,
        service: str,
        path: str,
        name: str,
        *,
        calls: list[str] | None = None,
        endpoint: str | None = None,
        deps: list[str] | None = None,
    ) -> str:
        fid = fn_id(service, path, name)
        file_id = f"{service}:{path}"
        s = self.store
        s.upsert_nodes("Service", [{"id": service}])
        s.upsert_nodes("File", [{"id": file_id, "path": path, "service": service}])
        s.upsert_nodes("Function", [{"id": fid, "name": name, "path": path, "service": service}])
        edges = [
            Edge("File", file_id, "contains", "Function", fid),
            Edge("Function", fid, "belongs_to", "Service", service),
        ]
        edges += [Edge("Function", fid, "calls", "Function", c) for c in calls or []]
        edges += [Edge("Function", fid, "depends_on", "Dependency", d) for d in deps or []]
        if endpoint:
            s.upsert_nodes("Endpoint", [{"id": f"{service}:{endpoint}", "route": endpoint}])
            edges += [
                Edge("Function", fid, "handles", "Endpoint", f"{service}:{endpoint}"),
                Edge("Endpoint", f"{service}:{endpoint}", "belongs_to", "Service", service),
            ]
        s.upsert_edges(edges)
        return fid

    def dependency(self, name: str, version: str) -> str:
        dep = f"{name}@{version}"
        self.store.upsert_nodes("Dependency", [{"id": dep, "name": name, "version": version}])
        return dep

    def commit(
        self,
        sha: str,
        ts: int,
        *,
        modifies_fn: list[str] | None = None,
        files: list[str] | None = None,
        changes_dep: list[str] | None = None,
        author: str = "dev",
        message: str = "",
    ) -> str:
        s = self.store
        s.upsert_nodes("Commit", [{"sha": sha, "ts": ts, "message": message}])
        s.upsert_nodes("Developer", [{"id": author}])
        edges = [Edge("Commit", sha, "authored_by", "Developer", author)]
        edges += [Edge("Commit", sha, "modifies_fn", "Function", f) for f in modifies_fn or []]
        edges += [Edge("Commit", sha, "modifies", "File", f) for f in files or []]
        edges += [Edge("Commit", sha, "changes_dep", "Dependency", d) for d in changes_dep or []]
        s.upsert_edges(edges)
        return sha

    def deployment(
        self, dep_id: str, env: str, commits: list[str], started_at: int, status: str = "success"
    ) -> str:
        s = self.store
        s.upsert_nodes(
            "Deployment", [{"id": dep_id, "env": env, "started_at": started_at, "status": status}]
        )
        s.upsert_nodes("Environment", [{"id": env}])
        s.upsert_edges(
            [Edge("Deployment", dep_id, "deployed_to", "Environment", env)]
            + [Edge("Deployment", dep_id, "includes", "Commit", c) for c in commits]
        )
        return dep_id

    def error(
        self,
        fingerprint: str,
        *,
        thrown_in: list[str],
        endpoint: str | None = None,
        message: str = "",
        count: int = 1,
    ) -> str:
        s = self.store
        s.upsert_nodes("Error", [{"fingerprint": fingerprint, "message": message, "count": count}])
        edges = [
            Edge("Error", fingerprint, "thrown_in", "Function", f, {"frame_depth": i})
            for i, f in enumerate(thrown_in)
        ]
        if endpoint:
            edges.append(Edge("Error", fingerprint, "observed_on", "Endpoint", endpoint))
        s.upsert_edges(edges)
        return fingerprint

    def incident(
        self,
        inc_id: str,
        *,
        service: str,
        deployment: str | None = None,
        errors: list[str] | None = None,
        status: str = "open",
        detected_at: int = 0,
    ) -> str:
        s = self.store
        s.upsert_nodes("Incident", [{"id": inc_id, "status": status, "detected_at": detected_at}])
        edges = [Edge("Incident", inc_id, "affects", "Service", service)]
        edges += [Edge("Incident", inc_id, "evidenced_by", "Error", e) for e in errors or []]
        if deployment:
            edges.append(Edge("Deployment", deployment, "triggers", "Incident", inc_id))
        s.upsert_edges(edges)
        return inc_id

    def candidates(self, inc_id: str, ranked: list[tuple[str, float]]) -> None:
        """Persist the full ranked candidate list; top-1 also gets `likely_caused_by`."""
        self.store.delete_edges("Incident", inc_id, "candidate")
        self.store.delete_edges("Incident", inc_id, "likely_caused_by")
        edges = [
            Edge("Incident", inc_id, "candidate", "Commit", sha, {"rank": i + 1, "score": score})
            for i, (sha, score) in enumerate(ranked)
        ]
        if ranked:
            edges.append(Edge("Incident", inc_id, "likely_caused_by", "Commit", ranked[0][0]))
        self.store.upsert_edges(edges)

    # -- S1-S5 shapes (PLAN.md §10) -------------------------------------------
    def s1_payment_validation(self) -> Scenario:
        v = self.function("payments", "src/payment/validation.js", "validatePayment")
        h = self.function(
            "payments", "src/routes.js", "handlePayment", calls=[v], endpoint="POST /payments"
        )
        noise = self.commit("n0000001", 100, files=["docs:README.md"], message="docs")
        bad = self.commit("c0ffee01", 200, modifies_fn=[v], message="Refactor payment validation")
        self.deployment("dep-842", "prod", [noise, bad], 300)
        self.error("fp-undefined-currency", thrown_in=[v, h], endpoint="payments:POST /payments")
        self.incident(
            "inc-1", service="payments", deployment="dep-842", errors=["fp-undefined-currency"]
        )
        return Scenario("S1", "inc-1", bad, [v], ["payments"], [noise, bad])

    def s2_two_functions_one_file(self) -> Scenario:
        path = "src/payment/validation.js"
        v = self.function("payments", path, "validatePayment")
        r = self.function("payments", path, "formatReceipt")
        h = self.function(
            "payments", "src/routes.js", "handlePayment", calls=[v, r], endpoint="POST /payments"
        )
        decoy = self.commit("d3c0y001", 200, modifies_fn=[r], message="tidy receipt format")
        bad = self.commit("c0ffee02", 210, modifies_fn=[v], message="stricter validation")
        self.deployment("dep-2", "prod", [decoy, bad], 300)
        self.error("fp-s2", thrown_in=[v, h], endpoint="payments:POST /payments")
        self.incident("inc-2", service="payments", deployment="dep-2", errors=["fp-s2"])
        return Scenario("S2", "inc-2", bad, [v], ["payments"], [decoy, bad])

    def s3_five_commit_deployment(self) -> Scenario:
        v = self.function("payments", "src/payment/validation.js", "validatePayment")
        h = self.function(
            "payments", "src/routes.js", "handlePayment", calls=[v], endpoint="POST /payments"
        )
        other = [self.function("gateway", f"src/m{i}.js", f"helper{i}") for i in range(4)]
        shas = [
            self.commit(f"a000000{i}", 100 + i, modifies_fn=[other[i]], message=f"noise {i}")
            for i in range(4)
        ]
        bad = self.commit("c0ffee03", 150, modifies_fn=[v], message="refactor validation")
        self.deployment("dep-3", "prod", [*shas, bad], 300)
        self.error("fp-s3", thrown_in=[v, h])
        self.incident("inc-3", service="payments", deployment="dep-3", errors=["fp-s3"])
        return Scenario("S3", "inc-3", bad, [v], ["payments"], [*shas, bad])

    def s4_cross_service(self) -> Scenario:
        validate = self.function("payments", "src/validate.js", "validateCharge")
        self.function(
            "payments", "src/routes.js", "handleCharge", calls=[validate], endpoint="POST /charge"
        )
        create = self.function("orders", "src/orders.js", "createOrder", calls=[validate])
        self.function(
            "gateway", "src/routes.js", "postOrder", calls=[create], endpoint="POST /orders"
        )
        bad = self.commit("c0ffee04", 200, modifies_fn=[create], message="send amount in cents")
        self.deployment("dep-4", "prod", [bad], 300)
        self.error("fp-s4", thrown_in=[validate], endpoint="payments:POST /charge")
        self.incident("inc-4", service="payments", deployment="dep-4", errors=["fp-s4"])
        return Scenario("S4", "inc-4", bad, [create], ["payments", "orders", "gateway"], [bad])

    def s5_dependency_bump(self) -> Scenario:
        new = self.dependency("money-utils", "2.0.0")
        fmt = self.function("payments", "src/money.js", "toCents", deps=[new])
        h = self.function(
            "payments", "src/routes.js", "handlePayment", calls=[fmt], endpoint="POST /payments"
        )
        self.function("orders", "src/total.js", "orderTotal", deps=[new])
        bad = self.commit(
            "c0ffee05", 200, files=["payments:package.json"], changes_dep=[new], message="bump"
        )
        self.deployment("dep-5", "prod", [bad], 300)
        self.error("fp-s5", thrown_in=[fmt, h])
        self.incident("inc-5", service="payments", deployment="dep-5", errors=["fp-s5"])
        return Scenario("S5", "inc-5", bad, [fmt], ["payments", "orders"], [bad])
