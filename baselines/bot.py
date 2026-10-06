"""
Baseline bots for the CoGNETs Swarm Arena.

Three fixed opponents share every run with the participants. They are open
source on purpose: a team should be able to read exactly what it is up against,
and each one is a legitimate idea taken to its natural stopping point.

    BOT=naive-max      python bot.py
    BOT=even-split     python bot.py
    BOT=proportional   python bot.py

What they are worth, measured on identical devices over 40 seeds:
    proportional  is about  2%  ahead of naive-max
    even-split    is roughly level with it, and ahead on some devices
    the organisers' reference agent is about 22% ahead of all three

The gap between the bots and that reference is the room you are playing for,
and almost none of it is in the bid split.

Copyright 2026 The CoGNETs Consortium
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from typing import Any, Callable, Dict, List

# The bots reuse the participants' client and runner rather than duplicating
# them, so there is exactly one retry implementation in the repository. In the
# Docker image these are copied to ./_lib; from a clone they live in
# ../agent-template. Try both.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _candidate in (
    os.path.join(_HERE, "_lib"),
    os.path.join(os.path.dirname(_HERE), "agent-template"),
):
    if os.path.isfile(os.path.join(_candidate, "client.py")):
        sys.path.insert(0, _candidate)
        break

from runner import run_strategy  # noqa: E402  (path set above)

LOG = logging.getLogger("bot")
RESOURCES = ("compute", "energy", "security")


# ---------------------------------------------------------------------------
# The three strategies
# ---------------------------------------------------------------------------
def buy_floors(
    bid: Dict[str, float],
    budget: float,
    prices: Dict[str, float],
    capacities: Dict[str, float],
    q_min: float,
    s_min: float,
    others: Dict[str, float],
) -> Dict[str, float]:
    """Top up compute and security until their floors are met.

    Inverting the Kelly rule, the minimum bid that buys a share ``t`` of pool
    ``k`` against a field ``S_k`` is ``b = S_k * t / (C_k - t)``. Any shortfall
    is taken from unspent budget first, then pro-rata from the other resources.
    """
    bid = dict(bid)
    for k, floor in (("compute", q_min), ("security", s_min)):
        cap = float(capacities.get(k, 1.0))
        s_k = max(float(others.get(k, 1.0)), 1e-9)
        if floor <= 0.0 or floor >= cap:
            continue
        needed = s_k * floor / (cap - floor)
        if needed <= bid.get(k, 0.0):
            continue
        deficit = needed - bid[k]
        donors = [r for r in RESOURCES if r != k and bid.get(r, 0.0) > 0.0]
        available = sum(bid[r] for r in donors)
        headroom = max(0.0, budget - sum(bid.values()))
        take = min(deficit, headroom + available)
        if take <= 0.0:
            continue
        from_donors = take - min(take, headroom)
        if from_donors > 0.0 and available > 1e-9:
            for r in donors:
                bid[r] -= from_donors * (bid[r] / available)
        bid[k] += take
    total = sum(bid.values())
    if total > budget and total > 1e-9:
        bid = {k: v * (budget / total) for k, v in bid.items()}
    return {k: max(0.0, v) for k, v in bid.items()}


#: Aim this far above a service floor when buying it.
#:
#: The bid that buys exactly the floor is worked out from LAST round's field
#: against THIS round's capacity, and capacity is redrawn every round. Aiming at
#: the floor itself therefore lands under it about half the time, which is the
#: worst of both: the insurance is paid for and the penalty is taken anyway. The
#: margin is what turns the purchase into actual cover.
FLOOR_MARGIN = 1.20


def _floor_target(floor: Any, capacity: Any) -> float:
    """The share to buy for a floor: the floor plus a margin, kept under the pool."""
    target = float(floor) * FLOOR_MARGIN
    return min(target, 0.95 * float(capacity))


def estimate_others(history: List[Dict[str, Any]]) -> Dict[str, float]:
    """Recover the rest of the swarm's total bid from the published price."""
    if not history:
        return {k: 1.0 for k in RESOURCES}
    last = history[-1]
    prices = last.get("prices") or {}
    caps = last.get("capacities") or {}
    mine = last.get("bid") or {}
    out = {}
    for k in RESOURCES:
        if k in prices and k in caps:
            out[k] = max(1e-4, float(prices[k]) * float(caps[k]) - float(mine.get(k, 0.0)))
        else:
            out[k] = 1.0
    return out


# ---------------------------------------------------------------------------
# The three strategies
# ---------------------------------------------------------------------------
def naive_max(
    budget: float,
    prices: Dict[str, float],
    capacities: Dict[str, float],
    profile: Dict[str, Any],
    history: List[Dict[str, Any]],
) -> Dict[str, float]:
    """Spend the whole budget, split by preference weights. Nothing else.

    The floor of the field, and the strategy the participant template ships
    with. It gets the easy part right - there is no reason to leave budget
    unspent - and both hard parts wrong: it misses its service floors, and it
    hoovers up energy until its battery is flat and it starts losing whole
    rounds. A team that cannot beat this has a bug rather than a strategy
    problem, which is exactly why it is here.
    """
    w = profile.get("weights", {})
    return {k: budget * float(w.get(k, 1.0 / 3.0)) for k in RESOURCES}


def even_split(
    budget: float,
    prices: Dict[str, float],
    capacities: Dict[str, float],
    profile: Dict[str, Any],
    history: List[Dict[str, Any]],
) -> Dict[str, float]:
    """One third on each pool, regardless of anything at all.

    A control rather than a serious opponent, but not a pushover: because the
    CES utility rewards balanced bundles, ignoring your own weights costs less
    than you would expect, and it stumbles into its floors more often than
    naive-max does.
    """
    third = budget / 3.0
    return {k: third for k in RESOURCES}


def proportional(
    budget: float,
    prices: Dict[str, float],
    capacities: Dict[str, float],
    profile: Dict[str, Any],
    history: List[Dict[str, Any]],
) -> Dict[str, float]:
    """Reads the market and buys its service floors.

        score_k  =  w_k^2 * C_k / lambda_k           normalised to the budget
        then top up compute and security until q_min and s_min are met

    The strongest of the three, and roughly what a good team writes in its
    first hour: lean toward pools that are large or cheap, and never eat a
    floor penalty when the insurance costs less than the loss. Squaring the
    weight and dividing by the price is the split a price-taker would choose
    under this utility. It is worth deriving for yourself, and worth knowing
    before you spend an afternoon on it that the whole idea buys only a couple
    of percent over simply bidding your weights.

    What it does NOT know is that the energy it wins is drawn from its own
    battery. It flattens itself and rests for roughly a third of the run. That
    omission is deliberate: it is the single largest source of score in the
    challenge, and a baseline that already knew it would mean a team scored
    nothing for discovering it.
    """
    w = profile.get("weights", {})
    scores = {}
    for k in RESOURCES:
        price = max(float(prices.get(k, 1.0)), 0.05)
        cap = float(capacities.get(k, 1.0))
        weight = float(w.get(k, 1.0 / 3.0))
        scores[k] = weight * weight * cap / price
    total = sum(scores.values()) or 1.0
    bid = {k: budget * v / total for k, v in scores.items()}

    return buy_floors(
        bid,
        budget,
        prices,
        capacities,
        _floor_target(profile.get("q_min", 0.0), capacities.get("compute", 1.0)),
        _floor_target(profile.get("s_min", 0.0), capacities.get("security", 1.0)),
        estimate_others(history),
    )


def _battery_from(
    history: List[Dict[str, Any]], profile: Dict[str, Any]
) -> float:
    """Latest known charge. The arena reports it in every settled result."""
    for entry in reversed(history):
        if "battery" in entry:
            return float(entry["battery"])
    return float((profile.get("features") or {}).get("battery", 1.0))


STRATEGIES: Dict[str, Callable[..., Dict[str, float]]] = {
    "naive-max": naive_max,
    "even-split": even_split,
    "proportional": proportional,
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "WARNING").upper(),
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    bot_name = os.environ.get("BOT", "naive-max")
    if bot_name not in STRATEGIES:
        print(
            f"unknown BOT={bot_name!r}; choose one of {sorted(STRATEGIES)}",
            file=sys.stderr,
        )
        return 64

    arena_url = os.environ.get("ARENA_URL", "http://localhost:8080")
    team = os.environ.get("TEAM_NAME", f"bot-{bot_name}")

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    LOG.warning("baseline '%s' joining %s as '%s'", bot_name, arena_url, team)
    try:
        return run_strategy(STRATEGIES[bot_name], arena_url, team, stop=stop)
    except KeyboardInterrupt:
        return 0
    finally:
        stop.set()


if __name__ == "__main__":
    raise SystemExit(main())
