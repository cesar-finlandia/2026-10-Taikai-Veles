"""
=============================================================================
  THIS IS THE FILE YOU EDIT. Everything else in this folder already works.
=============================================================================

Your job: given your budget for this round and what the arena has published,
decide how to split that budget across the three resource pools.

Right now `decide_bid` does exactly what the `naive-max` baseline bot does -
spend everything, split by your device's preference weights. So out of the box
you are registered, bidding, on the leaderboard, and losing to a bot running
your own strategy. Your job is to beat it.

Read docs/strategy-primer.md before you start. It is three pages and it will
save you two hours.

Copyright 2026 The CoGNETs Consortium
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from typing import Any, Dict, List

RESOURCES = ("compute", "energy", "security")


def decide_bid(
    budget: float,
    prices: Dict[str, float],
    capacities: Dict[str, float],
    profile: Dict[str, Any],
    history: List[Dict[str, Any]],
) -> Dict[str, float]:
    """Return this round's bid.

    Parameters
    ----------
    budget : float
        What you may spend this round. It does NOT carry over - anything you
        do not spend is simply gone. Redrawn every round around 1.0.

    prices : dict[str, float]
        The congestion price lambda_k that cleared the PREVIOUS round, one per
        resource, where lambda_k = (total bid on k) / (capacity of k). This is
        your only direct signal about what everyone else is doing.

    capacities : dict[str, float]
        The size C_k of each pool THIS round. Redrawn every round around 1.0,
        so a pool that was scarce last round may be plentiful now.

    profile : dict
        Your device. The hardware in it is fixed for the whole run:
          profile["weights"]  -> {"compute": w_C, "energy": w_E, "security": w_S}
                                 summing to exactly 1. These weight your CES
                                 utility, so `budget * w_k` is always a legal
                                 bid.
          profile["q_min"]    -> minimum compute share, or the round's utility
                                 is halved
          profile["s_min"]    -> minimum security share, same penalty
          profile["features"] -> mobility, capacities, security posture - and
                                 two entries that are NOT fixed:
                                 ["battery"] and ["compromise"] are refreshed
                                 from the arena every round, so the battery you
                                 read here is your charge right now. It is the
                                 most important number on this page.

    history : list[dict]
        Every settled round you took part in, oldest first. Each entry carries
        "bid", "allocation", "spend", "utility", "floor_violations", "prices",
        "capacities", "battery", "battery_drawn", "admissible_next_round" and
        "cumulative_score". This is where you find out whether what you did
        actually worked. Look at it.

    Returns
    -------
    dict[str, float]
        {"compute": ..., "energy": ..., "security": ...}
        Every value >= 0, and the sum must not exceed `budget`.

        The agent clamps your return value before sending it, so you cannot
        get yourself ejected from here by accident. But a bid that gets scaled
        down is a bid you did not intend, so aim to return something valid.
    """
    weights = profile.get("weights", {})

    # ---------------------------------------------------------------- BASELINE
    # Spend the lot, split by preference. This is `naive-max`. Delete it.
    return {k: budget * float(weights.get(k, 1.0 / 3.0)) for k in RESOURCES}

    # -------------------------------------------------------------------------
    # WHERE TO GO NEXT
    #
    # Two things matter far more than anything else. Both are visible in
    # `history` if you look, and neither needs any game theory.
    #
    # -- 1. ENERGY IS NOT FREE -------------------------------------------------
    #
    # The energy share you WIN is drawn from your own battery. Win a lot of it
    # and your charge falls; drop to the cutoff and your node becomes
    # inadmissible and sits the round out entirely - scoring nothing, not
    # merely less. It recharges while resting, then you rejoin.
    #
    # naive-max never notices, flattens itself, and rests for roughly a third
    # of the run. Check it yourself:
    #
    #     history[-1]["battery"]                 how much is left
    #     history[-1]["battery_drawn"]           what last round cost you
    #     history[-1]["admissible_next_round"]   are you about to lose a round
    #
    # Easing off the energy pool as charge falls is worth more than every other
    # idea in this file combined. How you pace it - a hard floor, a linear
    # taper, something that reasons about the recharge cycle - is up to you.
    #
    # -- 2. NEVER EAT A FLOOR PENALTY -----------------------------------------
    #
    # Missing q_min on compute or s_min on security halves the round. Missing
    # both quarters it. Check `history[-1]["floor_violations"]` - if it is not
    # empty you are throwing away half a round, and no amount of clever bidding
    # elsewhere earns that back.
    #
    # Inverting the Kelly rule gives the minimum bid that buys a share `t` of
    # pool k against a field S_k:
    #
    #     b_k  =  S_k * t / (C_k - t)
    #
    # -- HOW TO ESTIMATE S_k --------------------------------------------------
    #
    # S_k is the rest of the swarm's total bid on resource k. You are told
    # enough to recover it:
    #
    #     total_bid_on_k  =  prices[k] * capacities_of_that_round[k]
    #     S_k             =  total_bid_on_k  -  (your own bid on k)
    #
    # Both terms are in history[-1]. For example:
    #
    #     def estimate_others(history, resource):
    #         if not history:
    #             return 1.0
    #         last = history[-1]
    #         total = last["prices"][resource] * last["capacities"][resource]
    #         return max(1e-4, total - last["bid"][resource])
    #
    # Smooth it over a few rounds - one round is noisy and the field moves.
    #
    # -- WHAT IS NOT WORTH YOUR TIME ------------------------------------------
    #
    # Solving the exact Kelly best response is a nice piece of maths and we
    # measured it at about +2% over simply bidding your weights. In a field
    # this size the optimal split is very close to weight-proportional. Do it to
    # strengthen your Insight analysis if you have the time, but do the two
    # things above first - they are worth an order of magnitude more.
    #
    # Holding budget back is also not the answer. There is no interest on
    # unspent budget and it does not carry over, so within a round you should
    # normally commit all of it. The question is always WHERE, not HOW MUCH.
    # -------------------------------------------------------------------------
