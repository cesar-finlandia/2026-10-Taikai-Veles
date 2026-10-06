# How this maps onto CoGNETs

This challenge is a deliberate reduction of a real research framework. This
page says exactly which parts you are touching, which parts were left out, and
where the simplifications are.

---

## The project

[CoGNETs](https://cognets.eu/) is an EU research project on device-centric
networks: heterogeneous IoT nodes — edge boards, phones, cameras, industrial
PLCs — organising themselves into **IoT-to-Cloud swarms** that cooperatively
run data-intensive services such as federated learning, distributed intrusion
detection and sensor fusion.

The defining constraint is that a CoGNETs swarm operates **autonomously at the
IoT-edge domain**: no central orchestrator allocates work, conflicts are
resolved by the devices' own decisions, and no device is guaranteed to stay in
the swarm for the duration of a service. Devices join and leave at any time.

Every round, the swarm has to resolve four interlocking problems:

1. **Pricing** — how to price a scarce resource so each device's contribution
   reflects its marginal value to the swarm.
2. **Allocation** — given those prices, how each device splits its capacity
   across resources and services, respecting per-device constraints (battery
   floors, minimum security, mobility).
3. **Discrete assignment** — when a service decomposes into labelled tasks
   (FL trainers, layer slices, aggregators), which task runs on which device.
4. **Participation** — which devices actively contribute, which hold a passive
   replica, and which only help maintain the swarm.

---

## The four-stage game pipeline

CoGNETs answers all four with a pipeline where each stage's output is exactly
the next stage's input.

| Stage | Game | Produces | In this challenge? |
|---|---|---|---|
| 1 | Nash Bargaining (closed-form KKT) | unit prices `λ_k` | **yes**, simplified |
| 2 | Kelly mechanism + synchronous best-response dynamics | resource shares `x_i^k` | **yes** |
| 3 | Projected Tullock Blotto contest | task win probabilities `P_ij` | no |
| 4 | Capacity-constrained Gale–Shapley co-matching | assignment `σ : J → S` | no |

You implement a **device agent that plays stages 1 and 2**. Stages 3 and 4 are
where a service's discrete tasks get handed to specific devices, and they need
a service catalogue and a role layer that would not fit in two days.

---

## What is faithful

**The Kelly mechanism (stage 2).** Exactly as CoGNETs uses it:

```
x_i^k = C_k · b_i^k / Σ_j b_j^k
```

Proportional share, diminishing returns, and an interior Nash equilibrium that
is the fixed point of the best-response map.

**The CES utility.** CoGNETs encodes the Computing-vs-Energy-vs-Security
trade-off as a constant-elasticity-of-substitution production utility. Same
functional form here, same role, ρ = 0.5 so σ = 2.

**Congestion prices as the market signal.** In the full framework stage 1's
bargaining solution yields `λ*_k = W_k (Σ a_i^k)/(C_k − Σ d_i^k)` analytically,
and those prices propagate into stage 2's best response. Here we publish the
price that actually cleared the previous round. Different derivation, same
job: it is the information an agent best-responds to.

**The admissibility gate.** CoGNETs Definition 2.1 admits a device at round `t`
when its compromise level `κ_i < κ̄`, its battery `b_i > b̲`, and its minimum
QoS and security shares are jointly satisfiable. All three are implemented —
`κ` grows on protocol violations, the battery cutoff makes a node inadmissible,
and `q_min`/`s_min` are the floors.

**The device feature vector.** `h_i = (c_i^C, c_i^R, b_i, s_i, κ_i, m_i)` —
compute capacity, RAM, battery, security posture, compromise, mobility — is
what your profile carries, in that shape.

**Join/leave without warning.** Lease expiry and re-admission are the framework's
churn semantics: a device that goes quiet stops being scored, and rejoining
preserves its standing but not the rounds it missed.

**Log social welfare.** `Σ_i log(u_i)` is the framework's headline efficiency
metric, reported here every round. Nothing in the scoring depends on it. In the
full pipeline the Stage 1 bargaining prices are what keep individual and
collective outcomes aligned; this reduction publishes last round's clearing
price instead, so that alignment is gone and playing well costs the swarm. See
"What is simplified, and honestly" below.

---

## What is simplified, and honestly

**Prices are realised, not bargained.** We publish last round's clearing price
instead of solving the Nash Bargaining Solution. The signal plays the same role
in your decision; the derivation is not the same.

**No SBRD convergence loop.** In CoGNETs, stage 2 is solved by synchronous
best-response dynamics that converge to the unique interior NE within a round.
Here one round is one shot, and the dynamics play out *across* rounds instead.

**Services are not modelled.** There is no service catalogue, no task
decomposition, no FL aggregator, no requester/replica/helper role layer. The
three pools are abstract rather than tied to named workloads.

**The battery coupling is ours.** In CoGNETs, energy is a priced resource and
battery is a device feature with an admissibility floor. Making the charge
drain *in proportion to the energy share won* is a modelling choice made for
this challenge, to give both hackathon days a genuine intertemporal trade-off.
It is faithful in spirit — energy-aware edge computing is a core project
concern — but you will not find that exact coupling in the deliverables.

**Everything is normalised.** Pools sit around 1.0 and budgets around 1.0.
CoGNETs recovers absolute values by multiplying through the swarm's
instantaneous capacity envelope.

**The field is tiny.** Four nodes, not a swarm of hundreds. This matters more
than it sounds: with four similar devices the optimal split is close to
weight-proportional, which is why the exact best response is worth only ~2%
here and would be worth considerably more in a large heterogeneous swarm.

---

## If you want to go further

**Stage 3 — the Blotto contest.** Add `M` service tasks per round, each with a
per-dimension demand vector `v_j = (v_C, v_E, v_S)` and a value `α_j`. Each
device spreads a unit of contest effort across tasks; a projected Tullock
contest turns effort and capability into win probabilities
`P_ij = φ_ij / Σ_ℓ φ_ℓj`. The interesting part is that your stage-2 allocation
determines your capability in stage 3, so the two couple.

**Stage 4 — CO-Matching.** A capacity-constrained Gale–Shapley over CES
preference scores produces a stable integer assignment of tasks to devices, and
terminates in at most `n²` proposals. Stability under requester pinning is what
guarantees the device that asked for a service takes part in it.

Both are described in the CoGNETs D3.1+/D3.2 deliverable, along with the full
algorithm catalogue and the simulator that produced the project's numerical
results.

---

## The honest summary

You are implementing a real mechanism from a real research framework, reduced
until it fits in a day, with the simplifications written down rather than
hidden. The Kelly allocation, the CES payoff, the admissibility floors and the
churn semantics are the genuine article. The pricing derivation, the service
layer and the last two stages are not here.

If you leave understanding *why* a proportional-share mechanism punishes greed,
and having felt what it is like when your node drops out of a swarm because you
drew too much power, you have got the two lessons that matter — and neither of
them required you to read a word about Nash bargaining.
