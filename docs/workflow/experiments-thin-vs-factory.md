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

---

## Running Tally

| Run | Slug | Feature type | Thin within noise on correctness? | Engine unique catch? | Friction (Factory vs Thin) |
|-----|------|--------------|-----------------------------------|----------------------|----------------------------|
| _none yet_ | | | | | |

**Sample size:** 0 runs.

**Switch recommendation:** _Undecided — no runs yet._ (After runs: state "SWITCH",
"KEEP", or "NEED MORE RUNS" with the count behind it.)
