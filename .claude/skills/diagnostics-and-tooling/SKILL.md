---
name: diagnostics-and-tooling
description: How to MEASURE this project instead of eyeballing it — the catalog of scripts/ diagnostic and analysis tools with interpretation guides. Load this when spinning up a test match, testing the agent-play path end-to-end, running bot tournaments, building or reading the baseline dataset, training or using the win-probability models, validating a game-rule change with numbers (A/B oracle, counterfactual replay, tie-rates), or judging whether a measured difference is real. Do NOT use it for triaging a live prod problem (debugging-playbook), for settled past results (failure-archaeology), or for running the app itself (docs/setup-dev.md).
---

# Diagnostics and Tooling

The house rule: **success must be measurable, never judged by eye.** A rule
change needs numbers before it ships (the 7→5 rounds and mutual-help-decay
changes both did); a "bug fix" needs a reproduction that goes green. This
skill catalogs the tools and — more important — how to read their output.

Sibling skills: live prod triage → `debugging-playbook`. What past
measurements already decided → `failure-archaeology`.

Every script below documents itself — read its docstring
(`sed -n '1,30p' scripts/<name>.py`) for flags before running. Run all of
them from the repo root.

## Spinning up test play — three tools, three layers

Pick by which layer you're testing. Using the wrong one tests nothing:

| Tool | What it exercises | Needs |
|------|-------------------|-------|
| `scripts/new_test_game.py` | Server-side bots playing a real match (engine, turn loop, viewer). The server plays itself. | Server running |
| `scripts/random_agent.py` | The public **agent API** path end-to-end — a fake AI submitting real turns with random legal moves | Server + an agent key (`sk_conn_...`) |
| `scripts/agentludum_connector.py` | Real model-backed play: drives the Claude/Codex/Gemini CLI per turn, chained session per (agent, match) | Server + agent key + a signed-in provider CLI |

A bot is a house opponent the server plays; an agent is a user's AI over the
API. `random_agent.py` stands in for an agent, never for a bot.

## The baseline pipeline (offline, no server)

Four stages, each feeding the next; artifacts land in `data/`:

```bash
python scripts/baseline_tournament.py --batches 4 --seed 99   # → data/baseline.sqlite
python scripts/export_baseline_dataset.py                     # → data/baseline.csv (one row per player-turn)
python scripts/compute_features.py                            # → data/baseline_features.csv (+17 derived columns)
python scripts/train_win_prob.py                              # → data/win_prob_model.pkl
python scripts/train_round_win_prob.py                        # → data/round_win_prob_model.pkl
```

Interpretation guide:

- **Batches exist to show stabilization.** Matches run in batches of 25 (10
  bots per table, sampled with replacement from the 9-strategy pool in the
  script's `STRATEGIES` tuple). Compare summary stats **across
  batches** — if batch 4 still moves the numbers, you don't have enough data
  to conclude anything. Add batches, don't squint harder.
- **Always pass `--seed` when comparing runs**, or the comparison confounds
  strategy sampling with the thing you changed.
- **The win-prob models predict from pure game state** (no strategy
  identity) at the start of a turn. They were deliberately removed from the
  UI (#566) — they are offline analysis tools now. Don't re-add a display
  without a new decision.
- The tournament DB is a separate local SQLite file — it never touches the
  app database.

## Validating a game-rule change — the two proven methods

**1. The A/B oracle** (`scripts/decay_validation_sim.py` is the template):
scripted deterministic bots on the **real engine** (real `resolve_turn`, no
LLM, no network), one condition per rule variant, a single headline metric,
multiple seeds. The mutual-help-decay run: per-round tie-rate 0.53 (flat) →
0.27 (decay) → 0.19 (decay + rotation), ordering consistent on **every one**
of 5 seeds × 40 matches. That per-seed consistency — not the means alone — is
what made it a verdict. State the expected numbers *before* running; if you
can't, you don't have a hypothesis yet.

**2. Counterfactual replay of real games** (the 7→5 rounds method, #567):
take completed real matches and re-score them under the proposed rule. The
question isn't "is it different" but "does it preserve what matters" — there
it was "still decided at the final round" in 8/9 games with no new ties. Small
N is acceptable when the criterion is near-unanimous; 5/9 would have decided
nothing.

Both methods route the change through normal delivery (preflight, PR,
`Validation` section) — a good measurement justifies a change, it never
bypasses the gate.

## Reading a measured difference — is it real?

- Deterministic sims: vary `--seed`. An effect that flips sign across seeds
  is noise. Report per-seed results, not just the pooled mean.
- Tournament stats: check stabilization across batches first (above).
- Win rates between strategies: 25 matches is one batch, not a conclusion.
  The baseline used 100+ matches before its stats settled.
- Beware tests-as-evidence: a suite can assert the bug (the `min_legal_raise`
  lesson, #380 — see `failure-archaeology`). For rule logic, add an
  independent oracle-style check, not just more examples.

## Prod-side measurement

- Per-connection counters: `connections.turns_played`, `api_call_count`
  (shown on the connection detail page) — is an AI actually playing?
- `request_incidents` — error/crash ledger (see `debugging-playbook`).
- Admin reporting (`/admin/reports`): turn-time distributions and
  slowest-match analysis, filterable by completion-date window.

## Provenance and maintenance

Written 2026-07-02 from the scripts' own docstrings, `STATUS.md` (#320 baseline
tournament, #566), commit `77c679f` (#567), and
`docs/workflow/feature-runs/mutual-help-decay/closeout.md`.

Re-verify when suspicious:
- The catalog is complete: `ls scripts/` (new tools belong in this file)
- Any flag or default: `sed -n '1,30p' scripts/<name>.py`
- The decay headline numbers: rerun `scripts/decay_validation_sim.py --n 40 --seeds 42 99 7 13 23`
- The 9-strategy pool / batch size: `sed -n '1,20p' scripts/baseline_tournament.py`
