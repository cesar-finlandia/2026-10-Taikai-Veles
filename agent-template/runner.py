"""
A reusable "run this strategy against an arena" loop.

``agent.py`` deliberately does NOT use this - it spells the loop out in full,
because reading it is part of learning the protocol. This module exists so the
baseline bots (and the organisers' reference agent) share one implementation
instead of three that drift apart.

You are welcome to use it in your own agent if you prefer:

    from runner import run_strategy
    from strategy import decide_bid
    run_strategy(decide_bid, arena_url="http://localhost:8080", team="team-kappa")

Copyright 2026 The CoGNETs Consortium
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from client import (
    AlreadyBid,
    ArenaClient,
    ArenaClientError,
    Ejected,
    NoRound,
    NotAdmissible,
    RoundClosed,
    WrongRound,
)

LOG = logging.getLogger("runner")
RESOURCES = ("compute", "energy", "security")

StrategyFn = Callable[..., Dict[str, float]]


def clamp(bid: Any, budget: float) -> Dict[str, float]:
    """Make any return value a legal bid. Never raises."""
    clean: Dict[str, float] = {}
    for k in RESOURCES:
        try:
            v = float((bid or {}).get(k, 0.0))
        except (TypeError, ValueError, AttributeError):
            v = 0.0
        if v != v or v in (float("inf"), float("-inf")) or v < 0.0:
            v = 0.0
        clean[k] = v
    total = sum(clean.values())
    if total > budget and total > 0:
        clean = {k: v * (budget / total) for k, v in clean.items()}
    return clean


def heartbeat_loop(
    client: ArenaClient, interval: float, stop: threading.Event
) -> None:
    while not stop.is_set():
        try:
            client.heartbeat()
        except Ejected:
            return
        except ArenaClientError as exc:
            LOG.debug("heartbeat failed: %s", exc)
        stop.wait(interval)


def run_strategy(
    strategy: StrategyFn,
    arena_url: str,
    team: str,
    stop: Optional[threading.Event] = None,
    on_result: Optional[Callable[[Dict[str, Any]], None]] = None,
    connect_timeout: float = 90.0,
) -> int:
    """Register, heartbeat, and bid every round until the run finishes.

    Returns 0 on a clean finish, 1 if the arena never came up, 2 if ejected.
    """
    stop = stop or threading.Event()
    client = ArenaClient(arena_url, team)

    if not client.wait_for_arena(timeout=connect_timeout):
        LOG.error("arena at %s never became healthy", arena_url)
        return 1

    try:
        client.register()
    except ArenaClientError as exc:
        LOG.error("registration failed: %s", exc)
        return 1

    lease = float(client.arena_info.get("lease_seconds", 12.0))
    total_rounds = int(client.arena_info.get("total_rounds", 0))

    hb = threading.Thread(
        target=heartbeat_loop, args=(client, max(1.0, lease / 3.0), stop), daemon=True
    )
    hb.start()

    history: List[Dict[str, Any]] = []
    last_bid_round = 0
    last_result_round = 0
    exit_code = 0

    try:
        while not stop.is_set():
            try:
                rnd = client.get_round()
            except NoRound:
                stop.wait(0.4)
                continue
            except Ejected:
                return 2
            except ArenaClientError:
                stop.wait(0.8)
                continue

            round_index = int(rnd["round"])

            # A restarted arena counts from round 1 again. The guard below is
            # keyed off the round number and `history` belongs to a run that no
            # longer exists, so both are dropped when the counter goes back.
            if round_index < last_bid_round:
                LOG.info(
                    "round counter went backwards (%d -> %d): a new run has "
                    "started on this arena, resetting per-run state",
                    last_bid_round,
                    round_index,
                )
                last_bid_round = 0
                last_result_round = 0
                history.clear()

            # Bid first, collect results afterwards. Reading the previous
            # result is bookkeeping; missing the bidding window costs a whole
            # round. Nothing that can block goes in front of the bid.
            if round_index <= last_bid_round or rnd.get("settled"):
                if last_result_round < last_bid_round and last_bid_round:
                    try:
                        result = client.get_result(last_bid_round)
                        if result.get("participated") and not (
                            history and history[-1].get("round") == result.get("round")
                        ):
                            history.append(result)
                            if on_result:
                                on_result(result)
                        last_result_round = last_bid_round
                    except (NoRound, ArenaClientError):
                        pass
                stop.wait(0.2)
                continue

            you = rnd.get("you") or {}
            if you.get("already_submitted") or you.get("admissible") is False:
                last_bid_round = round_index
                stop.wait(0.25)
                continue

            budget = float(rnd.get("budget", 0.0))
            try:
                bid = strategy(
                    budget=budget,
                    prices=rnd.get("prices", {}),
                    capacities=rnd.get("capacities", {}),
                    profile=client.profile,
                    history=history,
                )
            except Exception:
                LOG.exception("strategy raised; falling back to proportional")
                weights = client.profile.get("weights", {})
                bid = {k: budget * float(weights.get(k, 1 / 3)) for k in RESOURCES}

            try:
                client.post_bid(round_index, clamp(bid, budget))
                last_bid_round = round_index
            except (AlreadyBid, RoundClosed, WrongRound, NotAdmissible):
                last_bid_round = round_index
            except Ejected:
                return 2
            except ArenaClientError as exc:
                LOG.debug("bid failed: %s", exc)

            if total_rounds and round_index >= total_rounds:
                stop.wait(2.5)
                break
            stop.wait(0.2)
    finally:
        stop.set()
        client.close()

    return exit_code
