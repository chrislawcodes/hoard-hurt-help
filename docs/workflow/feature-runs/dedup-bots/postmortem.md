# Post-mortem: dedup-bots (D3 + D5)

Second Claude-only Feature Factory run; the final actionable item from the
duplication inventory.

## What went well
- **The adversarial review's highest-value output was a decision NOT to refactor.**
  Both plan/spec lenses independently judged `pick_by_trust` over-abstraction for
  D5's one-line idiom (caller closures restate per-site seed/trust/sign, so a helper
  adds determinism risk with no real dedup). The FF process turned "do the dedup"
  into "prove D5 isn't worth deduping, and document why per-site" — the right
  engineering call, made explicit and auditable rather than by gut.
- **Recon before spec saved a wasted cluster.** Checking the code first showed D4
  was already unified in #551, so it never entered scope.
- **Tests-first, green-on-base** worked cleanly for D3; the D5 selectors gained
  determinism regression coverage (`context.turn`-in-seed, trust ordering) they
  previously lacked — net positive even though no production code changed.
- **The spec gate caught real test-methodology gaps** (characterization tests must
  be captured on the base first; per-site inputs must produce *different* picks;
  `not-a-true-duplicate` must be proven byte-unchanged) before implementation.

## What didn't work
- **The runner classified the feature TRIVIAL and recommended skipping FF**
  (`discover --complete` → "runner overhead exceeds its protection"). For a
  user-requested FF run this required `--force-path full`. Reasonable guard, but the
  3 spec review rounds on a ~110-line diff were arguably more ceremony than the
  change warranted — the review value was real (the D5 decision) but could have been
  reached with fewer rounds.
- **`deliver` still needs `gh`** (same gap as the engine run) — PR created via the
  GitHub MCP + `closeout --pr-number`. The fix proposed in the prior post-mortem
  (`docs/.../dedup-engine-cseries/`) still stands.
- **Diff-stage review returned empty** (`prepare-claude-reviews --stage diff` →
  `reviews: []`) for this small in-scope code change. With #562 ("only in-scope code
  changes re-open a clean review") merged, this is now expected behavior for a tiny
  diff — worth confirming it's intentional and not under-reviewing.

## Proposed workflow changes (for human approval)
1. **Carry forward the prior run's #1:** `deliver` no-`gh` path (accept
   `--pr-number`/`--pr-url`). Still the top gap on the web path.
2. **Right-size the review rounds for trivial-but-forced FF runs:** when
   `--force-path full` overrides a TRIVIAL estimate, consider a single spec review
   round by default (operator can opt into more), to match ceremony to risk.
3. **Document that an empty diff-stage review is the expected post-#562 behavior**
   for sub-threshold in-scope diffs, so it doesn't read as a skipped gate.
