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

## Run 3 — `decay-switch` (2026-07-28)

**Feature:** Per-match mutual-help decay ON/OFF switch (a rules toggle). UI-completeness /
silent-failure-prone — the mutual-help value must read the same in the engine, the
AI-facing rules text + pact note, the bot fatigue logic, and every legend/replay surface
(game viewer, front-page showcase, lobby); a wrong render passes resolver tests yet ships a
legend that disagrees with the score.

**Factory branch/PR:** `exp-factory/decay-switch` (`696f5743`) — not shipped, branch deleted  |
**Thin branch/PR:** `exp-thin/decay-switch` (`e8f133f5`) — **WINNER, PR opened**

| | Factory (engine) | Thin (engine-free) |
|--|------------------|--------------------|
| Blind judge: more correct? | No | **Yes — judge picked Thin** |
| Preflight/tests pass | Yes (ruff/mypy/1472) | Yes (ruff/mypy/1474) |
| Acceptance criteria met | 6/6 claimed — judge: **AC4 violated** on front-page + lobby legends | **6/6** |
| Real findings | ~36 / 5 rounds | ~34 / 3 boundaries |
| False positives | 2 | few |
| Unique catch (other missed) | `create_match` "shipped inert" wiring — but its OWN build's bug | **front-page showcase + lobby legend AC4 surfaces the engine deferred/missed**; stale sample-data w/ impossible +12/+4 |
| Real-work tokens | 7,506,467 | 5,568,563 (**~26% less**) |
| Wall-clock | ~32 min (incl. resume) | ~26 min (incl. resume) |
| Friction events (breakages) | 7 (all engine) | 5 (manual chores) |

**Verdict:** Thin won a **UI-completeness / multi-render-path feature — the exact type Run 2
said routes to the engine.** Both engine-cores were correct; they split on AC4's "no legend
may show a decay the engine won't pay." The engine ran MORE review rounds across MORE lenses
and still MISSED the front-page + lobby legend surfaces (deferring them) while shipping ZERO
rendered-legend tests; Thin wired AND rendered-tested every surface. So on this run the engine
did NOT catch the cross-render-path gap — Thin did — **directly contradicting Run 2's routing
hypothesis.** Burden of proof not met; ~26% cheaper, less friction, zero maintenance for Thin.

**Caveats:** both arms hit the subscription 5-hour session limit mid-run (two parallel builds
on one Claude sub) and were resumed with context intact — token counts include that retry on
both sides, and nested review-subagent tokens are uncounted (Factory ran more rounds, so its
true cost gap is wider). No hand-finishing of either build.

**Lesson:** Run 2's "route UI-completeness features to the engine" rule **did not replicate.**
Here a UI-completeness feature went to Thin, and Thin — not the engine — caught the
cross-render-path gap. The deciding factor looks less like feature-type and more like which
arm's diff-review happened to render the actual surface. Don't route by feature-type yet.

---

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
| 3 | `decay-switch` | UI-completeness; silent-failure-prone | YES (better, per judge) | NO — **Thin** caught the render-path gap the engine deferred | Factory 7 vs Thin 5; Factory ~26% more tokens |

**Sample size:** 3 runs. **Score: Thin 2, Factory 1.**

**Switch recommendation:** LEAN SWITCH — not yet definitive. Run 2's emerging routing rule
(UI-completeness / multi-render-path → engine) **did NOT replicate**: Run 3 was a
UI-completeness feature the engine LOST, missing the very cross-render-path legend gap that
rule predicted it would catch (Thin caught + rendered-tested it; the engine deferred it with
zero rendered-legend tests). So the engine's lone win (Run 2) now looks more like variance
than a reliable feature-type pattern. Thin has won 2 of 3, cheaper + lower-friction every
time. Do 1–2 more runs to confirm, but the case for keeping the ~40-module engine is
weakening — it is not splitting cleanly by feature type.
