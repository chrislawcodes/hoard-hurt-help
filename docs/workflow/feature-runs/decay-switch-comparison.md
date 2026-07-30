# Thin vs Factory — Mutual-Help Decay Switch

## Outputs
- Factory: branch `exp-factory/decay-switch` (commit 696f5743) — no PR
- Thin: branch `exp-thin/decay-switch` (commit e8f133f5) — no PR
- Blind mapping (private): Implementation 1 = Factory, Implementation 2 = Thin

## 1. Correctness (blind judge)
- **Verdict: Implementation 2 (Thin) more correct/complete.** Engine cores equivalent — no hard scoring bug in either (resolve_turn OFF=flat+8 / ON=decays, rules-text swap, pact note, viewer per-move value, bot partner-fatigue gate, model, migration all correct in both).
- They split on **AC4 ("no lying")**: Factory left the front-page showcase and lobby legends hardcoded to "bonus decays each round" while an OFF match scores flat +8 on those surfaces → a real legend-vs-engine contradiction. Factory also shipped **zero rendered-legend tests** (asserted only the payload dict, not the HTML a viewer reads). Thin wired AND tested every legend surface (game page, front-page showcase, lobby-neutral).
- Preflight independently re-run by orchestrator: **Factory PASS** (ruff/mypy/1472 pytest) · **Thin PASS** (ruff/mypy/1474 pytest). NOTE: Factory's green suite never covered the AC4 surface it violated — the "tests pass, bug ships" trap.
- Acceptance criteria: **Thin 6/6.** Factory 5/6 + AC4 violated on secondary watch surfaces.

## 2. Review value
| | Factory | Thin |
|--|---------|------|
| Real findings | ~36 across 5 rounds | ~34 across 3 boundaries |
| False positives | 2 rejected | few |
| Unique catch the other missed | `create_match` "shipped inert" wiring — but its OWN build's bug | front-page showcase + lobby legend AC4 surfaces (Factory deferred/missed); stale sample-data with impossible +12/+4 deltas |
| Stages where review changed the artifact | spec, plan, diff (3/3) | spec, plan, diff (3/3) |

Both reviews earned their keep. But the engine's larger review MISSED the AC4 surface that decided the blind verdict; the thin review caught it.

## 3. Cost
| | Factory | Thin |
|--|---------|------|
| Real-work tokens (top-level driver) | 7,506,467 | 5,568,563 (~26% less) |
| Nested review subagents (uncounted) | more rounds/lenses → true gap wider | fewer |
| Wall-clock (incl. failed run + resume) | ~32 min | ~26 min |
| Interruptions | shared session-limit hit once (both) | same |

## 4. Friction (breakages/workarounds)
| Factory (7 — all engine) | Thin (5 — all "no engine convenience") |
|--------------------------|----------------------------------------|
| stale checkpoint forced full spec re-round; lens-routing over-applied completeness to tasks; 54KB diff exceeded review budget; reconcile race; `[CHECKPOINT]` marker false warning; review token-accounting gap; staleness re-review churn | no automated consumer map (missed 2 render paths until whole-diff review); no per-slice checkpoint; manual reviewer orchestration; stale-fixture find was luck not a gate; manual "re-run affected reviewer" decision |

## 5. Ergonomics / maintenance
- Factory: drove the ~40-module engine + its state/checkpoint/review artifacts; one point in its favor — add-only `*_for_match` API (didn't alter existing signatures) and it updated the architecture doc.
- Thin: no engine touched; modified existing rules-method signatures (backward-compatible kwarg).

## Verdict
Clean win for Thin. Equivalent engine core; Thin is more correct on the AC4 invariant (Factory shipped a real legend-vs-engine contradiction on secondary surfaces with no rendered-legend test), ~26% cheaper on top-level tokens (gap wider once nested reviewers are counted), fewer/lighter friction events, and zero engine maintenance. The Factory engine ran MORE review rounds across MORE lenses and still missed the exact surface the thin path caught and tested — it did not show the material catch advantage the burden-of-proof rule demands. This run: SWITCH-leaning.

## Log entry — append to docs/workflow/experiments-thin-vs-factory.md
| Date | Feature | Blind judge | Thin tokens | Factory tokens | Friction (T/F) | Verdict |
|------|---------|-------------|-------------|----------------|----------------|---------|
| 2026-07-28 | mutual-help decay switch | **Thin** more correct (Factory ships AC4 legend bug, no rendered-legend test) | 5.57M | 7.51M | 5 / 7 | Thin — switch-leaning |

**Running tally — after 3 runs (this is Run 3; see the running log): Thin 2, Factory 1.** Thin won Run 1 (`agent-model-selection`, settled backend) and this run (`decay-switch`, UI-completeness); the Factory won Run 2 (`betrayal-8-4`, UI-completeness) by catching a render-path gap Thin deferred. But this run is the SAME feature type as Run 2 and the engine LOST it — so Run 2's "engine for UI-completeness" routing rule did NOT replicate. Lean SWITCH; 1–2 more runs to confirm before retiring the ~40-module engine.
