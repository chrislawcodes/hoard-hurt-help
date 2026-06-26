---
reviewer: gemini
lens: requirements-adversarial
stage: spec
model: claude-sub-agent
note: "Claude-only run: Codex/Gemini CLIs unavailable in this environment; this adversarial lens was produced by an independent Claude sub-agent acting as the requirements/testability reviewer."
resolution_status: "accepted"
resolution_note: "All blocker/major/minor findings incorporated into spec.md (revised clusters table, dispositions incl. not-a-true-duplicate, C2 3-axis params, C8 field-only _mark_cancelled with cycle-free home, narrowed C4, required characterization tests, tightened deferral floor, Validation deliverable). See spec reconciliation table."
---

# Requirements/testability-adversarial review — spec (engine C-series dedup)

Independent adversarial read of the first spec draft as a requirements document.

## Blockers

- **R1 [blocker] C2 understates divergence and does not pin it.** Beyond phase and
  resume-guard, `_open_turn` sets `current_round`+`current_turn` while
  `_open_actor_turn` sets only `current_turn`. Acceptance criteria must explicitly
  require the unified opener to write the same `Match` columns per mode and to
  carry the get-or-create only in simultaneous mode.
- **R2 [blocker] "Full pytest is the oracle" is unvalidated.** An oracle is valid
  only if it FAILS on a behavior change. No current test is shown to fail on the
  C2 `current_round` write or the C4 reserved-seat difference. Require named
  characterization tests, written before refactor, each demonstrably failing under
  a wrong merge.

## Major

- **R3 [major] Deferral escape hatch is a loophole.** "Defer in closeout with a
  reason" has no floor and no gate sign-off — an implementer could defer every
  non-mechanical cluster and still pass. Require: mechanical clusters
  (C1/C3/C6/C7) non-deferrable; a deferral must cite the specific risk + the
  characterization test that proves it, approved at a review gate.
- **R4 [major] "grep shows copies gone" is not a proof of absence.** Reworded
  copies pass a literal grep. Require presence-based proof (each old site imports
  the shared symbol) plus an exact per-cluster check (e.g. one `def _has_moved`).
- **R5 [major] Missing "not-a-true-duplicate" disposition.** The spec assumes every
  cluster is a real duplicate; C2 is the counter-example. A correct "leave as-is,
  document divergence" outcome must be a first-class disposition, not mislabeled as
  a deferral.
- **R6 [major] Shared-symbol homes/naming too vague to enforce.** "Domain-named" is
  not checkable. Each new/relocated shared symbol's target module must be named in
  the plan; a cross-module symbol should drop its `_` private prefix.
- **R7 [major] CLAUDE.md Validation deliverable + per-cluster commits missing.**
  This is not a small change, so the PR must carry a `Validation` section (ruff,
  mypy, full pytest, test count). Per-cluster commits/diff-checkpoints let a risky
  cluster be reverted without losing the mechanical ones.

## Minor

- **R8 [minor] Test-count ≥1291 is weak.** Guards deletion, not weakening, and
  rises as new tests land. Reframe as "branch-base + new characterization tests;
  no test removed/skipped/xfail'd."
- **R9 [minor] C8 "where registry/logging allows" is fuzzy.** Require the extracted
  helper not absorb per-site logging/registry teardown, and assert each cancel site
  keeps its ops-event reason and registry behavior.
- **R10 [minor] C1 timing not pinned.** Add a one-line assertion that the unified
  now-helper is tz-aware UTC and `deadline_at` arithmetic is byte-identical.

## Verdict

Sufficient-with-changes — but the changes are not cosmetic. Two blockers (C2
divergence not pinned; oracle unvalidated) must be fixed before implementation, plus
the deferral floor, the not-a-true-duplicate disposition, concrete module homes, and
the Validation deliverable.

## Resolution
- status: accepted
- note: All blocker/major/minor findings incorporated into spec.md (revised clusters table, dispositions incl. not-a-true-duplicate, C2 3-axis params, C8 field-only _mark_cancelled with cycle-free home, narrowed C4, required characterization tests, tightened deferral floor, Validation deliverable). See spec reconciliation table.
