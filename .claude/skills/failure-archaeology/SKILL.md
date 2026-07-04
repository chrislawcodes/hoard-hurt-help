---
name: failure-archaeology
description: The chronicle of this repo's settled battles — bugs already root-caused, designs already validated or rejected, refactors already adjudicated — each as symptom → root cause → evidence → status. Load this BEFORE re-investigating anything that feels familiar, proposing a rule/design change that may have been tried, re-attempting a dedup or refactor, or asking "didn't we already fix/try/decide this?". Also load it when git history or an old PR needs interpreting. Do NOT use it for a live production problem (use debugging-playbook), for reading current invariants (architecture docs), or for Direct-vs-Factory workflow results (experiments.md).
---

# Failure Archaeology

Settled battles. Each entry is a fight this project already had, with the
verdict and the evidence. Check here before re-fighting one — re-deriving a
settled answer wastes a session; re-attempting a rejected change reintroduces
a known bug.

**One home per fact.** This file is an *index with verdicts*. Detail lives in
the linked home (PR, doc, or commit). Do not copy the detail here.

Sibling skills: live prod issue → `debugging-playbook`. Measuring a claim →
`diagnostics-and-tooling`. Current invariants → `docs/platform/AGENT_LUDUM_ARCHITECTURE.md`
("Notable shapes & tensions"). Workflow A/B verdicts → `experiments.md`.

## Production incidents (full detail: `docs/operations/debugging-history.md`)

That file is the source of truth for prod incidents. Index only:

| Battle | Verdict | Evidence |
|--------|---------|----------|
| M_0279 frozen: bot HELP/HURT crashed the turn loop | Bots reason in seat names; DB stores integer `agent_id`. Translate at **every** record boundary, not just HTTP. | #289 fix, #290 added `request_incidents` persistence for loop crashes (`method='TASK'`) |
| G_0012 frozen ~8.5h by mid-deploy restart | Resume must be idempotent at turn AND round boundaries; a fire-and-forget task must never die silently. | #45 (get-or-create `_open_turn` + done-callback), #46 (`rounds_awarded` guard, migration 0008) |
| MCP page "waiting" while the AI was playing | `token.client_id` from an OAuth-proxy provider is the *user subject*, not a client id. Key per-client identity on the DCR `client_id` from the raw bearer JWT. | #454 regression, #456 fix; tension recorded in architecture doc |

## Agent-play battles (settled in code)

| Battle | Verdict | Evidence |
|--------|---------|----------|
| Late talk messages silently dropped at round start | One stable `turn_token` across talk→act; `Turn.phase` distinguishes phases. **Never re-mint the token at the handoff** — that was the bug. Late talk now gets HTTP 202 `talk_window_closed`, not an error. | #540 (`2d30f8c`); invariant in architecture "tensions" |
| Agents sleeping ~49s between turns, looking stalled | Never hand an agent a deadline after it submits — a CLI agent reads it as "wait until then" and inserts its own shell sleep. Reply `next_poll_after_seconds=0`: "poll again now". | #541 (`dc95f64`) |
| Seat submits only fallback HOARD every turn (two independent causes) | (a) Stale legacy `AgentVersion.model` forwarded verbatim ran the wrong CLI (`claude --model gpt-5.4-mini` → 404 → HOARD). Guard: forward a model only if it doesn't provably belong to another provider; migration 0043 cleared stale models. (b) `codex exec resume` rejects `--sandbox` after the subcommand — flag order matters. | #569 (`5bffc0c`) |
| HELP/HURT targets rejected → missed turns | Target rules and the valid-target list lived only in the FIRST turn's prompt; chained sessions dropped `target_id` on later turns, and the loop re-submitted the doomed move until deadline. Verdict: restate targets **every** turn, validate before submit, re-ask once, then HOARD (a valid move that closes the turn). Server resolves targets case/whitespace-insensitively. | #586 (`3842dcb`) |
| MCP clients silently stopping mid-game | Re-sending the full transcript every poll overflows the client's tool-output buffer and trips its loop detection. Poll history is a rolling window (`RECENT_HISTORY_TURNS`); full state is pulled once on session start. | architecture "tensions" (`lean-poll-history`) |

## Game-design battles (settled by measurement)

| Battle | Verdict | Evidence |
|--------|---------|----------|
| Match length: 7 rounds → 5 | Counterfactual replay of 9 real games: 5 rounds keeps the match decided at the final round in 8/9, no new ties, ~29% shorter (49→35 turns). Turns/round stays 7 — the within-round lead settles around turn 6. | #567 (`77c679f`) |
| Flat mutual help caused tie stalemates | Decaying mutual help (`max(2, 8-k)` per pair) + decay-aware partner rotation. Tie-rate 0.53 → 0.27 → 0.19 (baseline → decay → aware), consistent across 5 seeds × 40 matches. | #553, #556; oracle: `scripts/decay_validation_sim.py`; run recorded in `docs/workflow/feature-runs/mutual-help-decay/closeout.md` |
| Win-probability display | Removed from the UI (#566). The trained models remain in `data/` for offline analysis only. Don't re-add the display without a new decision. | #566 |

## Refactors adjudicated "do not re-attempt"

| Candidate | Why rejected | Evidence |
|-----------|--------------|----------|
| C2: unify the two turn-row openers | One is get-or-create (resume-safe), the other a blind INSERT — behaviorally different on purpose (the G_0012 lesson). | dedup run `docs/workflow/feature-runs/dedup-engine-cseries/`, PR #559 |
| D5: extract a `pick_by_trust` helper from 6 seeded trust-tiebreak selectors | Per-site seed args / access patterns / signs differ; a shared helper adds determinism risk with no real dedup. Sites pinned by a determinism regression test. | dedup run `docs/workflow/feature-runs/dedup-bots/`, PR #563 |
| Turn-loop twins: merge `_all_submitted`/`_all_messaged` + the two wait loops (`scheduler_turn_loop.py`) | Real duplication, adjudicated leave-as-is: this is the code that freezes live games when it breaks (G_0012, M_0279) and the payoff is ~40 lines. Boring, separate functions are the safety feature. Owner call, 2026-07-04. | Tier 4 close-out of the refactoring survey (see STATUS.md entry + its PR) |
| Split `scripts/agentludum_connector.py` into a package | Deferred, not refused: operators install by copying the one file, so a split first requires a distribution change (e.g. zipapp) and a one-time re-install for everyone. Section banners added instead. Revisit only if the install story changes. | Tier 4 close-out of the refactoring survey (see STATUS.md entry + its PR) |

## Testing battles

| Battle | Verdict | Evidence |
|--------|---------|----------|
| "More tests" read as "more correct" | The Direct Liar's Dice engine had a real `min_legal_raise` bug — and 6 of its own 692 test lines asserted the buggy values, so the bigger suite *hid* it. Test count ≠ correctness; add an independent minimality/oracle test for rule logic. | #371 vs #380 (fix); `experiments.md` #10 |
| Mocked tokens proved the wrong thing | Spec 016's tests passed with a fake distinct `client_id`; real tokens carry the shared Google subject. When a key must be per-X, assert against a *real* token/data shape, not a fixture. | #454/#456; lesson recorded in `debugging-history.md` |
| Full suite too slow to iterate on | Long-poll tests patched to skip the 40s production hold (#568); suite runs under pytest-xdist (#575). Don't reintroduce real-time waits into tests. | #568, #575 |

## Adding an entry

Add a battle when a non-trivial investigation settles: one table row here
(symptom-shaped name, one-sentence verdict, PR/commit evidence, link to the
home holding the detail). Prod incidents get their full write-up in
`docs/operations/debugging-history.md` (per `CLAUDE.md`) and only an index row
here. If it doesn't fit an existing section, add a section.

## Provenance and maintenance

Written 2026-07-02 from `docs/operations/debugging-history.md`, `STATUS.md`,
`experiments.md`, and `git log` (history depth: 610 commits on `origin/main`).

Re-verify when suspicious:
- A PR's real story: `git log origin/main --oneline | grep '#<PR>'` then `git show -s --format='%b' <sha>`
- The decay numbers: rerun `scripts/decay_validation_sim.py` (see its docstring)
- Round count still 5 / decay rule still shipped: check `app/games/hoard_hurt_help/` before citing either as current
