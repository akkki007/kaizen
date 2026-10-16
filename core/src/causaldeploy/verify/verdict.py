"""Verification verdict (#91): a pure function of the measured metrics and the tolerances.

Everything that goes in is stored next to the verdict (`result.verification`), so the decision
can be recomputed later from the record alone: `verdict(**stored_inputs)` == stored verdict.

Rules (all must hold to pass):
- the verification workflow concluded successfully and the tests passed (when reported);
- the fixed run has an error rate, from at least `min_samples` requests (when reported);
- fixed error rate <= baseline error rate + `error_tolerance`;
- fixed p95 <= baseline p95 + `p95_tolerance_ms` (only when both p95 values are known).
The failing run is recorded for the before/after comparison; it does not change the outcome.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict


class Metrics(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    error_rate: float | None = None
    p95_ms: float | None = None
    samples: int | None = None  # requests measured

    @classmethod
    def parse(cls, raw: Any) -> "Metrics | None":
        if not isinstance(raw, dict):
            return None
        samples = raw.get("samples", raw.get("requests"))
        return cls(
            error_rate=_num(raw.get("error_rate")),
            p95_ms=_num(raw.get("p95_ms")),
            samples=int(samples) if isinstance(samples, int | float) else None,
        )


class VerdictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    error_tolerance: float = 0.02
    p95_tolerance_ms: float = 250.0
    min_samples: int = 20

    @classmethod
    def from_settings(cls) -> "VerdictConfig":
        from causaldeploy.config import get_settings

        s = get_settings()
        return cls(
            error_tolerance=s.verify_error_tolerance,
            p95_tolerance_ms=s.verify_p95_tolerance_ms,
            min_samples=s.verify_min_samples,
        )


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    checks: dict[str, bool | None]  # None: not evaluated (input missing)
    reasons: list[str]


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def verdict(
    failing: Metrics | None,
    fixed: Metrics | None,
    baseline: Metrics | None,
    cfg: VerdictConfig,
    *,
    workflow_ok: bool = True,
    tests_passed: bool | None = None,
) -> Verdict:
    checks: dict[str, bool | None] = {}
    reasons: list[str] = []
    checks["workflow"] = workflow_ok
    if not workflow_ok:
        reasons.append("verification workflow did not succeed")
    checks["tests"] = tests_passed
    if tests_passed is False:
        reasons.append("tests failed")

    after = fixed.error_rate if fixed else None
    base_rate = (baseline.error_rate if baseline else None) or 0.0
    if after is None:
        checks["error_rate"] = False
        reasons.append("no error rate measured for the fixed build")
    else:
        ok = after <= base_rate + cfg.error_tolerance
        checks["error_rate"] = ok
        if not ok:
            reasons.append(
                f"error rate {after:.3f} > baseline {base_rate:.3f} + {cfg.error_tolerance:.3f}"
            )

    samples = fixed.samples if fixed else None
    if samples is None or cfg.min_samples == 0:
        checks["samples"] = None
    else:
        checks["samples"] = samples >= cfg.min_samples
        if not checks["samples"]:
            reasons.append(f"only {samples} requests measured (< {cfg.min_samples})")

    p95 = fixed.p95_ms if fixed else None
    base_p95 = baseline.p95_ms if baseline else None
    if p95 is None or base_p95 is None:
        checks["p95"] = None
    else:
        checks["p95"] = p95 <= base_p95 + cfg.p95_tolerance_ms
        if not checks["p95"]:
            reasons.append(
                f"p95 {p95:.0f}ms > baseline {base_p95:.0f}ms + {cfg.p95_tolerance_ms:.0f}ms"
            )

    if failing and failing.error_rate is not None and after is not None:
        checks["improved"] = after < failing.error_rate or failing.error_rate <= base_rate
    else:
        checks["improved"] = None

    gating = ("workflow", "tests", "error_rate", "samples", "p95")
    passed = all(checks[k] is not False for k in gating)
    return Verdict(passed=passed, checks=checks, reasons=reasons)


def recompute(stored: dict[str, Any]) -> Verdict:
    """Re-derive the verdict from a stored `result.verification` record."""
    inputs = stored["inputs"]
    return verdict(
        Metrics.parse(inputs.get("failing")),
        Metrics.parse(inputs.get("fixed")),
        Metrics.parse(inputs.get("baseline")),
        VerdictConfig(**stored["config"]),
        workflow_ok=bool(inputs.get("workflow_ok")),
        tests_passed=inputs.get("tests_passed"),
    )
