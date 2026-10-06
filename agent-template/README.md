# Your agent

You hand in your whole fork of this repository. This folder is the part you
change, and the part your Docker image is built from.

It works as shipped — it registers, keeps its lease, bids every round and
survives the faults the graded arena throws at it. It just does not bid *well*.

## The one file you have to change

**`strategy.py`** — a single function, `decide_bid`. Everything else here is
plumbing that already works, and rewriting it is a good way to lose points to a
bug rather than to a strategy.

| File | Change it? |
|---|---|
| `strategy.py` | **yes, this is the challenge** |
| `agent.py` | no. The main loop: registration, heartbeat thread, round loop, result fetch, clean shutdown |
| `client.py` | no. HTTP with exponential backoff, jitter, auto re-registration, typed exceptions |
| `runner.py` | optional. A compact reusable version of the loop if you prefer it to `agent.py` |
| `Dockerfile` | only if you add dependencies |
| `requirements.txt` | if you add dependencies |

## Run it

```bash
# from the repository root, with the arena already up
make agent

# or natively
ARENA_URL=http://localhost:8080 TEAM_NAME=team-kappa python agent.py
```

Both variables come from the environment. **Do not hardcode them** — the
grading harness injects its own, and hardcoding costs engineering points.

## Before you submit

```bash
make check
```

Runs the organisers' conformance suite against your agent, fault injection
included. Green here predicts your functional and resilience score.

Then check the boring things, because this is where strong teams lose points:

- Your repository clones and builds on a machine that has never seen your
  laptop. No `.env` committed, no absolute paths.
- Every dependency you installed by hand is in `requirements.txt`.
- `docker build .` works from a clean clone.
- Your README says what your strategy does, in 300 words or fewer.

## Where the wins are

Read [`../docs/strategy-primer.md`](../docs/strategy-primer.md).
