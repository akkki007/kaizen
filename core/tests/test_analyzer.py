import time

import pytest

from causaldeploy.graph.store import GraphStore
from causaldeploy.ingest.analyzer.javascript import JavaScriptAnalyzer
from causaldeploy.ingest.code_graph import build_code_graph, write_code_graph
from tests.shoplite_files import load

V = "payments:payments/src/payment/validation.js:validatePayment"
R = "payments:payments/src/routes.js:handlePayment"
TOCENTS = "money-utils:lib/money-utils/src/index.js:toCents"


def test_file_analysis_functions_and_lines() -> None:
    src = (
        "function a() {\n  return b();\n}\n"
        "const c = (x) => { a(); };\n"
        "class K {\n  m() { return this.n(); }\n  n() {}\n}\n"
        "exports.d = function () { c(); };\n"
    )
    f = JavaScriptAnalyzer().analyze_file("x.js", src)
    assert [(x.qname, x.start, x.end) for x in f.functions] == [
        ("a", 1, 3),
        ("c", 4, 4),
        ("K.m", 6, 6),
        ("K.n", 7, 7),
        ("d", 9, 9),
    ]
    assert ("K.m", "K.n") in f.calls and ("a", "b") in f.calls and ("d", "c") in f.calls


def test_typescript_and_imports() -> None:
    src = "import d, { e as f } from './y';\nimport * as ns from './z';\nexport const g = (a: number): number => f(a) + ns.h(a);\n"
    out = JavaScriptAnalyzer().analyze_file("a.ts", src)
    assert out.imports["f"].name == "e" and out.imports["ns"].name is None
    assert [fn.qname for fn in out.functions] == ["g"]
    assert {("g", "f"), ("g", "ns.h")} <= set(out.calls)


def test_syntax_errors_do_not_raise() -> None:
    assert JavaScriptAnalyzer().analyze_file("bad.js", "function (((").functions == []


@pytest.fixture(scope="module")
def cg():  # type: ignore[no-untyped-def]
    return build_code_graph(load())


def test_shoplite_functions_exact(cg) -> None:  # type: ignore[no-untyped-def]
    assert sorted(cg.functions) == [
        "gateway:gateway/src/server.js:POST /orders",
        "money-utils:lib/money-utils/src/index.js:formatMoney",
        TOCENTS,
        "orders:orders/src/orders.ts:OrderService.create",
        "orders:orders/src/orders.ts:OrderService.total",
        "orders:orders/src/orders.ts:createOrder",
        "payments:payments/src/payment/validation.js:formatReceipt",
        V,
        "payments:payments/src/routes.js:GET /health",
        R,
    ]


def test_shoplite_calls_routes_deps_exact(cg) -> None:  # type: ignore[no-untyped-def]
    assert cg.calls == {
        ("gateway:gateway/src/server.js:POST /orders", "orders:orders/src/orders.ts:createOrder"),
        (
            "orders:orders/src/orders.ts:OrderService.create",
            "orders:orders/src/orders.ts:OrderService.total",
        ),
        ("orders:orders/src/orders.ts:OrderService.create", V),
        ("orders:orders/src/orders.ts:OrderService.total", TOCENTS),
        (V, TOCENTS),
        (R, "payments:payments/src/payment/validation.js:formatReceipt"),
        (R, V),
    }
    assert cg.handles == {
        (R, "payments:POST /payments"),
        ("payments:payments/src/routes.js:GET /health", "payments:GET /health"),
        ("gateway:gateway/src/server.js:POST /orders", "gateway:POST /orders"),
    }
    assert cg.service_deps["payments"]["money-utils"] == "2.0.0"
    assert (V, "money-utils") in cg.fn_deps


def test_analysis_is_fast() -> None:
    t = time.perf_counter()
    build_code_graph(load())
    assert time.perf_counter() - t < 10


def test_write_code_graph_is_idempotent(store: GraphStore, cg) -> None:  # type: ignore[no-untyped-def]
    write_code_graph(store, cg, "abc123")
    first = store.counts()
    write_code_graph(store, cg, "abc123")
    assert store.counts() == first
    rows = store.read(
        "MATCH (f:Function {id: $id})-[:depends_on]->(d:Dependency) RETURN d.id", {"id": V}
    )
    assert rows == [["money-utils@2.0.0"]]


def test_internal_package_versions_come_from_the_tree(cg) -> None:  # type: ignore[no-untyped-def]
    # payments declares "^2.0.0" but the workspace package itself is 2.0.0 in the fixture
    assert cg.service_deps["payments"]["money-utils"] == "2.0.0"
    assert cg.service_deps["orders"]["money-utils"] == "2.0.0"
