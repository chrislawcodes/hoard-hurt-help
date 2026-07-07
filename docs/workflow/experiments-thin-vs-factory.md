# Experiments — Thin path vs Feature Factory engine

Accumulating A/B data to decide **build-vs-switch**: does the custom Claude-only
Feature Factory engine (`run_factory.py`, ~40 modules) catch enough MORE than the
engine-free **Thin path** (Claude Code + GitHub Spec Kit stages + plain
adversarial-subagent review) to justify maintaining it?

Run via the `experiment-thin-vs-factory` skill. Each run appends one entry below
(newest first) and updates the Running Tally + Switch recommendation.

**Burden of proof is on the engine.** Thin within noise on correctness + real
findings = a vote to switch (it's cheaper, near-zero maintenance). Keep the engine
only on a repeatable, material catch advantage — especially on silent-failure-risk
features.

This is a separate axis from `experiments.md` (which is Direct-Path vs Feature
Factory). Keep them distinct.

---

## Entry template

```markdown
## Run N — `<slug>` (<date YYYY-MM-DD>)

**Feature:** <one sentence; note if silent-failure-prone>

**Factory branch/PR:** <...>  |  **Thin branch/PR:** <...>

| | Factory (engine) | Thin (engine-free) |
|--|------------------|--------------------|
| Blind judge: more correct? | | |
| Preflight/tests pass | | |
| Acceptance criteria met | N/N | N/N |
| Real findings | | |
| False positives | | |
| Unique catch (other missed) | <or —> | <or —> |
| Real-work tokens | | |
| Wall-clock | | |
| Friction events (breakages) | | |

**Verdict:** <did the engine out-catch Thin enough to justify maintenance? apply burden of proof>

**Lesson:** <one concrete routing rule>

---
```

<!-- New entries go directly below this line, newest first. -->

## Run 2 — `betrayal-8-4` (2026-07-07)

**Feature:** The "8/4" betrayal-payoff re-split (attacker +8 / victim −4 instead of
+4 / −8); UI-completeness / silent-failure-prone — the visible number must thread
through the resolver, the inround mirror, **two robot-circle animation score loops**,
and the feed, so a wrong render passes the resolver tests yet ships a viewer that
disagrees with the score.

**Factory branch/PR:** `exp-factory/betrayal-8-4` (`7fba769e` → polished `93d27e2e`) —
**WINNER, shipped**  |  **Thin branch/PR:** `exp-thin/betrayal-8-4` (`7b586ef`) — not shipped

| | Factory (engine) | Thin (engine-free) |
|--|------------------|--------------------|
| Blind judge: more correct? | **Yes — judge picked Factory** | No |
| Preflight/tests pass | Yes (1439 → 1441 after polish) | Yes (1439) |
| Acceptance criteria met | **7/7** | 6/7 (missed the animation) |
| Real findings | spec: dedicated `betrayal_bonus` key + 3 missed UI touchpoints; plan: **the two-JS-loops under-count** (both reviewers) + a non-preflight-green slice; diff: 3 stale `-8` comments | spec: same `betrayal_bonus`/gift catch + missed test files; plan: a **vacuous floor test**; diff: clean |
| False positives | 1 (impl-lens double-count, withdrawn on trace) | — |
| Unique catch (other missed) | **the animation under-count → caught AND fixed** (Thin flagged it, deferred as "game-art") | a stale `-8` comment in `runtime.py` Factory left; slightly stronger attacker-floor test |
| Real-work tokens | 3,214,158 | 1,868,379 (**~1.7× cheaper**) |
| Wall-clock | longer (2 spec rounds, 12 commits) | shorter |
| Friction events (breakages) | 9 (review-block assembly ran out of turns repeatedly; stale-artifact "repairable" state; 2 nonexistent touchpoints) | 6 (Spec Kit can't drive slash commands non-interactively; its dropped-in skill files broke a repo test; same 2 touchpoints) |

**Verdict:** First run where the engine produced a **materially more correct** output.
Its deeper plan review independently caught the two-animation-loop under-count and
**fixed** it; the Thin arm saw the same gap and **deferred** it, shipping a viewer that
disagrees with the authoritative score on the exact feature being built. The user
weighted the viewer heavily, so that gap decides it. The engine met the burden of
proof here — at ~1.7× the tokens and more friction.

**Caveats:** n=1 for this feature type. Both arms got the same fully-settled design +
touchpoint list up front, so spec ceremony added little; the difference was plan/diff
review depth on the viewer render paths.

**Lesson:** For **UI-completeness** features (one visible value threaded through several
render paths), the engine's deeper plan review catches cross-path gaps the thin path
defers. Route UI-completeness / silent-failure features to the engine; keep settled
backend changes on Thin.

---

## Run 1 — `agent-model-selection` (2026-06-29)

**Feature:** The verification-store slice (2a+2b: store + engine + connector
channels) of agent model selection; silent-failure-prone (sanitization of
stored error text).

**Factory branch/PR:** PR #574 (merged as squash 5826ee40, after fixes)  |
**Thin branch/PR:** independent builder subagent off clean main, blind to the
Factory code; no separate PR — the blind judge's findings were folded into
#574 before ship.

| | Factory (engine) | Thin (engine-free) |
|--|------------------|--------------------|
| Blind judge: more correct? | No | **Yes — judge picked Thin** |
| Preflight/tests pass | Yes | Yes |
| Acceptance criteria met | — (not scored) | — (not scored) |
| Real findings | Own adversarial lenses missed all 4 real gaps in its build (see Verdict) | Blind-judge comparison surfaced those 4 gaps |
| False positives | not tracked | not tracked |
| Unique catch (other missed) | — (none; burden of proof not met) | 4 real gaps in the Factory build its own reviews missed |
| Real-work tokens | not captured (full spec→plan→design→tasks ceremony) | ~242k (~2 subagents) |
| Wall-clock | not captured (multi-stage, multi-day ceremony) | ~11 min |
| Friction events (breakages) | Per-stage reviews, the dead-Gemini Claude-review dance, stale-checkpoint loops | zero |

**Reuse:** near-tie — both builds reused shared helpers; neither duplicated the
model→provider mapping.

**Verdict:** Switch-leaning at n=1 — the engine produced a slightly worse build
at far higher cost. The blind judge (not the engine's own lenses) caught real
gaps in the Factory build, including a genuine credential leak in stored error
text: `sanitize_error` leaked `sk-…` dash-form API keys and missed absolute
paths outside a home/temp allowlist (e.g. `/opt/homebrew/bin/claude`). Also:
`model_status_for` had no injectable clock, and the worklist didn't exclude
paused agents. No unique engine catch — the burden of proof was not met on
this run.

**Caveats:** n=1. The Thin arm built only the backend core while the Factory
built the whole feature (connector loop + UI). The Thin builder received
requirements the Factory's spec process had already refined, so Factory spec
work transferred to Thin for free.

**Lesson:** When the spec is already refined, an independent blind-judge
comparison caught more than the factory's own adversarial lenses.

---

## Running Tally

| Run | Slug | Feature type | Thin within noise on correctness? | Engine unique catch? | Friction (Factory vs Thin) |
|-----|------|--------------|-----------------------------------|----------------------|----------------------------|
| 1 | `agent-model-selection` | verification store; silent-failure-prone | YES (better, per judge) | NO | Factory high vs Thin zero |
| 2 | `betrayal-8-4` | UI-completeness; silent-failure-prone | NO — Factory better, per judge | **YES — animation under-count Thin deferred** | Factory 9 vs Thin 6; Factory ~1.7× tokens |

**Sample size:** 2 runs.

**Switch recommendation:** NEED MORE RUNS — the two runs **split by feature type**.
Thin won the settled-backend feature (Run 1) for far less cost; the engine won the
UI-completeness feature (Run 2) by catching a cross-render-path gap Thin deferred.
Emerging routing rule: **UI-completeness / multi-render-path features → engine;
settled backend → Thin.** Not yet a clean switch/keep — need more runs of each type.
