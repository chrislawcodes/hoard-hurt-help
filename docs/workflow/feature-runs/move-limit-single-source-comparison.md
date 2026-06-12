# Experiment — Single source of truth for move length limits

## Outputs

- Direct Path: branch `direct/move-limit-single-source` (local, no PR) — commit on top of origin/main
- Feature Factory: branch `factory/move-limit-single-source` (local, no PR) — 3 commits on top of origin/main

Feature: make the public-`message` (200) and private-`thinking` (200) char caps a single source of truth so they can't silently drift apart again. No value change. Core deliverable: a regression test that fails on divergence.

## Did Reviews Change The Work?

| Stage | Path | Artifact | artifact_revised | issues_raised | issues_accepted | review_rounds |
|-------|------|----------|-----------------|---------------|-----------------|---------------|
| Spec | Direct Path | (collapsed) | n/a | n/a | n/a | 0 |
| Plan | Direct Path | (collapsed) | n/a | n/a | n/a | 0 |
| Tasks | Direct Path | (collapsed) | n/a | n/a | n/a | 0 |
| Implement | Direct Path | code | yes | 1 | 1 | 1 |
| Spec | Feature Factory | spec.md | yes | 2 | 2 | 1 |
| Plan | Feature Factory | plan.md | yes | 8 | 8 | 3 |
| Tasks | Feature Factory | tasks.md | no | 0 | 0 | 0 |
| Implement | Feature Factory | code | no | 2 | 0 (deferred) | 1 |

## Token Efficiency (Claude only)

| Path | Billed Input | Cache Read | Output | Real-Work (billed+output) |
|------|-------------|-----------|--------|--------------------------|
| Direct Path | 171,199 | 3,687,466 | 17,004 | 188,203 |
| Feature Factory | 417,767 | 32,674,286 | 64,109 | 481,876 |

Note: Feature Factory's adversarial reviews ran on the **Codex** and **Gemini** CLIs. Those provider tokens are NOT in the Claude totals above — Factory's true cost is higher still. Wall-clock: Direct ≈ 6 min, Factory ≈ 37 min.

## The two designs (both correct, both green)

| | Direct Path | Feature Factory |
|---|---|---|
| Source of truth | **new** `app/move_limits.py` (dependency-free, 2 constants) | constants added to **existing** `app/agent_prompt.py` (already optionally shared) |
| Connector at runtime | always uses its **own local copy** of the numbers; never imports `app` | **imports the real values** when `app/` is present; falls back to a local `_FALLBACK_*` copy only when standalone |
| Anti-drift guard | test: connector constant == server constant | test: connector constant == server constant **+ live clip behavior + app-blocked import branch** |
| Test | `tests/test_move_limit_single_source.py` — 5 tests, 101 lines, **structural** (constant/ schema/ prose equality) | `tests/test_move_length_limits.py` — 7 tests, 179 lines, **structural + behavioral** |
| Diff size | 154 lines, 4 files | 218 lines, 4 files |
| New files | 1 (move_limits.py) | 0 (reused agent_prompt.py) |

## Outcome

- **Did Feature Factory catch problems the Direct Path missed?** Yes, one real one. Its Plan-stage review (Round 2, flagged HIGH) caught that pinning the *constants* isn't enough — the test must also exercise the *actual clip behavior*, or a wrong hardcoded literal at a call site would slip through green. Factory added behavioral clip tests + an `app`-blocked import test. Direct's test is purely structural and would not catch a bad call-site literal.
- **Did the extra reviews change the code/scope/tests?** Yes. Spec review drove Factory's explicit fallback-constant design; Plan review materially strengthened the test. (Direct's single self-review only extended its test to pin the prompt prose — a smaller change.)
- **Was the overhead worth it?** Quality-wise the review earned its keep (a stronger safety net). Cost-wise it was ~2.5× the Claude real-work tokens, ~6.5× the wall-clock, plus uncounted Codex/Gemini calls — steep for a low-risk refactor whose values never change.
- **Which path next time?** For a mechanical refactor where you just need it done: Direct Path. When the deliverable IS the safety-net test and you want it to catch behavior (not just constants): Feature Factory's reviews demonstrably upgrade a structural test into a behavioral one. This is a backend/correctness-flavored task, and Factory again found a real gap — consistent with the ValueRank "backend → Feature Factory" pattern.
