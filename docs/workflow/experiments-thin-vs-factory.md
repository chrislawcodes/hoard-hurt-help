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

**Sample size:** 1 run.

**Switch recommendation:** NEED MORE RUNS — switch-leaning at n=1 (1 of 1 runs
favored Thin).
