# Measurement log — one entry per experiment, newest on top

Discipline (from `docs/strategy-primer.md` §7): change ONE thing at a time, keep the seed
fixed, let the numbers decide. Every entry: date, strategy diff (1 line), seed/scenario,
score, rounds idle, floor misses, mean battery, utility/round, verdict (keep/revert).

## Template

| Date | Change | Seed | Score | Idle | Floors | Mean batt | u/round | vs bots (n/e/p) | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | |

- `vs bots (n/e/p)`: beat naive-max / even-split / proportional? (y/n each)
- Attach: `results.json` or board screenshot path per row.
