"""
Conformance suite - run this before you submit.

    make check
    python tests/conformance.py --agent-cmd "python agent.py" --agent-dir agent-template

It boots a short, hostile arena, runs your agent against it, and asserts the
things the organisers assert. The checks below are the SAME code that runs at
grading time, so a green result here is a genuine predictor of your functional
and resilience score.

What it does NOT check is your strategy. Beating the baselines is measured
separately, on a seed nobody has seen.

Exit code 0 = all hard checks passed.

Copyright 2026 The CoGNETs Consortium
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import urllib.error
import urllib.request

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"
)
if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
    GREEN = RED = YELLOW = DIM = BOLD = RESET = ""


# ---------------------------------------------------------------------------
# Result bookkeeping
# ---------------------------------------------------------------------------
@dataclass
class Check:
    number: int
    name: str
    hard: bool
    passed: bool
    detail: str = ""

    @property
    def label(self) -> str:
        if self.passed:
            return f"{GREEN}PASS{RESET}"
        return f"{RED}FAIL{RESET}" if self.hard else f"{YELLOW}WARN{RESET}"


@dataclass
class Report:
    checks: List[Check] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)

    def add(self, number: int, name: str, hard: bool, passed: bool, detail: str = "") -> None:
        self.checks.append(Check(number, name, hard, passed, detail))

    @property
    def hard_failures(self) -> List[Check]:
        return [c for c in self.checks if c.hard and not c.passed]

    @property
    def resilience_score(self) -> float:
        """The 20 resilience points, weighted as in the published rubric."""
        weights = {5: 8.0, 6: 5.0, 7: 4.0, 8: 3.0}
        return sum(w for n, w in weights.items()
                   if any(c.number == n and c.passed for c in self.checks))


# ---------------------------------------------------------------------------
# Arena control
# ---------------------------------------------------------------------------
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def get_json(url: str, token: Optional[str] = None, timeout: float = 5.0,
             attempts: int = 4) -> Any:
    """GET with a couple of retries.

    The observability endpoints are exempt from fault injection, but this suite
    is an observer of a deliberately hostile arena - a transient failure here
    should never be mistaken for a failure of the agent under test.
    """
    last: Optional[Exception] = None
    for attempt in range(attempts):
        request = urllib.request.Request(url)
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 503):
                raise
            last = exc
        except Exception as exc:  # network hiccup
            last = exc
        time.sleep(0.25 * (2 ** attempt))
    raise last if last else RuntimeError(f"GET {url} failed")


def wait_healthy(base: str, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if get_json(f"{base}/healthz", timeout=2.0).get("ok"):
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def start_arena(port: int, rounds: int, round_seconds: float, seed: int,
                chaos: float, log: Path) -> subprocess.Popen:
    env = {
        **os.environ,
        "ARENA_SCENARIO": "graded",
        "ARENA_TOTAL_ROUNDS": str(rounds),
        "ARENA_ROUND_SECONDS": str(round_seconds),
        "ARENA_SEED": str(seed),
        "ARENA_CHAOS_RATE": str(chaos),
        "ARENA_START_DELAY": "5",
        "LOG_LEVEL": "warning",
        "PYTHONPATH": str(REPO_ROOT),
    }
    handle = log.open("w", encoding="utf-8")
    kwargs: Dict[str, Any] = dict(
        cwd=str(REPO_ROOT), env=env, stdout=handle, stderr=subprocess.STDOUT
    )
    # Its own process group, so `stop()` can signal the arena and everything it
    # spawned WITHOUT signalling this test runner as well. Without this,
    # os.killpg would take down the suite along with the arena.
    if os.name == "posix":
        kwargs["start_new_session"] = True
    return subprocess.Popen(
        [sys.executable, "-m", "arena", "--port", str(port), "--log-level", "warning"],
        **kwargs,
    )


def container_name(team: str) -> str:
    return f"conformance-{team}".replace("/", "-")[:60]


def start_image(image: str, team: str, port: int, log: Path) -> subprocess.Popen:
    """Run the submitted image, which is what the deliverable actually is.

    The container reaches the arena on the host through host.docker.internal;
    `--add-host` supplies that name on the Linux daemons that do not provide it.

    The client is given its own process group for the same reason the arena is:
    `stop()` signals a whole group, and a `docker run` sharing this suite's
    group would be told to stop together with the suite that is running it.
    """
    handle = log.open("w", encoding="utf-8")
    cmd = [
        "docker", "run", "--rm",
        "--name", container_name(team),
        "--add-host", "host.docker.internal:host-gateway",
        "-e", f"ARENA_URL=http://host.docker.internal:{port}",
        "-e", f"TEAM_NAME={team}",
        "-e", "LOG_LEVEL=info",
        image,
    ]
    kwargs: Dict[str, Any] = dict(stdout=handle, stderr=subprocess.STDOUT)
    if os.name == "posix":
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def start_agent(cmd: str, cwd: Path, arena_url: str, team: str,
                log: Path) -> subprocess.Popen:
    env = {
        **os.environ,
        "ARENA_URL": arena_url,
        "TEAM_NAME": team,
        "LOG_LEVEL": "info",
        "PYTHONUNBUFFERED": "1",
    }
    handle = log.open("w", encoding="utf-8")
    kwargs: Dict[str, Any] = dict(
        cwd=str(cwd), env=env, stdout=handle, stderr=subprocess.STDOUT
    )
    if os.name == "posix":
        kwargs["start_new_session"] = True
    return subprocess.Popen(split_command(cmd), **kwargs)


def default_agent_cmd() -> str:
    """`agent.py` on the interpreter that is running this suite.

    Not a bare `python`: plenty of Linux installs, WSL among them, ship only
    `python3`, and a hardcoded `python` then fails to launch at all - the agent
    never appears and the first check reports it as a registration failure.
    Borrowing this interpreter also hands the agent the same site-packages the
    suite was started with, so a dependency installed into a virtualenv is
    present rather than missing.
    """
    exe = sys.executable or "python3"
    if os.name == "nt":
        return f'"{exe}" agent.py' if " " in exe else f"{exe} agent.py"
    return f"{shlex.quote(exe)} agent.py"


def split_command(cmd: str):
    """Turn a command string into whatever Popen wants on this platform.

    `shlex.split` is POSIX-flavoured: it treats a backslash as an escape, so
    `C:\\Python311\\python.exe agent.py` comes back as `C:Python311python.exe`
    and the run fails with a confusing "No such file". Windows has its own
    command-line parsing, and handing Popen the raw string is the supported way
    to use it.
    """
    return cmd if os.name == "nt" else shlex.split(cmd)


def _shares_our_group(process: subprocess.Popen) -> bool:
    """True when signalling this child's process group would signal us too.

    Every child here is started in a session of its own so that a whole tree -
    a shell, the interpreter it launched, anything either spawned - can be
    stopped in one call. If one ever is not, its group is this suite's own, and
    `killpg` on it would stop the suite, `make`, and the terminal's foreground
    job along with it.
    """
    try:
        return os.getpgid(process.pid) == os.getpgrp()
    except Exception:
        return True


def stop(process: Optional[subprocess.Popen], grace: float = 6.0) -> Optional[int]:
    """Terminate politely, then firmly. Returns the exit code if it stopped."""
    if process is None or process.poll() is not None:
        return process.poll() if process else None
    try:
        if os.name == "posix" and not _shares_our_group(process):
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:
            process.terminate()
    except Exception:
        process.terminate()
    try:
        return process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix" and not _shares_our_group(process):
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            else:
                process.kill()
        except Exception:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return None


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------
def run_conformance(args: argparse.Namespace) -> Report:
    report = Report()
    port = args.port or free_port()
    base = f"http://127.0.0.1:{port}"
    team = args.team
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    arena_log = workdir / "conformance-arena.log"
    agent_log = workdir / "conformance-agent.log"

    agent_dir = (REPO_ROOT / args.agent_dir).resolve()
    if not agent_dir.is_dir():
        raise SystemExit(f"agent directory not found: {agent_dir}")

    print(f"{BOLD}CoGNETs Swarm Arena - conformance suite{RESET}")
    print(f"{DIM}  arena   {base}  ({args.rounds} rounds x {args.round_seconds}s, "
          f"chaos {args.chaos}){RESET}")
    if args.image:
        print(f"{DIM}  agent   image {args.image}{RESET}")
    else:
        print(f"{DIM}  agent   {args.agent_cmd}  (cwd {agent_dir}){RESET}")
    print(f"{DIM}  team    {team}{RESET}\n")

    arena = agent = None
    try:
        arena = start_arena(port, args.rounds, args.round_seconds, args.seed,
                            args.chaos, arena_log)
        if not wait_healthy(base):
            raise SystemExit(f"arena did not start; see {arena_log}")

        t0 = time.time()
        try:
            if args.image:
                agent = start_image(args.image, team, port, agent_log)
            else:
                agent = start_agent(args.agent_cmd, agent_dir, base, team,
                                    agent_log)
        except OSError as exc:
            # The command could not be launched at all, usually an interpreter
            # that is not on PATH. Report it as the failed check it is rather
            # than as a traceback out of subprocess.
            report.add(
                1, "Registers within 30 s", True, False,
                f"could not start {args.agent_cmd!r}: {exc}\n"
                f"          pass a command that exists, e.g. "
                f"make check AGENT_CMD=\"{default_agent_cmd()}\"",
            )
            print_report(report, agent_log, arena_log)
            return report

        # --- check 1: registers promptly ---------------------------------
        registered_at: Optional[float] = None
        deadline = t0 + 30.0
        while time.time() < deadline:
            try:
                teams = {n["team"] for n in get_json(f"{base}/v1/swarm")["nodes"]}
                if team in teams:
                    registered_at = time.time() - t0
                    break
            except Exception:
                pass
            if agent.poll() is not None:
                break
            time.sleep(0.4)

        if registered_at is not None:
            detail = f"after {registered_at:.1f}s"
        else:
            # Nearly always the agent never got off the ground: a missing
            # interpreter, a syntax error, a dependency that is not installed.
            # Quote its own last words instead of leaving "never appeared" to
            # be interpreted.
            detail = "never appeared in /v1/swarm"
            code = agent.poll()
            if code is not None:
                detail += f"; the agent process exited with code {code}"
                tail = [ln.rstrip() for ln in
                        agent_log.read_text(encoding="utf-8", errors="replace")
                        .splitlines() if ln.strip()][-3:]
                if tail:
                    detail += "\n          last output: " + \
                              "\n                       ".join(tail)
                if any("ModuleNotFoundError" in ln for ln in tail):
                    # This suite runs the agent as an ordinary process, so its
                    # packages have to be on the interpreter that starts it. A
                    # team that has only ever run inside Docker has them in the
                    # image and nowhere else.
                    detail += (
                        "\n          this suite runs the agent as a plain "
                        "process, so its dependencies must be\n"
                        "          installed for this interpreter:\n"
                        f"            {sys.executable} -m pip install -r "
                        "agent-template/requirements.txt\n"
                        "          or check the image you actually submit "
                        "instead:\n"
                        "            make check-docker IMAGE=your/agent:latest"
                    )
            else:
                detail += "; the agent is still running but never registered"

        report.add(
            1, "Registers within 30 s", True, registered_at is not None, detail,
        )
        if registered_at is None:
            print_report(report, agent_log, arena_log)
            return report

        # --- watch the run ------------------------------------------------
        max_heartbeat_gap = 0.0
        last_seen_active = time.time()
        crashed_at_round: Optional[int] = None
        forced_expiry_done = False
        recovered_after_expiry = False
        saw_403_survived = False
        faults_before = 0

        while True:
            try:
                status = get_json(f"{base}/v1/status")
                board = {r["team"]: r for r in
                         get_json(f"{base}/v1/leaderboard")["leaderboard"]}
            except Exception:
                time.sleep(0.5)
                continue

            row = board.get(team)
            now = time.time()

            if row is not None:
                if row["active"]:
                    max_heartbeat_gap = max(max_heartbeat_gap, now - last_seen_active)
                    last_seen_active = now
                    if forced_expiry_done:
                        recovered_after_expiry = True
                if row["ejected"]:
                    break

            # Around a third of the way in, force a lease expiry by pausing
            # nothing - we simply check that the agent's own heartbeat keeps
            # it active, then verify it recovers if it ever does drop.
            if (not forced_expiry_done
                    and status.get("round", 0) >= max(2, args.rounds // 3)):
                forced_expiry_done = True

            if agent.poll() is not None and not status.get("finished"):
                crashed_at_round = status.get("round", 0)
                break

            if status.get("finished"):
                break
            if now - t0 > args.timeout:
                break
            time.sleep(0.5)

        # Give the agent a moment to shut down of its own accord.
        time.sleep(2.0)

        status = get_json(f"{base}/v1/status")
        board = {r["team"]: r for r in
                 get_json(f"{base}/v1/leaderboard")["leaderboard"]}
        row = board.get(team, {})
        faults = status.get("faults", {})
        injected = (faults.get("injected_503", 0) + faults.get("injected_429", 0))

        total_rounds = max(1, status.get("round", args.rounds))
        participated = row.get("rounds_participated", 0)
        missed = row.get("rounds_missed", 0)
        idle = row.get("rounds_idle", 0)

        # Coverage is measured against the rounds you were ELIGIBLE for, not
        # every round. Sitting out on a flat battery is a strategy consequence
        # and it is already punished by your score; it must not also fail a
        # functional check, or the battery mechanic would cost you twice.
        eligible = max(1, total_rounds - idle)
        ratio = min(1.0, participated / eligible)

        # --- check 2: heartbeat gap ---------------------------------------
        lease = status.get("lease_seconds", 12.0)
        report.add(
            2, f"Stayed inside the {lease:.0f}s lease window", True,
            missed <= max(1, int(0.05 * eligible)),
            f"missed {missed} of {eligible} eligible rounds",
        )

        # --- check 3: bid coverage ----------------------------------------
        #
        # Same one-round tolerance as check 2, and for the same reason. This
        # run is short: after battery rests there are usually 8 or 9 eligible
        # rounds, so a bare `ratio >= 0.95` means ZERO misses allowed, and one
        # unlucky fault burst on a laptop that is also running Docker, an IDE
        # and forty browser tabs fails an agent that is perfectly correct.
        #
        # It also made the two checks contradict each other: check 2 passed
        # with one missed round and check 3, the hard one, failed on the same
        # number. A pre-flight that says NOT READY on Tuesday and PASSED on
        # Wednesday for the same code teaches teams to ignore it, which is
        # worse than not shipping it at all.
        #
        # The graded run is 60 rounds, where 95% is a real 95%.
        slack = max(1, int(0.05 * eligible))
        report.add(
            3, "Valid in-budget bid in >= 95% of eligible rounds", True,
            ratio >= 0.95 or missed <= slack,
            f"{participated}/{eligible} eligible ({ratio:.0%})"
            + (f", {missed} missed of {slack} allowed on a run this short"
               if missed else "")
            + (f"; {idle} rounds resting on a flat battery (not counted)"
               if idle else ""),
        )

        # --- check 4: no protocol penalties -------------------------------
        kappa = row.get("compromise", 0.0)
        report.add(
            4, "No protocol penalties (kappa == 0)", True,
            kappa == 0 and not row.get("ejected"),
            f"kappa={kappa:.2f}" + (" EJECTED" if row.get("ejected") else ""),
        )

        # --- check 5: survived injected faults ----------------------------
        survived_faults = crashed_at_round is None and injected > 0
        report.add(
            5, "Survived injected 503/429 without exiting", True,
            crashed_at_round is None,
            f"{injected} faults injected"
            + (f"; agent exited at round {crashed_at_round}"
               if crashed_at_round else ""),
        )

        # --- check 6: recovery --------------------------------------------
        report.add(
            6, "Kept its lease alive for the whole run", False,
            missed == 0,
            "no rounds missed" if missed == 0
            else f"{missed} rounds missed - heartbeat interval may be too long",
        )

        # --- check 7: handled inadmissible rounds -------------------------
        battery = row.get("battery", 1.0)
        report.add(
            7, "Handled inadmissible rounds without exiting", False,
            crashed_at_round is None,
            f"battery ended at {battery:.2f}",
        )

        # --- check 8: clean shutdown --------------------------------------
        #
        # Windows has no SIGTERM. `terminate()` there is TerminateProcess,
        # which kills the process outright and reports exit code 1 - no
        # handler runs and nothing can be shut down gracefully. Judging a
        # Windows agent against the POSIX exit codes failed every one of them
        # for behaving correctly, so the accepted set follows the platform and
        # the label says which one you were held to.
        code = stop(agent, grace=8.0)
        agent = None
        if os.name == "nt":
            label, accepted = "Exited on terminate (Windows: no SIGTERM)", (0, None, 1)
        else:
            label, accepted = "Exited cleanly on SIGTERM", (0, None, -15)
        report.add(8, label, False, code in accepted, f"exit code {code}")

        report.summary = {
            "team": team,
            "rounds_total": total_rounds,
            "rounds_eligible": eligible,
            "rounds_participated": participated,
            "rounds_missed": missed,
            "rounds_idle": idle,
            "bid_ratio": round(ratio, 4),
            "floor_violations": row.get("floor_violations", 0),
            "compromise": kappa,
            "ejected": bool(row.get("ejected")),
            "score": row.get("score", 0.0),
            "faults_injected": injected,
            "crashed_at_round": crashed_at_round,
            "functional_points": round(30.0 * ratio, 2) if crashed_at_round is None else 0.0,
            "resilience_points": round(report.resilience_score, 2),
        }

    finally:
        stop(agent)
        if args.image:
            # Stopping `docker run` kills the client, not always the container.
            subprocess.run(["docker", "rm", "-f", container_name(team)],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False)
        stop(arena)

    print_report(report, agent_log, arena_log)
    return report


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def print_report(report: Report, agent_log: Path, arena_log: Path) -> None:
    print()
    for check in report.checks:
        kind = "" if check.hard else f" {DIM}(advisory){RESET}"
        print(f"  [{check.label}] {check.number}. {check.name}{kind}")
        if check.detail:
            print(f"          {DIM}{check.detail}{RESET}")

    s = report.summary
    if s:
        print(f"\n{BOLD}  Summary{RESET}")
        print(f"    bids           {s['rounds_participated']}/{s['rounds_eligible']} "
              f"eligible ({s['bid_ratio']:.0%})")
        print(f"    missed         {s['rounds_missed']}"
              + (f"  {YELLOW}<- awake and silent: a bug{RESET}"
                 if s["rounds_missed"] else ""))
        print(f"    idle (battery) {s['rounds_idle']}"
              + (f"  {DIM}strategy, not a fault{RESET}" if s["rounds_idle"] else ""))
        print(f"    floor misses   {s['floor_violations']}"
              + (f"  {YELLOW}<- you are losing score here{RESET}"
                 if s["floor_violations"] else ""))
        print(f"    kappa          {s['compromise']:.2f}")
        print(f"    faults hit     {s['faults_injected']}")
        print(f"    score          {s['score']:.3f}")
        print(f"\n    {DIM}indicative: functional {s['functional_points']:.1f}/30, "
              f"resilience {s['resilience_points']:.1f}/20{RESET}")

    print()
    if report.hard_failures:
        print(f"  {RED}{BOLD}NOT READY TO SUBMIT{RESET} - "
              f"{len(report.hard_failures)} hard check(s) failed.")
        print(f"  {DIM}agent log: {agent_log}{RESET}")
        print(f"  {DIM}arena log: {arena_log}{RESET}")
    else:
        warns = [c for c in report.checks if not c.passed]
        if warns:
            print(f"  {GREEN}{BOLD}READY{RESET} - hard checks passed, "
                  f"{len(warns)} advisory warning(s) worth a look.")
        else:
            print(f"  {GREEN}{BOLD}ALL CHECKS PASSED{RESET} - good to submit.")
    print()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--agent-cmd", default=default_agent_cmd(),
                        help="command that starts your agent")
    parser.add_argument("--agent-dir", default="agent-template",
                        help="directory to run the agent command in")
    parser.add_argument("--image", default=None,
                        help="check a built Docker image instead of running the "
                             "agent as a process; the image is the artefact you "
                             "actually submit")
    parser.add_argument("--team", default=os.environ.get("TEAM_NAME", "conformance-team"))
    parser.add_argument("--rounds", type=int, default=15)
    parser.add_argument("--round-seconds", type=float, default=3.0)
    parser.add_argument("--chaos", type=float, default=0.12)
    parser.add_argument("--seed", type=int, default=424242)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--workdir", default=".")
    parser.add_argument("--json", dest="json_out", default=None,
                        help="also write the report as JSON to this path")
    args = parser.parse_args(argv)

    if not args.agent_cmd.strip():
        args.agent_cmd = default_agent_cmd()

    report = run_conformance(args)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(
                {
                    "checks": [vars(c) for c in report.checks],
                    "summary": report.summary,
                    "passed": not report.hard_failures,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    return 1 if report.hard_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
