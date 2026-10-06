# API reference

Base URL `http://localhost:8080`. Everything is JSON.

Auth is `Authorization: Bearer <token>` on every endpoint except
`/v1/register`, `/healthz` and the leaderboard page.

Interactive Swagger UI is at **`/docs`** — the fastest way to make your first
successful request.

---

## Quick map

| Method | Path | What it does |
|---|---|---|
| `POST` | `/v1/register` | join the swarm, get a token and your device profile |
| `POST` | `/v1/heartbeat` | renew your lease |
| `GET` | `/v1/round` | the round currently open |
| `POST` | `/v1/bid` | submit this round's bid |
| `GET` | `/v1/result/{round}` | what you were allocated and scored |
| `GET` | `/v1/me` | your full node state and recent history |
| `GET` | `/v1/leaderboard` | ranked standings |
| `GET` | `/v1/swarm` | every node's public profile |
| `GET` | `/v1/status` | run status, LSW series, injected-fault counters |
| `GET` | `/v1/events` | recent arena events, for debugging |
| `GET` | `/healthz` | liveness — never faulted |

---

## `POST /v1/register`

Join, or rejoin after your lease expired. Re-registering with the same team
name returns the **same node with a new token**: your cumulative score
survives, the rounds you missed do not.

```bash
curl -s localhost:8080/v1/register \
  -H 'content-type: application/json' \
  -d '{"team":"team-kappa"}'
```

```json
{
  "node_id": "node-004",
  "token": "K3xR...",
  "profile": {
    "features": { "compute_cap": 0.72, "ram_cap": 0.55, "battery": 0.91,
                  "security_posture": 0.68, "compromise": 0.0, "mobility": 0.19 },
    "weights":  { "compute": 0.41, "energy": 0.28, "security": 0.31 },
    "q_min": 0.161, "s_min": 0.104
  },
  "arena": {
    "scenario": "practice", "round_seconds": 6.0, "total_rounds": 40,
    "lease_seconds": 12.0, "resources": ["compute","energy","security"],
    "rho": 0.5, "kappa_bar": 0.6, "battery_cutoff": 0.05
  }
}
```

Your profile is derived deterministically from your team name, so it is the
same in every run you do and different from every other team's. **Read it** —
the right strategy is not the same for two different devices.

| Status | Meaning |
|---|---|
| `403` | your team was ejected from this run |
| `400` | empty or over-long team name |

---

## `POST /v1/heartbeat`

```json
{ "ok": true, "lease_expires_in": 12.0, "round": 12, "active": true }
```

Send one every `lease_seconds / 3`, **on its own schedule** — not inside your
bidding loop. If you only heartbeat when you bid, then one round you sit out
(flat battery, a slow retry) lets your lease expire and you silently stop being
scored. This is the single most common way teams lose points.

`401` means your token is no longer accepted: re-register.

---

## `GET /v1/round`

Works unauthenticated, but send your token — the `budget` and `you` fields only
appear when it can identify you.

```json
{
  "round": 12,
  "capacities": { "compute": 1.12, "energy": 0.87, "security": 1.03 },
  "prices":     { "compute": 2.41, "energy": 1.88, "security": 1.12 },
  "budget": 1.07,
  "opens_at": 1759740000.0,
  "closes_at": 1759740006.0,
  "seconds_remaining": 3.8,
  "settled": false,
  "you": {
    "node_id": "node-004",
    "already_submitted": false,
    "admissible": true,
    "battery": 0.62,
    "compromise": 0.0
  }
}
```

- `capacities` — the pool sizes **this** round.
- `prices` — the prices that **cleared the previous** round,
  `λ_k = (total bid on k) / C_k`. Floored at 0.01 so you can safely divide.
- `admissible: false` — you may not bid this round. Sit it out; do not crash.

`404` means no round is open yet (the arena is still in its start delay).

---

## `POST /v1/bid`

```bash
curl -s localhost:8080/v1/bid \
  -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"round":12,"bid":{"compute":0.51,"energy":0.32,"security":0.24}}'
```

```json
{
  "accepted": true,
  "round": 12,
  "bid": { "compute": 0.51, "energy": 0.32, "security": 0.24 },
  "spend": 1.07,
  "budget": 1.07,
  "warnings": []
}
```

Sending `round` is optional but recommended — it lets the arena reject a stale
bid with `422` instead of silently accepting it against the wrong round.

| Status | Meaning | What to do |
|---|---|---|
| `200` | accepted | check `warnings` |
| `400` | malformed body | fix your JSON |
| `403` | not admissible (flat battery, or ejected) | skip the round |
| `409` | you already bid this round | skip ahead |
| `410` | the round closed before your bid landed | do **not** retry it |
| `422` | you bid on a round that is not the open one | re-read `/v1/round` |
| `429`/`503` | injected fault | retry with backoff and jitter |

### Warnings and the compromise score κ

Every warning except `unknown_resource` raises your compromise score by
`kappa_penalty` (0.05). Reach `kappa_bar` (0.60) — twelve violations — and you
are **ejected for the rest of the run**.

| Warning | Cause | Costs κ |
|---|---|---|
| `negative_bid:<k>` | a negative value | yes |
| `non_numeric_bid:<k>` | not a number | yes |
| `invalid_bid:<k>` | NaN or infinity | yes |
| `over_budget` | the sum exceeded your budget | yes |
| `unknown_resource:<names>` | keys we don't recognise | no — curiosity is free |

Validate your own bid before you send it. The shipped template already clamps
for you; if you replace it, keep the clamp.

---

## `GET /v1/result/{round}`

The most useful endpoint in the API. Everything you need to improve is here.

```json
{
  "round": 12,
  "participated": true,
  "allocation": { "compute": 0.29, "energy": 0.11, "security": 0.18 },
  "bid":        { "compute": 0.51, "energy": 0.32, "security": 0.24 },
  "spend": 1.07,
  "prices":     { "compute": 2.41, "energy": 1.88, "security": 1.12 },
  "capacities": { "compute": 1.12, "energy": 0.87, "security": 1.03 },
  "utility_raw": 0.196,
  "utility": 0.098,
  "floor_violations": ["security_floor"],
  "battery_drawn": 0.043,
  "battery": 0.577,
  "admissible_next_round": true,
  "round_score": 0.098,
  "cumulative_score": 2.41
}
```

- `utility_raw` vs `utility` — the gap is your floor penalty. If they differ,
  `floor_violations` says which floor you missed and you have just lost half
  the round.
- `battery_drawn` — what the energy you won cost you in charge.
- `admissible_next_round` — `false` means you are about to lose a whole round.

`404` means the round has not settled yet. Try again shortly.

---

## `GET /v1/me`

Your node state plus the last 20 rounds of history: profile, budget, score,
`rounds_participated`, `rounds_missed`, `rounds_idle`, `floor_violations`,
`compromise`, `battery`, `active`, `ejected`, `admissible`, and any
`protocol_warnings`.

`rounds_missed` and `rounds_idle` are different and both matter:

- **missed** — you were awake and allowed to bid, and said nothing. A bug.
- **idle** — you were resting on a flat battery. A strategy problem.

---

## `GET /v1/leaderboard`

```json
{
  "leaderboard": [
    { "rank": 1, "team": "team-kappa", "node_id": "node-004",
      "score": 12.83, "utility_total": 12.83,
      "rounds_participated": 57, "rounds_missed": 0, "rounds_idle": 3,
      "floor_violations": 1, "compromise": 0.0, "battery": 0.71,
      "active": true, "ejected": false, "is_baseline": false }
  ],
  "status": { "...": "same shape as /v1/status" }
}
```

Unauthenticated. The bots' rows are visible too, which is the cheapest way to
see where they are losing.

---

## `GET /v1/status`

```json
{
  "scenario": "graded", "started": true, "finished": false,
  "round": 34, "total_rounds": 60, "round_seconds": 4.0,
  "lease_seconds": 12.0, "chaos_rate": 0.08,
  "nodes_registered": 5, "nodes_active": 5, "nodes_ejected": 0,
  "last_lsw": -7.42, "lsw_series": [-8.1, -7.9, "..."],
  "seconds_remaining": 2.3,
  "faults": { "chaos_rate": 0.08, "latency_ms": 250,
              "injected_503": 41, "injected_429": 19 }
}
```

`lsw` is the swarm's log social welfare, `Σ_i log(u_i)`, the CoGNETs efficiency
metric. No points ride on it. It is there so you can measure whether your
strategy helped the swarm or took from it, which is worth reporting either way.

---

## Injected faults

In the graded scenario the arena returns `503` (with `Retry-After: 1`) and
`429` at random, and adds up to 250 ms of latency to `/v1/bid`.

`/healthz`, `/v1/register`, `/docs` and the leaderboard page are **never**
faulted — a team that cannot register cannot start, and that is a support call
rather than a lesson.

**The three baseline bots are never faulted either.** They are the market you
bid against, and your strategy points are worked out by re-running that same
market with two known strategies in your place — see *Evaluation* in the
README. If the bots dropped rounds to injected faults, your run would have
faced a thinner market than those two comparisons did, and part of your score
would be the arena's dice rather than your ideas. Faults stay where they
belong: on the agent being scored for resilience, which is yours.

Retry with exponential backoff **and jitter**. The shipped `client.py` already
does; if you write your own, do the same. Never exit the process on a transient
status.

---

## Status codes at a glance

| Code | Meaning |
|---|---|
| `200` | fine |
| `400` | your request was malformed |
| `401` | bad or expired token — re-register |
| `403` | not admissible, or ejected |
| `404` | no round open, or that round has not settled |
| `409` | already submitted for this round |
| `410` | the round closed |
| `422` | wrong round number |
| `429` | rate limited (injected) |
| `503` | temporarily unavailable (injected) |
