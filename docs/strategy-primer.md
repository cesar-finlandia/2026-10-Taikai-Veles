# Strategy primer

Everything you need to beat the baseline bots, and nothing you don't. Read it
before you write code — it is three pages and it will save you two hours.

No game theory background is assumed.

---

## 1. The shape of the problem

Every round you get a **budget** `W` (around 1.0, redrawn each round, does not
carry over) and you split it across three pools:

```
bid = { "compute": b_C, "energy": b_E, "security": b_S }     with   Σ b ≤ W
```

The arena hands out each pool in proportion to the bids on it:

```
x_k  =  C_k · b_k / (Σ_j b_j^k)
```

`C_k` is the pool's size this round. The denominator includes **your own bid**,
which is why your share flattens out: against a field of total `S_k`, spending
`S_k` gets you half the pool, and spending `2·S_k` gets you only two thirds.

Your payoff is a CES utility over what you won, weighted by your device's own
preference weights, with ρ = 0.5:

```
u  =  ( w_C·x_C^ρ + w_E·x_E^ρ + w_S·x_S^ρ ) ^ (1/ρ)
```

Concave and increasing in every dimension. Your score is the sum of `u` over
every round you take part in.

---

## 2. Start by not overthinking it

Two things are true and worth getting out of the way early.

**Spend your whole budget.** There is no interest on unspent budget and it does
not carry over. Within a round, holding money back is just a smaller share for
no benefit. The question is always *where*, never *how much*.

**Bidding your weights is a decent split.** We measured the exact optimal split
(the Kelly best response) against simply bidding in proportion to your weights.
The difference is about **2%**. In a field this size, weight-proportional is
very nearly right.

So the strategy is *not* in the split. That is deliberate — it is why the
template hands you a working `naive-max` and dares you to beat it.

The two things that do matter are both **constraints**, and both are visible in
the data the API already gives you.

---

## 3. Insight one: energy is not free

**This is worth about 30%. It is the whole game.**

The energy share you *win* is drawn from your own battery:

```
drain  =  battery_drain · x_E · (1 + mobility)  +  idle_drain
```

When your charge falls to the cutoff (0.05) your node becomes **inadmissible**.
You do not merely score less — you sit the round out entirely, scoring
**nothing**. You recharge while resting, then rejoin.

`naive-max` never notices. It hoovers up energy, flattens itself, and spends
roughly **a third of the run** asleep. That is the hole you climb out of.

Everything you need is in your round result:

```python
last = history[-1]
last["battery"]                  # what is left
last["battery_drawn"]            # what that round cost you
last["admissible_next_round"]    # are you about to lose a round
last["allocation"]["energy"]     # the share that caused the drain
```

Your current charge is also in `profile["features"]["battery"]`, which the
client refreshes every round — use whichever reads better. `profile` gives you
the latest value; `history` gives you the whole trace.

**What to do about it** is genuinely open. Some directions, roughly in order of
effort:

- A hard floor: bid nothing on energy below some charge level.
- A taper: scale your energy bid by remaining charge, so you ease off
  gradually instead of hitting a wall.
- Taper from *full* charge rather than waiting for trouble. `proportional` only
  reacts at 25%, by which point it is already a round or two from resting —
  that lateness is most of why you can beat it.
- Reason about the duty cycle properly: resting recovers `recharge_rate` per
  round, so there is a sustainable rate of energy draw. Work out what it is and
  hold yourself to it.

The freed budget has to go somewhere. Compute and security cost nothing to
hold, so moving it there is free.

---

## 4. Insight two: never eat a floor penalty

**Worth a few percent, and it is nearly free once you see it.**

Your device carries two floors:

- `q_min` — minimum **compute** share
- `s_min` — minimum **security** share

Miss one and the round's utility is **halved**. Miss both and it is
**quartered**. Check it:

```python
history[-1]["floor_violations"]   # ["qos_floor"], ["security_floor"], or both
```

If that list is not empty you are throwing away half a round, and no amount of
clever bidding elsewhere earns it back.

To fix it you need the minimum bid that buys a given share. Invert the Kelly
rule — set `C_k·b/(b + S_k) = t` and solve for `b`:

```
b_k  =  S_k · t / (C_k − t)
```

Bid at least that much on compute and security, then split whatever is left.

---

## 5. How to estimate `S_k`

Both formulas above need `S_k`, the rest of the swarm's total bid on resource
`k`. You are told enough to recover it exactly.

The arena publishes the price that cleared the previous round:

```
λ_k  =  (total bid on k) / C_k
```

So:

```
total bid on k  =  λ_k · C_k
S_k             =  total bid on k  −  (your own bid on k)
```

and both terms are sitting in your last result:

```python
def estimate_others(history, resource):
    if not history:
        return 1.0
    last = history[-1]
    total = last["prices"][resource] * last["capacities"][resource]
    return max(1e-4, total - last["bid"][resource])
```

One round is noisy and the field genuinely moves, so smooth it over the last
three or four rounds rather than trusting a single sample.

---

## 6. A sketch of a decent agent

Not the answer — a shape to react against.

```python
def decide_bid(budget, prices, capacities, profile, history):
    w = profile["weights"]
    bid = {k: budget * w[k] for k in ("compute", "energy", "security")}

    # 1. don't lose the round to a flat battery
    battery = history[-1]["battery"] if history else 1.0
    keep = clamp(battery, 0.05, 1.0)          # your rule goes here
    freed = bid["energy"] * (1 - keep)
    bid["energy"] *= keep
    bid["compute"]  += freed / 2
    bid["security"] += freed / 2

    # 2. don't lose half the round to a floor
    S = {k: estimate_others(history, k) for k in bid}
    for k, floor in (("compute", profile["q_min"]),
                     ("security", profile["s_min"])):
        need = S[k] * floor / (capacities[k] - floor)
        if bid[k] < need:
            take_from_others(bid, k, need - bid[k])

    return rescale_to(bid, budget)
```

The interesting decisions are `keep` and how you fund the floor top-up. Both
are yours.

---

## 7. Measuring instead of guessing

Change one thing at a time and let the numbers decide. You can run as many
practice arenas as you like:

```bash
make up          # a fresh arena and three bots
make agent       # your agent against them
make board       # the standings
```

Two habits worth having:

- **Log your own diagnostics.** Rounds idle, floor misses, average battery,
  utility per round. If you cannot see it you cannot improve it.
- **Compare like with like.** The arena is seeded, so the same scenario and
  seed replays exactly. Change your strategy, not the seed, when you are
  measuring an improvement.

`GET /v1/leaderboard` shows the bots' `rounds_idle` and `floor_violations` too,
so you can see exactly where they are losing and check you are not losing the
same way.

---

## 8. If you have time left

None of these will move your score much. They are for the **Insight & pitch**
section of the rubric, which is ten points and is where an honest piece of
analysis is worth more to you than another 1% of score:

- Derive the exact Kelly best response and confirm for yourself that it is only
  worth ~2%. A clean negative result, honestly measured, is a good talk.
- Plot your cumulative score against the three baselines and explain the
  crossings.
- Work out the sustainable energy duty cycle analytically and compare it to
  what your agent actually does.
- Report the swarm's log social welfare (`/v1/status`) with and without your
  agent, and the other nodes' scores from the leaderboard. Did your strategy
  make the swarm better off, or did it take from its neighbours? Expect the
  second. Saying so plainly, with numbers, is the strongest thing you can put
  in a write-up.

---

## 9. Cheat sheet

| Symbol | Meaning | Where |
|---|---|---|
| `W` | your budget this round | `GET /v1/round` → `budget` |
| `C_k` | pool size this round | `GET /v1/round` → `capacities` |
| `λ_k` | last round's clearing price | `GET /v1/round` → `prices` |
| `w_k` | your preference weights | registration → `profile.weights` |
| `q_min`, `s_min` | your service floors | registration → `profile` |
| `x_k` | what you actually won | result → `allocation` |
| `S_k` | the rest of the field's bid | `λ_k · C_k − your bid` |

| Rule | Formula |
|---|---|
| Allocation | `x_k = C_k · b_k / Σ_j b_j` |
| Utility | `u = (Σ w_k x_k^0.5)^2` |
| Floor penalty | halve per missed floor |
| Battery drain | `battery_drain · x_E · (1 + mobility) + idle_drain` |
| Min bid for share `t` | `b_k = S_k · t / (C_k − t)` |
