"""
Game mathematics for the CoGNETs Swarm Arena.

This module is a deliberately small, readable reduction of the CoGNETs
four-stage game pipeline (Nash Bargaining -> Kelly/SBRD -> Blotto -> CO-Matching).

What is implemented here
------------------------
Stage 1  Bargaining-derived prices ... congestion price lambda_k, published per round
Stage 2  Kelly proportional allocation  x_i^k = C_k * b_i^k / sum_j b_j^k
Stage 2' CES utility ..............     u_i = (sum_k w_i^k (x_i^k)^rho)^(1/rho)
         plus the CoGNETs admissibility floors (q_min on compute, s_min on security)

Stages 3 and 4 are out of scope for a two-day challenge. See
docs/cognets-mapping.md for how they would slot in.

Every function here is pure and deterministic given its inputs, so a whole
tournament can be replayed exactly from its seed.

Copyright 2026 The CoGNETs Consortium
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import math
from typing import Dict, List, Mapping, Tuple

#: The three shared resource pools the swarm bids over.
RESOURCES: Tuple[str, ...] = ("compute", "energy", "security")

#: CES substitution parameter. rho in (0, 1) gives elasticity sigma = 1/(1-rho).
#: rho = 0.5 -> sigma = 2: resources substitute for one another, but a bundle
#: piled onto a single dimension is still worth measurably less than a
#: balanced one, so the optimum is interior rather than at a corner.
RHO: float = 0.5

EPS: float = 1e-9


# ---------------------------------------------------------------------------
# Stage 1 - prices
# ---------------------------------------------------------------------------
def clearing_prices(
    bids: Mapping[str, Mapping[str, float]],
    capacities: Mapping[str, float],
) -> Dict[str, float]:
    """Congestion price per resource: ``lambda_k = sum_j b_j^k / C_k``.

    In the full CoGNETs pipeline this signal comes out of the Stage 1 Nash
    Bargaining Solution in closed form. Here we simply publish the price that
    actually cleared the previous round, which plays the same role: it is the
    market information an agent best-responds to in Stage 2.

    Returns 0.0 for a resource nobody bid on. The caller is responsible for
    flooring the published value so agents never see a zero price.
    """
    prices: Dict[str, float] = {}
    for k in RESOURCES:
        total = sum(float(b.get(k, 0.0)) for b in bids.values())
        prices[k] = total / max(float(capacities.get(k, 1.0)), EPS)
    return prices


# ---------------------------------------------------------------------------
# Stage 2 - Kelly proportional allocation
# ---------------------------------------------------------------------------
def kelly_allocation(
    bids: Mapping[str, Mapping[str, float]],
    capacities: Mapping[str, float],
) -> Dict[str, Dict[str, float]]:
    """Kelly mechanism: each node's share of a pool is its share of the bids.

        x_i^k = C_k * b_i^k / (sum_j b_j^k)

    Three properties that matter for the challenge:

    * The allocation is always feasible - ``sum_i x_i^k == C_k`` whenever
      anyone bid at all.
    * Returns diminish, because the denominator contains your own bid. Doubling
      your bid never doubles your share.
    * Because your own bid sits in the denominator, the single-round problem is
      a concave programme with a unique interior solution. Working out that
      solution is a pleasant exercise and, as the strategy primer warns, worth
      very little. The score is elsewhere.
    """
    alloc: Dict[str, Dict[str, float]] = {
        node_id: {k: 0.0 for k in RESOURCES} for node_id in bids
    }
    for k in RESOURCES:
        pool = float(capacities.get(k, 1.0))
        total = sum(float(b.get(k, 0.0)) for b in bids.values())
        if total <= EPS:
            continue
        for node_id, b in bids.items():
            alloc[node_id][k] = pool * float(b.get(k, 0.0)) / total
    return alloc


def ces_utility(
    allocation: Mapping[str, float],
    weights: Mapping[str, float],
    rho: float = RHO,
) -> float:
    """Constant Elasticity of Substitution utility over an allocated bundle.

        u = ( sum_k w_k * (x_k)^rho ) ^ (1/rho)

    Concave and increasing in every dimension, so the Stage 2 bidding problem
    is a well-posed concave programme with a unique interior solution.
    """
    acc = 0.0
    for k in RESOURCES:
        x = max(float(allocation.get(k, 0.0)), 0.0)
        w = max(float(weights.get(k, 0.0)), 0.0)
        if x > 0.0 and w > 0.0:
            acc += w * (x ** rho)
    if acc <= 0.0:
        return 0.0
    return acc ** (1.0 / rho)


def apply_floors(
    utility: float,
    allocation: Mapping[str, float],
    q_min: float,
    s_min: float,
) -> Tuple[float, List[str]]:
    """Apply the CoGNETs admissibility floors (Definition 2.1, adapted).

    A node that fails to secure its minimum QoS share (on compute) or its
    minimum security share does not keep full value from the round. Rather than
    zeroing the utility - which would make the leaderboard brutally noisy and
    punish one bad round far out of proportion - we halve it per violated
    floor. Sharp enough to shape behaviour, forgiving enough to recover from.

    Returns ``(adjusted_utility, violations)``.
    """
    violations: List[str] = []
    factor = 1.0
    if float(allocation.get("compute", 0.0)) + EPS < q_min:
        violations.append("qos_floor")
        factor *= 0.5
    if float(allocation.get("security", 0.0)) + EPS < s_min:
        violations.append("security_floor")
        factor *= 0.5
    return utility * factor, violations


def sanitise_bid(
    raw: Mapping[str, object],
    budget: float,
) -> Tuple[Dict[str, float], List[str]]:
    """Validate and clamp a submitted bid vector.

    Never raises: a malformed bid becomes a usable one plus a list of warnings.
    Warnings drive the node's compromise score kappa, so repeated protocol
    abuse gets a node ejected - which mirrors the CoGNETs admission gate and,
    more practically, teaches teams to validate before they send.

    Returns ``(clean_bid, warnings)``.
    """
    warnings: List[str] = []
    clean: Dict[str, float] = {}

    for k in RESOURCES:
        value = raw.get(k, 0.0)
        try:
            v = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            warnings.append(f"non_numeric_bid:{k}")
            v = 0.0
        if math.isnan(v) or math.isinf(v):
            warnings.append(f"invalid_bid:{k}")
            v = 0.0
        elif v < 0.0:
            warnings.append(f"negative_bid:{k}")
            v = 0.0
        clean[k] = v

    extra = sorted(set(raw) - set(RESOURCES))
    if extra:
        # Free warning: curiosity is not a protocol violation.
        warnings.append("unknown_resource:" + ",".join(extra))

    total = sum(clean.values())
    if total > budget + 1e-6:
        warnings.append("over_budget")
        scale = budget / max(total, EPS)
        clean = {k: v * scale for k, v in clean.items()}

    return clean, warnings


# ---------------------------------------------------------------------------
# Swarm-level metric
# ---------------------------------------------------------------------------
def log_social_welfare(utilities: Mapping[str, float]) -> float:
    """``LSW = sum_i log(u_i)`` - the headline efficiency metric of the CoGNETs
    pipeline.

    Reported every round so teams can see whether their strategy makes the
    whole swarm better off or merely takes from its neighbours. It carries no
    points of its own, and measuring your own effect on it is worth writing up:
    in this reduction the two usually pull against each other.
    """
    return sum(math.log(max(float(u), 1e-6)) for u in utilities.values())
