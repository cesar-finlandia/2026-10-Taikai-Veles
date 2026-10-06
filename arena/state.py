"""
Swarm state: node registry, leases, round lifecycle, scoring.

The arena is a single-process, in-memory service. That is deliberate: a
two-day hackathon needs an environment that starts in one command, never needs a
database, and can be reset by restarting the container.

Copyright 2026 The CoGNETs Consortium
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import random
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import game
from .config import ScenarioConfig
from .game import RESOURCES

MAX_HISTORY = 200
MAX_EVENTS = 4000


class ArenaError(Exception):
    """Base class for errors the API layer turns into HTTP status codes."""

    status = 400


class AuthError(ArenaError):
    status = 401


class ForbiddenError(ArenaError):
    status = 403


class NoRoundError(ArenaError):
    status = 404


class RoundClosedError(ArenaError):
    status = 410


class DuplicateBidError(ArenaError):
    status = 409


class WrongRoundError(ArenaError):
    status = 422


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------
@dataclass
class NodeState:
    """One registered edge node - one hackathon team, or one baseline bot."""

    node_id: str
    team: str
    token: str

    # CoGNETs feature vector h_i = (c^C, c^R, b, s, kappa, m)
    compute_cap: float
    ram_cap: float
    battery: float
    security_posture: float
    compromise: float
    mobility: float

    # Preference weights w_i (AHP), summing to 1 across the three resources
    weights: Dict[str, float]

    # Admissibility floors
    q_min: float
    s_min: float

    budget: float = 1.0
    is_baseline: bool = False

    registered_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    active: bool = True
    ejected: bool = False
    eject_reason: str = ""

    score: float = 0.0
    utility_total: float = 0.0
    rounds_participated: int = 0
    rounds_missed: int = 0
    rounds_idle: int = 0
    floor_violations: int = 0
    protocol_warnings: List[str] = field(default_factory=list)
    history: List[dict] = field(default_factory=list)

    # -------------------------------------------------------------- helpers
    def features(self) -> Dict[str, float]:
        return {
            "compute_cap": round(self.compute_cap, 4),
            "ram_cap": round(self.ram_cap, 4),
            "battery": round(self.battery, 4),
            "security_posture": round(self.security_posture, 4),
            "compromise": round(self.compromise, 4),
            "mobility": round(self.mobility, 4),
        }

    def profile(self) -> Dict[str, Any]:
        return {
            "features": self.features(),
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "q_min": round(self.q_min, 4),
            "s_min": round(self.s_min, 4),
        }

    def admissible(self, battery_cutoff: float, kappa_bar: float) -> bool:
        """CoGNETs Definition 2.1, adapted.

        Admitted iff the node is not ejected, its compromise score is below the
        gate, it has charge above the cutoff, and its two floors are jointly
        satisfiable at all.
        """
        if self.ejected:
            return False
        if self.compromise >= kappa_bar:
            return False
        if self.battery <= battery_cutoff:
            return False
        if self.q_min + self.s_min >= 0.99:
            return False
        return True

    def inadmissible_reason(self, battery_cutoff: float, kappa_bar: float) -> str:
        if self.ejected:
            return f"ejected: {self.eject_reason}"
        if self.compromise >= kappa_bar:
            return f"compromise score {self.compromise:.2f} >= {kappa_bar:.2f}"
        if self.battery <= battery_cutoff:
            return (
                f"battery {self.battery:.3f} <= cutoff {battery_cutoff:.3f}; "
                f"idle a round to recharge"
            )
        return "admissible"

    def public(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "team": self.team,
            "features": self.features(),
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "q_min": round(self.q_min, 4),
            "s_min": round(self.s_min, 4),
            "active": self.active,
            "ejected": self.ejected,
            "is_baseline": self.is_baseline,
        }


# ---------------------------------------------------------------------------
# Rounds
# ---------------------------------------------------------------------------
@dataclass
class RoundState:
    """One auction round."""

    index: int
    opens_at: float
    closes_at: float
    capacities: Dict[str, float]
    prices: Dict[str, float]
    seed: int

    bids: Dict[str, Dict[str, float]] = field(default_factory=dict)
    submitted_at: Dict[str, float] = field(default_factory=dict)
    settled: bool = False
    results: Dict[str, dict] = field(default_factory=dict)
    lsw: float = 0.0

    def public(self, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        return {
            "round": self.index,
            "capacities": {k: round(v, 4) for k, v in self.capacities.items()},
            "prices": {k: round(v, 6) for k, v in self.prices.items()},
            "opens_at": round(self.opens_at, 3),
            "closes_at": round(self.closes_at, 3),
            "seconds_remaining": round(max(0.0, self.closes_at - now), 3),
            "settled": self.settled,
        }


# ---------------------------------------------------------------------------
# The arena
# ---------------------------------------------------------------------------
class Arena:
    """Registry + auctioneer + scorer, all in one object."""

    def __init__(self, config: ScenarioConfig) -> None:
        self.config = config
        self.nodes: Dict[str, NodeState] = {}
        self.token_index: Dict[str, str] = {}
        self.team_index: Dict[str, str] = {}
        self.rounds: List[RoundState] = []
        self.current: Optional[RoundState] = None
        self.started = False
        self.finished = False
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.event_log: List[dict] = []
        #: Team names the arena treats as its own baseline bots.
        self.baseline_teams = {f"bot-{b}" for b in config.baselines}
        #: Bearer tokens that fault injection skips. Only the baseline bots are
        #: ever in here, and the arena puts them there itself - registration
        #: cannot ask for it. See the note in chaos.py for why the bots have to
        #: be present in every round.
        self.faultless_tokens: set[str] = set()
        #: Node id of the first non-baseline registrant. The baselines are given
        #: a copy of its device so the leaderboard compares strategies and not
        #: hardware. See `_share_device_with_baselines`.
        self.device_source_id: Optional[str] = None

    # ------------------------------------------------------------- events
    def log(self, kind: str, **payload: Any) -> None:
        self.event_log.append({"t": round(time.time(), 3), "kind": kind, **payload})
        if len(self.event_log) > MAX_EVENTS:
            del self.event_log[: MAX_EVENTS // 4]

    # ----------------------------------------------------------- registry
    def register(self, team: str, is_baseline: bool = False) -> NodeState:
        """Register a node, or re-admit one that already exists.

        Re-registering under the same team name returns the same node with a
        fresh token: a crashed agent keeps its cumulative score but does not
        get back the rounds it missed. That is exactly the CoGNETs join/leave
        semantics, and it is the cheapest realistic churn we can implement.
        """
        team = (team or "").strip()
        if not team:
            raise ArenaError("team name must not be empty")
        if len(team) > 64:
            raise ArenaError("team name must be at most 64 characters")

        existing_id = self.team_index.get(team)
        if existing_id and existing_id in self.nodes:
            node = self.nodes[existing_id]
            if node.ejected:
                raise ForbiddenError(
                    f"team '{team}' was ejected from this run: {node.eject_reason}"
                )
            self.faultless_tokens.discard(node.token)
            self.token_index.pop(node.token, None)
            node.token = secrets.token_urlsafe(16)
            self.token_index[node.token] = node.node_id
            if node.is_baseline:
                self.faultless_tokens.add(node.token)
            node.last_heartbeat = time.time()
            node.active = True
            self.log("rejoin", team=team, node_id=node.node_id)
            return node

        is_baseline = is_baseline or team in self.baseline_teams
        node = self._mint_node(team, is_baseline)
        self.nodes[node.node_id] = node
        self.token_index[node.token] = node.node_id
        self.team_index[team] = node.node_id
        if is_baseline:
            self.faultless_tokens.add(node.token)
        self.log("register", team=team, node_id=node.node_id, baseline=is_baseline)
        self._share_device_with_baselines(node)
        return node

    def _share_device_with_baselines(self, node: NodeState) -> None:
        """Put the baselines on the first participant's device.

        A cumulative score only means something next to another score earned on
        the same hardware. The same strategy, run on forty different drawn
        devices, comes out anywhere between 13.0 and 17.7 - a spread wider than
        the distance between any two of the three baseline strategies. Bots
        sitting on devices of their own would make the leaderboard a reading of
        the hardware draw rather than of anyone's ideas.

        Giving the baselines a copy of the participant's device removes that
        term: four nodes, one set of weights and floors, and the only thing left
        that can separate them is what each one does with it. It is the same
        reasoning, and the same `clone_device` call, that the grading harness
        uses to put its two anchors on the graded team's device.

        A baseline that is already running holds the profile it was handed at
        registration, so its token is rotated here. The next call it makes comes
        back 401, and the client takes the re-registration path it already
        implements and is handed the new profile.
        """
        if node.is_baseline:
            if self.device_source_id:
                self.clone_device(node, self.nodes[self.device_source_id])
            return
        if self.device_source_id is not None:
            return
        self.device_source_id = node.node_id
        for other in self.nodes.values():
            if other.is_baseline:
                self.clone_device(other, node)
                self._rotate_token(other)

    def _rotate_token(self, node: NodeState) -> None:
        """Invalidate a node's token so its client re-registers and re-reads it."""
        self.faultless_tokens.discard(node.token)
        self.token_index.pop(node.token, None)
        node.token = secrets.token_urlsafe(16)
        self.token_index[node.token] = node.node_id
        if node.is_baseline:
            self.faultless_tokens.add(node.token)

    def _mint_node(self, team: str, is_baseline: bool) -> NodeState:
        """Derive a device profile deterministically from the team name.

        Two consequences worth knowing: a team's device is identical across
        every run they do both days, and no two teams get the same device. The
        second quietly kills copy-paste between tables and makes "read your own
        profile" a lesson that actually lands.
        """
        rng = random.Random(f"{self.config.seed}:{team}")

        w_c = rng.uniform(0.25, 0.55)
        w_e = rng.uniform(0.15, 0.40)
        w_s = max(0.10, 1.0 - w_c - w_e)
        total_w = w_c + w_e + w_s

        # Round FIRST, then push the rounding residue into the largest weight,
        # so the three numbers an agent is shown sum to exactly 1.0.
        #
        # Without this they summed to 1.0001 often enough to matter, and
        # `budget * w_k` - the most natural bid anyone writes, and the one the
        # template ships with - came out over budget by 1e-4 every round. That
        # is above the arena's own tolerance, so it raised a compromise warning
        # each time and ejected the node on round 12. The template happens to
        # clamp its bid and never saw it; an agent written from the API
        # documentation in another language would have been ejected for
        # following the rules exactly.
        weights = {
            "compute": round(w_c / total_w, 4),
            "energy": round(w_e / total_w, 4),
            "security": round(w_s / total_w, 4),
        }
        heaviest = max(weights, key=lambda k: weights[k])
        weights[heaviest] = round(
            weights[heaviest] + (1.0 - sum(weights.values())), 6)

        return NodeState(
            node_id=f"node-{len(self.nodes) + 1:03d}",
            team=team,
            token=secrets.token_urlsafe(16),
            compute_cap=round(rng.uniform(0.35, 1.00), 4),
            ram_cap=round(rng.uniform(0.30, 1.00), 4),
            battery=round(rng.uniform(0.55, 1.00), 4),
            security_posture=round(rng.uniform(0.40, 0.95), 4),
            compromise=0.0,
            mobility=round(rng.uniform(0.00, 0.45), 4),
            weights=weights,
            # Floors are set so they actually bind against a field of this
            # size: with four nodes each pool splits roughly four ways, so a
            # floor near 0.12-0.22 is reachable but not free. Change these and
            # re-measure.
            q_min=round(rng.uniform(0.12, 0.22), 4),
            s_min=round(rng.uniform(0.08, 0.16), 4),
            budget=self.config.base_budget,
            is_baseline=is_baseline,
        )

    def clone_device(self, target: NodeState, source: NodeState) -> NodeState:
        """Give `target` the same device as `source`.

        Two callers. The arena itself puts the baseline bots on the
        participant's device when that participant registers, so the standings
        compare strategies rather than hardware. The grading harness uses it to
        stand up SHADOW nodes: the template strategy and the reference agent,
        running on the graded team's exact device, in the same run and the same
        field.

        This matters more than it looks. Scoring a team against anchors that
        sit on *different* devices imports device luck into the score three
        times over - once for the team, once for each anchor - and measurement
        showed a genuinely good agent scoring anywhere from 0 to 25 depending
        on the draw. With the anchors on the team's own device, the ratio
        (S - T) / (R - T) compares like with like.

        Copies the device only. Score, history and lease state stay separate.
        """
        for attribute in (
            "compute_cap", "ram_cap", "battery", "security_posture",
            "mobility", "q_min", "s_min",
        ):
            setattr(target, attribute, getattr(source, attribute))
        target.weights = dict(source.weights)
        self.log("clone_device", target=target.node_id, source=source.node_id)
        return target

    def authenticate(self, token: str) -> NodeState:
        node_id = self.token_index.get((token or "").strip())
        if not node_id:
            raise AuthError("unknown or expired token; re-register to get a new one")
        node = self.nodes[node_id]
        if node.ejected:
            raise ForbiddenError(f"node ejected: {node.eject_reason}")
        return node

    def heartbeat(self, node: NodeState) -> Dict[str, Any]:
        node.last_heartbeat = time.time()
        if not node.active and not node.ejected:
            node.active = True
            self.log("reactivate", node_id=node.node_id, team=node.team)
        return {
            "ok": True,
            "lease_expires_in": round(self.config.lease_seconds, 3),
            "round": self.current.index if self.current else 0,
            "active": node.active,
        }

    def expire_leases(self) -> None:
        """Mark nodes that have gone quiet as inactive.

        Baselines are exempt: they are in-process and never miss a beat, and
        exempting them keeps the leaderboard honest if the host machine stalls.
        """
        now = time.time()
        for node in self.nodes.values():
            if node.is_baseline or node.ejected or not node.active:
                continue
            if now - node.last_heartbeat > self.config.lease_seconds:
                node.active = False
                self.log("lease_expired", node_id=node.node_id, team=node.team)

    def penalise(self, node: NodeState, reason: str) -> None:
        """Grow the compromise score kappa; eject once it crosses the gate."""
        node.compromise = round(node.compromise + self.config.kappa_penalty, 4)
        node.protocol_warnings.append(reason)
        if len(node.protocol_warnings) > 100:
            del node.protocol_warnings[:50]
        if node.compromise >= self.config.kappa_bar and not node.ejected:
            node.ejected = True
            node.active = False
            node.eject_reason = f"compromise threshold reached ({reason})"
            self.log("eject", node_id=node.node_id, team=node.team, reason=reason)

    # ------------------------------------------------------------- rounds
    def open_round(self) -> RoundState:
        index = len(self.rounds) + 1
        rng = random.Random(self.config.seed * 1000 + index)
        now = time.time()

        if self.current is not None and self.current.settled:
            raw_prices = game.clearing_prices(
                self.current.bids, self.current.capacities
            )
        else:
            raw_prices = {k: 1.0 for k in RESOURCES}

        capacities = {
            k: round(
                self.config.capacity_base
                * rng.uniform(
                    1.0 - self.config.capacity_jitter,
                    1.0 + self.config.capacity_jitter,
                ),
                4,
            )
            for k in RESOURCES
        }

        state = RoundState(
            index=index,
            opens_at=now,
            closes_at=now + self.config.round_seconds,
            capacities=capacities,
            # Floor the published price so an agent dividing by it never blows up.
            prices={k: round(max(v, 0.01), 6) for k, v in raw_prices.items()},
            seed=self.config.seed * 1000 + index,
        )

        self._tick_devices(rng)

        self.rounds.append(state)
        self.current = state
        if not self.started:
            self.started = True
            self.started_at = now
        self.log("round_open", round=index, capacities=capacities)
        return state

    def _tick_devices(self, rng: random.Random) -> None:
        """Replenish wallets at the start of a round. Battery is NOT touched here.

        The battery lifecycle deliberately runs in `settle()` instead:

          * a node that took part discharges by the energy share it won;
          * a node that sat the round out recharges.

        Doing it this way is what makes a flat battery actually cost you a
        round. If we recharged at round-open, a node that hit the cutoff would
        be revived in the same tick that checks its admissibility, and would
        never miss anything - which is exactly the bug this ordering fixes.
        """
        # ONE budget for the round, shared by every node.
        #
        # Drawing a separate budget per node added no strategic content - it is
        # pure noise - and it broke the grading anchors: a shadow node carrying
        # the team's device could still be handed a different wallet, so the
        # comparison was not like-for-like. A common budget makes every node in
        # a round face the same constraint, which is both fairer and quieter.
        budget = round(
            self.config.base_budget
            * rng.uniform(
                1.0 - self.config.budget_jitter,
                1.0 + self.config.budget_jitter,
            ),
            6,
        )
        for node in self.nodes.values():
            if node.ejected:
                continue
            node.budget = budget

    def _discharge(self, node: NodeState, energy_share: float) -> float:
        """Burn charge in proportion to the energy actually drawn this round.

        This is the mechanism that gives the challenge its intertemporal edge.
        Energy is not a free resource you simply want more of: the share you
        win is drawn from your own battery, and a battery below the cutoff
        makes the node inadmissible, costing whole rounds of scoring until it
        recharges.

        Mobile devices pay a surcharge, which is why the mobility feature in
        your profile is worth reading.
        """
        drawn = (
            self.config.battery_drain
            * float(energy_share)
            * (1.0 + node.mobility)
        )
        drawn += self.config.idle_drain
        node.battery = round(max(0.0, node.battery - drawn), 4)
        return round(drawn, 6)

    def submit(self, node: NodeState, round_index: Optional[int], bid: dict) -> dict:
        """Accept a bid for the currently open round."""
        state = self.current
        if state is None:
            raise NoRoundError("no round is open yet")
        if state.settled or time.time() > state.closes_at:
            raise RoundClosedError(
                f"round {state.index} has closed; wait for the next one"
            )
        if round_index is not None and round_index != state.index:
            raise WrongRoundError(
                f"you sent round {round_index} but round {state.index} is open"
            )
        if not node.admissible(self.config.battery_cutoff, self.config.kappa_bar):
            raise ForbiddenError(
                "node is not admissible this round: "
                + node.inadmissible_reason(
                    self.config.battery_cutoff, self.config.kappa_bar
                )
            )
        if node.node_id in state.bids:
            raise DuplicateBidError(f"already submitted for round {state.index}")

        clean, warnings = game.sanitise_bid(bid or {}, node.budget)
        for warning in warnings:
            if warning.startswith("unknown_resource"):
                continue
            self.penalise(node, warning)

        state.bids[node.node_id] = clean
        state.submitted_at[node.node_id] = time.time()
        self.heartbeat(node)

        return {
            "accepted": True,
            "round": state.index,
            "bid": {k: round(v, 6) for k, v in clean.items()},
            "spend": round(sum(clean.values()), 6),
            "budget": round(node.budget, 6),
            "warnings": warnings,
        }

    def settle(self) -> RoundState:
        """Close the open round: allocate, score, and record."""
        state = self.current
        if state is None:
            raise NoRoundError("nothing to settle")
        if state.settled:
            return state

        allocations = game.kelly_allocation(state.bids, state.capacities)
        utilities: Dict[str, float] = {}

        for node_id, alloc in allocations.items():
            node = self.nodes[node_id]
            raw_u = game.ces_utility(alloc, node.weights)
            utility, violations = game.apply_floors(
                raw_u, alloc, node.q_min, node.s_min
            )
            utilities[node_id] = utility

            node.utility_total += utility
            node.score += utility
            node.rounds_participated += 1
            node.floor_violations += len(violations)

            # The energy you won comes out of your own battery.
            drawn = self._discharge(node, alloc.get("energy", 0.0))

            result = {
                "round": state.index,
                "allocation": {k: round(v, 6) for k, v in alloc.items()},
                "bid": {k: round(v, 6) for k, v in state.bids[node_id].items()},
                "spend": round(sum(state.bids[node_id].values()), 6),
                "prices": {k: round(v, 6) for k, v in state.prices.items()},
                "capacities": {k: round(v, 4) for k, v in state.capacities.items()},
                "utility_raw": round(raw_u, 6),
                "utility": round(utility, 6),
                "floor_violations": violations,
                "battery_drawn": drawn,
                "battery": round(node.battery, 4),
                "admissible_next_round": node.admissible(
                    self.config.battery_cutoff, self.config.kappa_bar
                ),
                "round_score": round(utility, 6),
                "cumulative_score": round(node.score, 6),
            }
            state.results[node_id] = result
            node.history.append(result)
            if len(node.history) > MAX_HISTORY:
                del node.history[: MAX_HISTORY // 4]

        # Everyone who did not take part. Classify FIRST, then recharge -
        # otherwise the recharge makes a resting node look admissible and every
        # idle round is misreported as a missed one.
        for node in self.nodes.values():
            if node.node_id in allocations or node.ejected:
                continue
            could_have_bid = node.active and node.admissible(
                self.config.battery_cutoff, self.config.kappa_bar
            )
            if could_have_bid:
                # Awake, allowed to bid, and said nothing. That is a miss.
                node.rounds_missed += 1
            else:
                # Flat battery or an expired lease - resting, not missing.
                node.rounds_idle += 1
            node.battery = round(
                min(1.0, node.battery + self.config.recharge_rate), 4
            )

        state.lsw = round(game.log_social_welfare(utilities), 6) if utilities else 0.0
        state.settled = True
        self.log(
            "round_settled",
            round=state.index,
            lsw=state.lsw,
            participants=len(allocations),
        )

        if state.index >= self.config.total_rounds:
            self.finished = True
            self.finished_at = time.time()
            self.log("run_finished", rounds=state.index)

        return state

    # -------------------------------------------------------------- views
    def result_for(self, node: NodeState, round_index: int) -> Dict[str, Any]:
        if round_index < 1 or round_index > len(self.rounds):
            raise NoRoundError(f"round {round_index} does not exist")
        state = self.rounds[round_index - 1]
        if not state.settled:
            raise NoRoundError(f"round {round_index} has not settled yet")
        result = state.results.get(node.node_id)
        if result is None:
            return {
                "round": round_index,
                "participated": False,
                "reason": "no bid was recorded for you in this round",
                "swarm": {"lsw": state.lsw, "participants": len(state.results)},
            }
        return {
            **result,
            "participated": True,
            "swarm": {"lsw": state.lsw, "participants": len(state.results)},
        }

    def me(self, node: NodeState) -> Dict[str, Any]:
        return {
            "node_id": node.node_id,
            "team": node.team,
            "profile": node.profile(),
            "budget": round(node.budget, 6),
            "score": round(node.score, 6),
            "utility_total": round(node.utility_total, 6),
            "rounds_participated": node.rounds_participated,
            "rounds_missed": node.rounds_missed,
            "rounds_idle": node.rounds_idle,
            "floor_violations": node.floor_violations,
            "compromise": round(node.compromise, 4),
            "battery": round(node.battery, 4),
            "active": node.active,
            "ejected": node.ejected,
            "admissible": node.admissible(
                self.config.battery_cutoff, self.config.kappa_bar
            ),
            "protocol_warnings": node.protocol_warnings[-20:],
            "history": node.history[-20:],
        }

    def leaderboard(self) -> List[Dict[str, Any]]:
        rows = [
            {
                "team": n.team,
                "node_id": n.node_id,
                "score": round(n.score, 4),
                "utility_total": round(n.utility_total, 4),
                "rounds_participated": n.rounds_participated,
                "rounds_missed": n.rounds_missed,
                "rounds_idle": n.rounds_idle,
                "floor_violations": n.floor_violations,
                "compromise": round(n.compromise, 3),
                "battery": round(n.battery, 3),
                "active": n.active,
                "ejected": n.ejected,
                "is_baseline": n.is_baseline,
            }
            for n in self.nodes.values()
        ]
        rows.sort(key=lambda r: (-r["score"], r["team"]))
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
        return rows

    def swarm(self) -> List[Dict[str, Any]]:
        return [n.public() for n in self.nodes.values()]

    def status(self) -> Dict[str, Any]:
        settled = [r for r in self.rounds if r.settled]
        return {
            "scenario": self.config.name,
            "started": self.started,
            "finished": self.finished,
            "round": self.current.index if self.current else 0,
            "total_rounds": self.config.total_rounds,
            "round_seconds": self.config.round_seconds,
            "lease_seconds": self.config.lease_seconds,
            "chaos_rate": self.config.chaos_rate,
            "nodes_registered": len(self.nodes),
            "nodes_active": sum(1 for n in self.nodes.values() if n.active),
            "nodes_ejected": sum(1 for n in self.nodes.values() if n.ejected),
            "last_lsw": settled[-1].lsw if settled else None,
            "lsw_series": [r.lsw for r in settled][-80:],
            "seconds_remaining": (
                round(max(0.0, self.current.closes_at - time.time()), 2)
                if self.current and not self.current.settled
                else 0.0
            ),
        }
