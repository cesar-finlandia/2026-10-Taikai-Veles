# TASK — Generate the Master Blueprint + Design Plans for the VelesHack Challenge 4 entry

You are a principal engineer preparing a hackathon entry. Your job: read the
material below, then produce ONE Master Blueprint plus a set of implementation-ready
design plans. The implementor (operator + AI assistant) will build strictly from your
plans. Goal, simple and absolute: **maximize the 100-point graded score AND win
Challenge 4 (COGNETS): Smart Edge Resource Auctions** (€500 winner prize).

This is NOT a chassis-assembly entry. No chassis runtime modules are used — every
line of product code is written fresh during the hackathon (template + own code only).
There is no sweep harness; you write all plans in this run, implementation follows.

## PATH RESOLUTION — read this before opening any file

Execute this prompt with the ENTRY repository root as the working directory.

| root | from the entry root | absolute (this machine, at generation time) |
|---|---|---|
| ENTRY (cwd) | `.` | `C:/Users/cesar/Documents/CursorAI-projects/hackathon-entries/2026-10-Taikai-Veles` |
| PROJECT_DIR (brief + track files) | `../../hackathons/hackathon-projects/2026-10-Taikai-Veles` | `C:/Users/cesar/Documents/CursorAI-projects/hackathons/hackathon-projects/2026-10-Taikai-Veles` |
| CHASSIS (read-only reference) | `../../hackathons` | `C:/Users/cesar/Documents/CursorAI-projects/hackathons` |

1. A path with no prefix (`design_documents/…`, `agent-template/…`, `arena/…`) is ENTRY-local.
2. `design_documents/hackathon_brief.md` is the entry-local copy of the event brief — §4.11
   holds the binding Challenge 4 facts. The full track PDFs live ONLY in PROJECT_DIR:
   `../../hackathons/hackathon-projects/2026-10-Taikai-Veles/cognets/Challenge 4 - COGNETS.pdf`
   and `.../cognets/Challenge 4 COGNETS Introduction.pdf`.
3. If a relative path does not resolve (repos moved), use the absolute column — but never
   write an absolute path into a plan.
4. Report a file as missing only after trying every lookup. Never design against a file
   you could not open.

**Preflight — run first; every line must print a path, not an error:**

```bash
ls design_documents/hackathon_brief.md agent-template/strategy.py agent-template/agent.py \
   agent-template/client.py baselines/bot.py docs/strategy-primer.md docs/quickstart.md \
   docs/api.md tests/ Makefile docker-compose.yml
```

## READ FIRST (in this order)

1. `design_documents/hackathon_brief.md` §4.11 — binding task, game rules, grading rubric,
   submission checklist, deadlines. Source of truth for WHAT counts.
2. PROJECT_DIR `cognets/Challenge 4 - COGNETS.pdf` + `cognets/Challenge 4 COGNETS Introduction.pdf`
   — the organizers' full spec (take precedence over the brief on any conflict).
3. ENTRY `docs/strategy-primer.md` — the organizers' own strategy guide. Internalize §3–§5
   (battery ≈ 30%, floors ≈ few %, S_k estimation, duty-cycle reasoning) and §7–§8
   (measure-don't-guess protocol, Insight-point analyses).
4. ENTRY `docs/quickstart.md`, `docs/api.md` — endpoints, status codes, failure modes.
5. ENTRY `agent-template/strategy.py`, `agent-template/agent.py`, `agent-template/client.py`
   — what the template already does (loop, retry/backoff, heartbeat). Read, don't assume.
6. ENTRY `baselines/bot.py` — all three opponents. Know exactly what `proportional` does and
   what it ignores (battery).
7. ENTRY `tests/` + `Makefile` (`make graded`, `make check`, `make board`) — the conformance
   suite and rehearsal commands your plans must invoke as acceptance gates.

## MANDATORY COMPLIANCE — DISQUALIFICATION-LEVEL (extract before any design)

From the brief §4.11 + repo README, these are pass/fail. Restate each verbatim in the
blueprint §1 with its source:

- Deliverable: ONE Docker image (pip path only if Docker blocked), configured ENTIRELY by
  env vars (`ARENA_URL`, `TEAM_NAME`). One agent per team; `TEAM_NAME` identical all weekend
  (it decides the device profile).
- Functional bar: builds from clean clone; registers/heartbeats; valid in-budget bid in ≥95%
  of admissible rounds; completes the run.
- κ rule: malformed/negative/over-budget bids raise compromise score κ — 12 → ejection.
  Validate every bid client-side before sending (Σb ≤ W, each ≥ 0).
- Resilience: injected 503/429 + latency + lease expiry + battery outage. Retry with backoff;
  never exit on transient errors; re-register after lease expiry.
- No hardcoding of outcomes (graded seeds unpublished). Reading arena source is encouraged.
- Engineering: env-var config, no hardcoded URLs/secrets, readable code, useful logs, accurate
  README. Repo hygiene: no `.env` committed, no absolute paths, complete `requirements.txt`.
- Submission (TAIKAI, before 2026-10-07 14:59 UTC = 16:59 CEST): public fork link, TEAM_NAME,
  Dockerfile path + build command if non-plain, README with 300-word strategy write-up + all
  members, 3-slide deck, optional screenshot/`results.json`, live-pitch availability.
  Ties break by strategy score THEN SUBMISSION TIME — plan to submit early, not to polish late.
- Grading math: strategy = 25·clip((S−T)/(R−T),0,1) — NOT zero-sum; partial credit is real.
  Bad seeds (R≤T) dropped. Fault-lost rounds cost resilience, not strategy.

## STRATEGY DIRECTION (given — do not re-derive from scratch, improve on it)

The organizers measured: spend full budget every round; weight-proportional split is within ~2%
of the Kelly best response; battery mismanagement costs ~⅓ of the run; floor misses halve/quarter
rounds; S_k recoverable exactly as λ_k·C_k − own-bid (smooth over 3–4 rounds); `proportional`
reacts to battery at 25% — too late. Your plans must pursue, at minimum: (a) floor-guarantee
solver (min-bid inversion b = S·t/(C−t) for q_min/s_min, funded from cheapest source);
(b) battery governor (taper-from-full or sustainable-duty-cycle cap on energy bids, freed budget
to compute/security); (c) price-reactive tilt toward big/cheap pools; (d) diagnostics logging
(idle rounds, floor misses, mean battery, utility/round) feeding the measurement loop.
Beyond that: beat `proportional` by being early on battery, never by out-guessing Kelly by 2%.

## DELIVERABLES (exact paths, ENTRY-local)

Write ALL of the following in this run. No intermediate prompt files.

1. `design_documents/master_blueprint_entry.md`:
   - §1 Requirements: every compliance bullet above traced to its source + the rubric points
     it protects; functional + non-functional (determinism, log discipline, repo hygiene).
   - §2 Architecture: strategy.py policy pipeline (perceive → floor-guarantee → battery-govern
     → tilt → validate → bid), client hardening deltas vs template, local measurement harness,
     pitch analytics (crossings plot, welfare analysis). Inter-module contract table: every
     cross-file function/type owned by exactly one file with path, name, input/output shapes.
   - §3 Plan map: which plan owns what, build order, acceptance gate per plan.
2. `design_documents/DP-STRATEGY.md` — bidding policy: floor solver, battery governor with
   tunable parameters, tilt rule, rescale/validate; unit tests against closed-form Kelly math;
   acceptance = beats `naive-max` + `even-split` on fixed seeds, closes on `proportional`.
3. `design_documents/DP-CLIENT.md` — agent-loop hardening: pre-send validator (κ guard),
   retry/backoff audit vs template `client.py`, lease-expiry re-register path, heartbeat
   robustness, structured per-round diagnostics log; acceptance = `make check` green + survives
   `make graded` fault injection without exit.
4. `design_documents/DP-HARNESS.md` — measurement protocol: fixed-seed A/B procedure (change
   strategy, never seed), metrics table per run (score, idle, floors, mean battery, u/round),
   bot-comparison + crossings analysis, swarm-welfare (Σ log u with/without agent) measurement;
   owns `design_documents/measurement-log.md` entries per experiment.
5. `design_documents/DP-PITCH.md` — README (build/run + 300-word strategy + honest negatives),
   3-slide deck outline, `results.json` capture from best `make graded` run, live-pitch rehearsal
   script, submission checklist execution incl. early-submit + `make check-docker`.
6. `design_documents/DP-VIZ.md` (explicitly pitch-only, NOT graded): arena companion view for
   the pitch video (pools/bids/battery over rounds, crossings vs baselines). Small, static or
   replay-based — never load-bearing for score.

Plan format (every DP): goal → files touched (default: `agent-template/strategy.py` ONLY;
justify any other touch line-by-line; NEVER touch `arena/`, `baselines/`, `tests/`) →
numbered work units each with acceptance (command + expected output) → no-go constraints
(no hardcode, no secrets, no .env, deterministic seeded tests).

## FINISH

Report: files written, blueprint §1 trace table, total work units, first three build steps in
order, and anything you could not resolve from the read-first material (quote the gap, do not
fill it by invention).
