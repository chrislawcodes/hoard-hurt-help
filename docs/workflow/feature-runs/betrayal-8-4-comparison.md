# Thin vs Factory — 8/4 Betrayal Payoff

Same feature built down both paths. Feature is **UI-completeness / silent-failure-prone**:
the scoring change is small, but the visible effect must be threaded through the
resolver, the running-score mirror, two robot-circle animation score loops, and the
feed — a wrong number can pass the resolver tests and still ship a viewer that
disagrees with the authoritative score.

## Outputs
- **Factory:** `exp-factory/betrayal-8-4` (`7fba769e`, polished to `93d27e2e`) — **WINNER, shipped**
- **Thin:** `exp-thin/betrayal-8-4` (`7b586ef`) — not shipped

## 1. Correctness (blind judge)
- **Verdict: Factory more correct.** Both got the resolver math, rules text, and docs right and both used a dedicated `betrayal_bonus` field (so a betrayal is never mislabeled a "gift"). They split on the heavily-weighted **viewer** criterion: Factory credits the betrayer's +4 in **both** replay-animation score loops (`computeScores`/`sim` and `playAction`/`rScore`) and threads `betrayed_helper` into the robot-circle JSON; Thin **never touched the animation**, so the animated standings under-count every betrayer by 4 — a live discrepancy between the animation and the authoritative score, on the exact feature being built.
- **Preflight/tests:** Factory PASS (1439, +2 after polish = 1441) | Thin PASS (1439)
- **Acceptance criteria met:** Factory **7/7** | Thin **6/7** (missed the animation)

## 2. Review value
| | Factory (engine) | Thin (engine-free) |
|--|------------------|--------------------|
| Real findings | spec: dedicated `betrayal_bonus` key + 3 missed UI touchpoints; **plan: the two-JS-loops under-count (both reviewers, independently) + a non-preflight-green slice boundary**; diff: 3 stale `-8` comments | spec: same `betrayal_bonus`/gift catch + missed test files; **plan: a vacuous floor test** (would pass even if the bonus were never wired); diff: clean |
| False positives | impl-lens withdrew a double-count "blocker" after tracing | — |
| Unique catch the other missed | **the animation under-count → caught AND fixed** (Thin flagged it but deferred as "game-art") | fixed a stale `-8` comment in `runtime.py` that Factory left; slightly stronger attacker-own-delta floor test |
| Stages where review changed the artifact | spec, plan | spec, plan |

## 3. Cost (real-work tokens = billed_input + output, per orchestrator JSONL)
| | Factory | Thin |
|--|---------|------|
| Real-work tokens | **3,214,158** | **1,868,379** |
| Output tokens | 149,273 | 100,273 |
| Ratio | **~1.7×** | 1.0× |
| Human nudges (resume prompts) | 4 | 3 |

## 4. Friction (breakages / workarounds)
| Factory (9 events) | Thin (6 events) |
|--------------------|-----------------|
| Review subagents ran out of turns before emitting their `## Findings` block (recurring — the dominant babysitting cost); post-reconcile stale-artifact "repairable" state at spec/plan/diff; 2 brief-named touchpoints don't exist | Spec Kit can't drive its `/speckit.*` slash commands non-interactively → hand-authored spec/plan/tasks; Spec Kit's dropped-in skill files broke a repo test (had to remove); same 2 nonexistent touchpoints |

## Verdict
On **this** feature the Factory earned its keep. Its deeper plan review independently
caught the two-animation-loops under-count and **fixed** it, while the Thin arm saw the
same gap and **deferred** it — shipping a viewer that disagrees with the score on the
feature being built. The user explicitly weighted the viewer, so that gap is decisive.

But the win cost ~1.7× the real-work tokens and carried more engine friction (the
review-block assembly failed repeatedly and needed hand-completion). This is the first
run where the engine produced a **materially more correct** output — and it did so on a
**UI-completeness** feature, exactly the "a bug passes the tests but ships wrong" shape.

**n=1 for this feature type.** Recommendation: KEEP the engine for UI-completeness /
silent-failure-prone features; default to Thin for settled backend changes where the
prior run showed Thin lands within noise for far less cost.
