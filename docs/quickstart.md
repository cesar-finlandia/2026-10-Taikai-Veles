# Quickstart

Target: a node registered and bidding within **30 minutes** of kick-off. If it
takes longer than that, find a mentor — that is what we are here for.

---

## 1. Get the environment up

```bash
git clone https://github.com/czavitsanos-iti/veleshack-2026-4th-challenge
cd veleshack-2026-4th-challenge
cp .env.example .env          # then set TEAM_NAME in it
make up
```

`make up` builds and starts the arena plus your three opponents. Open
**http://localhost:8080** and watch a round settle. You should see three bots
on the board within a few seconds.

If `make` is not available (Windows without WSL, say), the underlying command
is:

```bash
docker compose up -d --build arena bot-naive-max bot-even-split bot-proportional
```

## 2. Get yourself on the board

```bash
make agent
```

That builds and runs the template agent in the foreground. It already
registers, heartbeats, bids every round, and survives the faults the graded
arena throws at it. Within a few seconds your team appears on the leaderboard,
**losing to `bot-naive-max`** — which is running exactly the strategy the
template ships with.

That is your starting line.

## 3. Make it yours

Edit **`agent-template/strategy.py`**. It is the only file you have to change.
Then:

```bash
make agent        # rebuild and run again
make board        # print the standings
```

Read [`strategy-primer.md`](strategy-primer.md) first. It is three pages and it
tells you exactly where the wins are.

## 4. Rehearse under fire

```bash
make graded
```

Restarts the arena on the graded scenario: 60 rounds, shorter windows, injected
`503`s and latency. This is the shape of the run you are scored on. An agent
that is comfortable here is an agent that will finish.

## 5. Check before you submit

```bash
make check
```

Runs the same conformance suite the organisers run, including the fault
injection. Green here is a genuine predictor of your functional and resilience
score. Do not submit without running it.

---

## Useful commands

| Command | What it does |
|---|---|
| `make up` | arena + three baseline bots |
| `make agent` | build and run your agent |
| `make graded` | restart on the hostile scenario |
| `make check` | the conformance suite, running your agent as a local process |
| `make check-docker IMAGE=you/agent` | the same suite against your built image |
| `make board` | print the leaderboard |
| `make status` | print the run status as JSON |
| `make logs` | follow the arena's logs |
| `make down` | stop everything |
| `make reset` | wipe the run and start fresh |

`make check` runs your agent as an ordinary process rather than in a container,
so its dependencies have to be installed for the Python that starts it:

    python3 -m pip install -r agent-template/requirements.txt

If you would rather stay in Docker, build your image and use `make check-docker`
instead - that checks the artefact you actually submit, and needs nothing
installed on your machine.

## Iterate faster

The graded scenario is 60 rounds of 4 seconds, so every experiment costs four
minutes. While you are developing, shorten it:

    ARENA_SCENARIO=graded ARENA_ROUND_SECONDS=0.6 ARENA_LEASE_SECONDS=2.0 \
    ARENA_TOTAL_ROUNDS=60 ARENA_SEED=7 python -m arena

Same game, same maths, about 40 seconds instead of four minutes. Two things to
know: `lease_seconds` must stay above `round_seconds` or the arena refuses to
start and says so, and `round_seconds` has a floor of 0.5.

Fix the seed while you are comparing two versions of your strategy, and change
it afterwards - a change that only helps on one seed has not helped.

---

## No Docker? That is fine

Docker Desktop is blocked on plenty of university and corporate laptops. The
pip path is **fully supported**, not a degraded fallback, and a non-containerised
submission still scores (with a small engineering deduction, because the
deliverable was specified as an image).

```bash
python -m pip install -e .

# terminal 1 - the arena
python -m arena --scenario practice

# terminal 2 - the three bots
make dev-bots

# terminal 3 - your agent
cd agent-template
ARENA_URL=http://localhost:8080 TEAM_NAME=team-kappa python agent.py
```

Python 3.10 or newer. On Windows without `make`, run the three bots by hand:

```powershell
$env:ARENA_URL="http://localhost:8080"
$env:BOT="naive-max";     $env:TEAM_NAME="bot-naive-max";     python baselines\bot.py
# ...and again for even-split and proportional, in their own terminals
```

---

## Troubleshooting

**Port 8080 is already in use.**
Set `ARENA_PORT=8090` in your `.env`, or pass `--port 8090` to `python -m arena`.

**My node keeps disappearing from the leaderboard.**
Your heartbeat interval is too close to the lease. Send one every
`lease_seconds / 3`, on its own thread, *not* inside the bidding loop.

**My utility is about half what I calculate.**
You are missing a service floor. Check `floor_violations` in
`GET /v1/result/{round}` — one missed floor halves the round, both quarters it.

**My agent stops partway through a run.**
An unhandled `503` or `410`. Catch, back off, continue. Never exit the process
on a transient status. The shipped `client.py` handles this; if you replaced
it, you own the retries.

**I keep going idle and I don't know why.**
Your battery is flat. The energy you *win* drains it — see
`battery_drawn` in your round results. This is the central mechanic of the
challenge, not a bug. The primer explains what to do.

**Nothing happens for the first few seconds after `make up`.**
The arena waits `start_delay_seconds` (8 in practice, 10 in graded) before
round 1, so slow agents can register. That is normal.

**`docker compose` says `TEAM_NAME` is not set.**
You have not created `.env` yet: `cp .env.example .env` and edit it.

---

## Where things live

```
arena/              the environment. Open source on purpose - read it
agent-template/     your agent. Only strategy.py needs to change
baselines/          the three bots you are up against
tests/              the conformance suite
docs/               you are here
```
