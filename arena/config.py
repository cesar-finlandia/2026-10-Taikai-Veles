"""
Scenario configuration for the CoGNETs Swarm Arena.

A scenario is the whole personality of a run: how fast rounds go, how much the
capacity and budget move, how hostile the network is, and which baseline bots
are in the field. Two ship with the challenge - ``practice`` and ``graded`` -
and the organisers run the graded one with a seed that is not published in
advance.

Copyright 2026 The CoGNETs Consortium
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List

SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"

#: Bots the arena knows how to describe. The compose file starts them; the
#: arena only needs the names so it can mark them on the leaderboard.
KNOWN_BASELINES = ("naive-max", "even-split", "proportional")


@dataclass
class ScenarioConfig:
    """Everything that distinguishes one run from another."""

    name: str = "practice"

    # --- round cadence -----------------------------------------------------
    round_seconds: float = 6.0
    total_rounds: int = 40
    lease_seconds: float = 12.0

    # --- economy -----------------------------------------------------------
    base_budget: float = 1.0
    budget_jitter: float = 0.25
    capacity_base: float = 1.0
    capacity_jitter: float = 0.30

    # --- device physics ----------------------------------------------------
    battery_drain: float = 0.30
    """Charge burned per unit of ENERGY SHARE won, before the mobility
    surcharge. This is the coupling that makes energy costly rather than free:
    win a lot of the energy pool and you flatten your own battery."""

    idle_drain: float = 0.004
    """A small fixed cost of being awake at all, so a node that bids nothing on
    energy still ages."""

    battery_cutoff: float = 0.05
    """Below this the node is inadmissible and must sit out a round."""

    recharge_rate: float = 0.22
    """Charge recovered per round spent idle."""

    # --- admission gate ----------------------------------------------------
    kappa_bar: float = 0.60
    kappa_penalty: float = 0.05

    # --- hostility ---------------------------------------------------------
    chaos_rate: float = 0.0
    latency_ms: int = 0

    # --- determinism -------------------------------------------------------
    seed: int = 20261006

    # --- field -------------------------------------------------------------
    baselines: List[str] = field(default_factory=lambda: list(KNOWN_BASELINES))

    # --- lifecycle ---------------------------------------------------------
    autostart: bool = True
    start_delay_seconds: float = 8.0
    """Grace period after boot before round 1 opens, so slow agents can register."""

    # ------------------------------------------------------------------ load
    @classmethod
    def from_yaml(cls, path: str | Path) -> "ScenarioConfig":
        data = _read_yaml(Path(path))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScenarioConfig":
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(
                f"unknown scenario keys: {sorted(unknown)}. "
                f"Valid keys are: {sorted(known)}"
            )
        return cls(**data)

    @classmethod
    def load(cls, name_or_path: str | None = None) -> "ScenarioConfig":
        """Resolve a scenario from a name, a path, or the environment.

        Precedence: explicit argument, then ``$ARENA_SCENARIO``, then
        ``practice``. Environment overrides are applied last so a grading run
        can inject the real seed without editing any file.
        """
        target = name_or_path or os.environ.get("ARENA_SCENARIO") or "practice"
        path = Path(target)
        if not path.exists():
            path = SCENARIO_DIR / f"{target}.yaml"
        if not path.exists():
            raise FileNotFoundError(
                f"scenario '{target}' not found; looked in {SCENARIO_DIR}"
            )
        cfg = cls.from_yaml(path)
        cfg.apply_env_overrides()
        cfg.validate()
        return cfg

    # -------------------------------------------------------------- override
    def apply_env_overrides(self) -> None:
        """Let the environment override any numeric or boolean field.

        ``ARENA_SEED=12345`` is the one the grading harness uses; the rest are
        there so a mentor can shorten a run on the day without editing YAML.
        """
        mapping = {
            "ARENA_SEED": ("seed", int),
            "ARENA_ROUND_SECONDS": ("round_seconds", float),
            "ARENA_TOTAL_ROUNDS": ("total_rounds", int),
            "ARENA_LEASE_SECONDS": ("lease_seconds", float),
            "ARENA_CHAOS_RATE": ("chaos_rate", float),
            "ARENA_LATENCY_MS": ("latency_ms", int),
            "ARENA_START_DELAY": ("start_delay_seconds", float),
            "ARENA_AUTOSTART": ("autostart", _as_bool),
        }
        for env_key, (attr, caster) in mapping.items():
            raw = os.environ.get(env_key)
            if raw is None or raw == "":
                continue
            try:
                setattr(self, attr, caster(raw))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{env_key}={raw!r} is not valid: {exc}") from exc

    # -------------------------------------------------------------- validate
    def validate(self) -> None:
        problems: List[str] = []
        if self.round_seconds <= 0.5:
            problems.append("round_seconds must be > 0.5")
        if self.total_rounds < 1:
            problems.append("total_rounds must be >= 1")
        if self.lease_seconds <= self.round_seconds:
            problems.append(
                "lease_seconds must exceed round_seconds, otherwise a node that "
                "bids once per round still loses its lease"
            )
        if not 0.0 <= self.budget_jitter < 1.0:
            problems.append("budget_jitter must be in [0, 1)")
        if not 0.0 <= self.capacity_jitter < 1.0:
            problems.append("capacity_jitter must be in [0, 1)")
        if not 0.0 <= self.chaos_rate <= 0.5:
            problems.append("chaos_rate must be in [0, 0.5]")
        if self.kappa_penalty <= 0 or self.kappa_bar <= self.kappa_penalty:
            problems.append("kappa_bar must exceed kappa_penalty")
        unknown = set(self.baselines) - set(KNOWN_BASELINES)
        if unknown:
            problems.append(f"unknown baselines: {sorted(unknown)}")
        if problems:
            raise ValueError("invalid scenario:\n  - " + "\n  - ".join(problems))

    # ------------------------------------------------------------------ view
    def public(self) -> Dict[str, Any]:
        """The subset agents are told about on registration."""
        return {
            "scenario": self.name,
            "round_seconds": self.round_seconds,
            "total_rounds": self.total_rounds,
            "lease_seconds": self.lease_seconds,
            "resources": ["compute", "energy", "security"],
            "rho": 0.5,
            "kappa_bar": self.kappa_bar,
            "battery_cutoff": self.battery_cutoff,
        }


def _as_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_yaml(path: Path) -> Dict[str, Any]:
    """Read a scenario file.

    Uses PyYAML when it is installed and falls back to a tiny parser that
    handles the flat ``key: value`` and ``key: [a, b]`` shapes our scenario
    files actually use. The fallback exists so the arena still starts on a
    machine where the dependency install went sideways - which, at 9am on a
    hackathon morning, is a scenario worth planning for.
    """
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path} must contain a mapping")
        return data
    except ImportError:
        return _read_yaml_minimal(text, path)


def _read_yaml_minimal(text: str, path: Path) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line[:1].isspace():
            raise ValueError(
                f"{path}:{lineno}: nested YAML needs PyYAML installed "
                f"(pip install pyyaml)"
            )
        if ":" not in line:
            raise ValueError(f"{path}:{lineno}: expected 'key: value'")
        key, _, value = line.partition(":")
        data[key.strip()] = _coerce_scalar(value.strip(), path, lineno)
    return data


def _coerce_scalar(value: str, path: Path, lineno: int) -> Any:
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_coerce_scalar(v.strip(), path, lineno) for v in inner.split(",")]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.lower() in {"null", "~", ""}:
        return None
    for caster in (int, float):
        try:
            return caster(value)
        except ValueError:
            pass
    return value.strip("'\"")
