"""
Run the arena without Docker:

    pip install -e .
    python -m arena                     # practice scenario on :8080
    python -m arena --scenario graded    # the hostile one
    python -m arena --port 8081          # a second arena alongside the first

This is the fallback path for laptops where Docker Desktop is blocked. It is a
fully supported way to run the challenge, not a degraded one.

Copyright 2026 The CoGNETs Consortium
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import argparse
import logging
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m arena",
        description="Run the CoGNETs Swarm Arena locally.",
    )
    parser.add_argument(
        "--scenario",
        default=os.environ.get("ARENA_SCENARIO", "practice"),
        help="scenario name (practice, graded) or a path to a YAML file",
    )
    parser.add_argument("--host", default=os.environ.get("ARENA_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("ARENA_PORT", "8080"))
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="override the scenario seed (the grading harness uses this)",
    )
    parser.add_argument(
        "--rounds", type=int, default=None, help="override total_rounds"
    )
    parser.add_argument(
        "--round-seconds", type=float, default=None, help="override round_seconds"
    )
    parser.add_argument(
        "--log-level", default=os.environ.get("LOG_LEVEL", "info"),
        help="uvicorn/arena log level",
    )
    args = parser.parse_args(argv)

    try:
        import uvicorn
    except ImportError:
        print(
            "uvicorn is not installed.\n"
            "  pip install -e .          (from the repository root)\n"
            "or\n"
            "  pip install -r arena/requirements.txt",
            file=sys.stderr,
        )
        return 1

    # Environment overrides are read by ScenarioConfig.load().
    os.environ["ARENA_SCENARIO"] = args.scenario
    if args.seed is not None:
        os.environ["ARENA_SEED"] = str(args.seed)
    if args.rounds is not None:
        os.environ["ARENA_TOTAL_ROUNDS"] = str(args.rounds)
    if args.round_seconds is not None:
        os.environ["ARENA_ROUND_SECONDS"] = str(args.round_seconds)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    from .config import ScenarioConfig
    from .main import create_app

    config = ScenarioConfig.load(args.scenario)
    app = create_app(config)

    print(
        f"\n  CoGNETs Swarm Arena"
        f"\n  scenario   {config.name}"
        f"\n  rounds     {config.total_rounds} x {config.round_seconds}s"
        f"\n  chaos      {config.chaos_rate:.2f}   latency {config.latency_ms}ms"
        f"\n  seed       {config.seed}"
        f"\n  leaderboard  http://localhost:{args.port}/"
        f"\n  api docs     http://localhost:{args.port}/docs\n",
        flush=True,
    )

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
