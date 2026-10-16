# Weight fitting (tune split only)

`python -m causaldeploy.evalharness.fit` from `v1`: coordinate search over 8 grid values per feature, 3 passes, strict improvement of (top-1 hits - 2 x confident misses, top-3 hits, scale-free margin) on 10 tune variants. Test-split cases are rejected by a guard before the fitter sees any data (`tests/test_fit.py`).

Frozen (constant across candidates in every tune case, so not identifiable): `config`, `sequence`, `temporal`, `triggered`.

Objective (10, 10, 0.130893) -> (10, 10, 0.274286). Changed: `path` +0.25 -> +0.40, `endpoint` +0.15 -> +0.00, `service` +0.10 -> +0.00, `centrality` +0.05 -> +0.00, `dependency` +0.10 -> +0.40, `msg_sim` +0.05 -> +0.00, `history` +0.10 -> +0.40, `blast` -0.10 -> +0.00.

| weights | split | n | top-1 | top-3 | false attribution |
|---|---|---|---|---|---|
| base | tune | 10 | 100% | 100% | 0% |
| base | test | 18 | 83% | 100% | 0% |
| fitted | tune | 10 | 100% | 100% | 0% |
| fitted | test | 18 | 56% | 100% | 0% |

**Decision.** Held-out results are worse with the fitted weights (top-1 56% vs 83%), so the default stays `v1` and no new weights file is written. Where the tune split is already saturated, the margin term is all that is left to optimize; it moves weight onto the few features the tune scenarios exercise and away from evidence the held-out scenarios need. The held-out split catches that.
