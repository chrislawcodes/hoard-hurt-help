# Experiment bookkeeping — Mutual-Help Decay Switch (THIN arm)

Engine-free delivery via the `feature-thin` skill: Claude authored spec/plan/tasks
directly, then ran foreground adversarial review subagents at each boundary. No
`run_factory.py`, no checkpoints, no Spec Kit.

## 1. Stage table

| Stage | Artifact | review_rounds | findings_raised | findings_accepted | artifact_revised |
|-------|----------|:-------------:|:---------------:|:-----------------:|:----------------:|
| Spec  | spec.md  | 1 | 11 (7 fix, 3 affirm, 1 defer) | 8 | yes |
| Plan  | plan.md  | 1 | 16 (16 fix)                   | 16 | yes |
| Build + whole-diff | code | 2 | R1: 15 (2 fix, 2 defer, 11 affirm/safe); R2: 3 (1 fix, 2 confirmed) | 3 code-changing | yes (code) |

Reviewers per boundary: Spec = feasibility-adversarial + requirements-adversarial.
Plan = testability-adversarial + implementation-adversarial. Whole diff (×2 rounds)
= regression + completeness + silent-failure + test-honesty + one blind reviewer.
All foreground, batched parallel.

**Did the reviews change the code? YES at every boundary** (the experiment's ground-truth proxy):
- Spec review → added the Protocol requirement for `*_for_match`, batch-mode migration downgrade, `| default(true)`, corrected consumer enumeration.
- Plan review → added 6 tests (agent_base_prompt, migration backfill, legend, seed_basis guard, bot-wiring, liars-dice), corrected the caller name, the `prior_counts` init guard, and `current_pact_values` OFF early-return.
- Diff review R1 → **reverted** the replay-JS change (real regression) and gated the front-page showcase legend.
- Diff review R2 → **neutralized** the lobby `move_legend` (a gap the compact-mode CSS hid from R1) and removed the now-dead lobby wiring.

## 2. Findings-verdict table (every finding, none dropped)

### Spec stage
| # | Reviewer/lens | Finding | Verdict | Reason |
|---|---------------|---------|---------|--------|
| S1 | feasibility | `*_for_match` must be on the GameModule **Protocol**, not just BaseGameModule, or typed callers fail mypy | fix now | added to Protocol + BaseGameModule; mypy green |
| S2 | feasibility | migration downgrade should use `batch_alter_table` (repo SQLite convention) | fix now | 0047 downgrade wraps in batch |
| S3 | feasibility | THREE hardcoded `8`s in the replay JS, not one | fix now (later reverted) | enumerated all three; whole-diff review then reverted the JS entirely (see D1) |
| S4 | feasibility | `viewer_headline.py` + `web_games_catalog.py` missing from enumeration | fix now | added to spec non-consumer list with reasons |
| S5 | feasibility | FR7 legend needs `\| default(true)` for match-less pages | fix now | `_markup.html` uses `\| default(true)` |
| S6 | requirements | `viewer_headline.py` omitted (dup of S4) | fix now | dispositioned as non-consumer (flat +8, correct under OFF) |
| S7 | requirements | `match_summary`/`turn_block`/`board_signals` reference mutual but are non-consumers | fix now | added to non-consumer list (counts / FR6-corrected delta) |
| S8 | requirements | AC3 bot half (FR5) is a faithful reading, not a dodge | affirm | bots consume no rules text; PARTNER_FATIGUE is the only decay-aware surface |
| S9 | requirements | AC4-under-OFF fully covered | affirm | every decay computer + text is gated |
| S10 | requirements | win-prob reason imprecise (it reads a boolean, not the value) | fix now | corrected the non-consumer reason |
| S11 | requirements | switch only settable via `create_match`, no web UI | defer | UI exposure explicitly out of scope (spec Non-goals); settable for the A/B via the helper |

### Plan stage
| # | Reviewer/lens | Finding | Verdict | Reason |
|---|---------------|---------|---------|--------|
| P1 | testability | `agent_base_prompt`/`rules_text_for_match` OFF surfaces untested | fix now | added `test_rules_text_for_match_off_drops_decay`, `test_agent_base_prompt_off_drops_decay` |
| P2 | testability | migration test must seed a PRE-existing row to prove backfill | fix now | `test_0047_...` inserts M_PRE at 0046, asserts ==1 after 0047 |
| P3 | testability | watch-page legend has no test | fix now | added OFF/ON route legend tests + a default-true render test |
| P4 | testability | seed_basis invariant unguarded; plan's rationale was false | fix now | added `test_bot_context_seed_basis_ignores_decay`; corrected plan wording |
| P5 | testability | in-memory Match default trap → viewer must use `is False`, add ON viewer test | fix now | viewer reads `is not False`; viewer test covers OFF=8 and ON=7 |
| P6 | testability | OFF tests must farm past turn 1 (k≥1) or they're vacuous | fix now | non-vacuity rule; every OFF test asserts a farmed turn |
| P7 | testability | JS untestable; pin the rc_data `delta` it reads | fix now | `test_viewer_off_pact_value_flat_8` asserts rc_data delta (8 vs 7) |
| P8 | testability | create_match test should re-query, not read in-memory | fix now | expire_all + re-select |
| P9 | testability | bot fatigue test must assert BOTH arms | fix now | asserts ON→0 and OFF≥20 |
| P10 | testability | bot service→runtime wiring untested at both call sites | fix now | `test_bot_runtime_threads_decay_flag` spies both talk+act |
| P11 | testability | FR9 cross-game not behavior-tested | fix now | `test_liars_dice_rules_for_match_unchanged` |
| P12 | implementation | caller is `build_turn_static_dict`, not `_static_turn_payload` | fix now | corrected plan + edited the right function |
| P13 | implementation | `resolve_turn` OFF must init `prior_counts={}` to avoid F821 | fix now | initialized before the `if decay_on:` query |
| P14 | implementation | in-memory Match unflushed None counts crash `make_game_rules_text` | fix now | rules tests set total_rounds/turns_per_round explicitly |
| P15 | implementation | viewer `pact_counts` increment wasteful under OFF | fix now | skipped under OFF |
| P16 | implementation | `current_pact_values` OFF still scans DB | fix now | early-return flat map, no scan |

### Whole-diff stage — Round 1
| # | Reviewer/lens | Finding | Verdict | Reason |
|---|---------------|---------|---------|--------|
| D1 | regression + silent-failure | replay-JS `a.delta` change surfaces the stale bundled sample `_rc-g0016-payload.json` (impossible asymmetric mutual deltas 12/8/4) → demo shows +12/+4 | **fix now (revert)** | not needed for the feature — hardcoded +8 is exactly correct under OFF; the ON-rail over-count it "fixed" is pre-existing and the stale sample blocks a clean keep. Reverted all 3 sites. |
| D2–D8 | regression | resolve_turn scalar_one, Protocol stubs, ON rules byte-identity, BotContext field, migration, legend default, callers | affirm/safe | each proven safe (default ON unchanged; mypy green; 1471 tests pass) |
| D9 | regression | no production route creates an OFF match | defer | UI out of scope (S11); OFF is settable via create_match for the A/B |
| D10 | completeness | showcase legend on front page + lobby not gated (`move_legend` + `_markup`) | fix now | front page + watch page gated via `showcase_mutual_help_decay` route helper (R2 finished the lobby) |
| D11 | test-honesty | all 16 tests mutation-proven sound | affirm | each OFF test fails on a forced-ON build |
| D12 | test-honesty | `test_legend_on_match_shows_decay` can't detect broken wiring alone | accept | paired with `test_legend_off_match_shows_flat`, which does detect it |
| D13 | blind | all 6 ACs MET | affirm | verified against ACs + code |
| D14 | blind | `viewer_headline.py` "+8 each" hardcode overstates a re-formed pact under ON | defer | PRE-EXISTING (not in diff); correct under OFF (the feature's concern); an ON-path fix unrelated to the switch |
| D15 | blind | `\| default(true)` treats None as OFF vs code's None→ON | safe / no-op | column is NOT NULL, so a DB-backed page never sees None; not reachable |

### Whole-diff stage — Round 2 (after D1 revert + D10 front-page fix)
| # | Reviewer/lens | Finding | Verdict | Reason |
|---|---------------|---------|---------|--------|
| R1 | completeness | JS revert confirmed closed; OFF invariant holds (engine pays flat 8 = the hardcoded 8) | affirm | no surface shows a decayed number under OFF |
| R2 | completeness | showcase legend gating propagates correctly on front page + watch page | affirm | helper + Jinja context inheritance verified |
| R3 | completeness | **lobby gap**: lobby replay is compact → CSS hides the gated `_markup` legend; the only VISIBLE lobby legend is the ungated `move_legend.html` ("bonus decays each round"), and my lobby route wiring fed a hidden element | **fix now** | neutralized `move_legend` mutual clause to "mutual +8 each" (correct for both settings AND the multi-game live branch); removed the dead lobby route wiring; added `test_move_legend_is_setting_neutral` |

Net: 20 accepted/actioned fixes; 3 deferred with reasons (S11/D9 = UI out of scope; D14 = pre-existing ON headline); 1 documented no-op (D15). **3 findings changed the shipped code after the build was "done"** (D1 revert, D10 front-page gating, R3 lobby neutralization) — the whole-diff review demonstrably earned its keep.

## 3. Friction log — capabilities the factory engine would have provided

1. **No automated consumer map.** I hand-grepped every mutual-help consumer across
   scoring, rules, agent payload, bots, viewer JSON, headline, summary, board
   signals, and 4 templates. My manual enumeration MISSED two render paths — the
   front-page/lobby showcase legend (D10) and the compact-mode-hidden lobby legend
   (R3) — that the completeness reviewers caught only at the whole-diff stage. A
   factory consumer-tracing checkpoint would likely have surfaced these before the
   build.
2. **No per-slice checkpoint review.** I built all 13+ consumers in one pass (the
   brief's same-commit rule forced it), so the JS-sample regression (D1) and the
   showcase-legend gaps weren't caught until the end-of-build fan. A checkpointed
   engine reviews each slice's diff as it lands.
3. **Stale data artifact discovery was luck.** The bundled `_rc-g0016-payload.json`
   carries impossible mutual deltas from an older viewer format; I only learned this
   because the regression reviewer dug into it. The factory's data-critical-waves
   discipline flags exactly this kind of production/fixture data mismatch.
4. **Manual reviewer orchestration.** I hand-wrote each reviewer's lens prompt,
   hand-generated the diff (twice), and hand-consolidated ~34 findings into this
   table. The engine dispatches reviewers, aggregates findings, and loops rounds
   automatically — this was the bulk of the Thin path's overhead.
5. **Manual "re-run affected reviewer" decision.** Deciding that the D1 revert + D10
   fix warranted a round-2 completeness review (which found R3) was a judgment call
   I made by hand; the engine re-runs affected checkpoints on every change.

Where the Thin path held up: the foreground reviewers were fast and high-signal,
every finding got a recorded verdict here (no silent drops — the Run-2-loss failure
mode), and the total ceremony was lighter than a full factory run for a feature
whose design settled after one spec round.
