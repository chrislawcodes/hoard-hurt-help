# Post-mortem: strategy-first-onboarding

Claude-orchestrated run, 2026-06-15. Shipped as PR #411 (`ae70137`).

## What went well

- **The adversarial reviews earned their keep at the design stages.** Plan review
  caught issues a human (and I) would have missed: the 4-second connect-screen
  poll had the same "bounce when any provider is live" bug as the page (so
  connecting a *second* provider would fail), `enabled_provider_values` ignoring
  PAUSED connections, and the provider→client map omitting Hermes/OpenClaw. These
  were real and would have shipped broken.
- **Decoupling held up.** No DB migration was needed — readiness derives from
  existing connection-coverage helpers, exactly as the spec bet.
- **Reviews converged.** Spec in 3 rounds, plan in 4; each round found genuinely
  deeper issues, not nitpicks, then stopped.

## What didn't

- **Per-slice testing missed cross-cutting breakage.** Codex ran only the
  slice-relevant tests per slice, so 7 older tests that depended on the old
  create-agent redirect didn't fail until the full Preflight Gate at the end.
  Fixing them also surfaced a real inconsistency (the redirect used a *live*
  check while the readiness badge used an *enabled* check) — aligned both to the
  status-aware enabled check.
- **Codex runner flakiness.** The `codex` review subprocess hung on stdin twice
  (spec round 2, plan round 1), each time burning a full ~3-minute review round
  before failing. Re-running converged, but it cost two extra rounds.
- **Runner ergonomics cost real time:**
  - The per-slice loop is implement → **diff checkpoint** → next slice. The diff
    checkpoint is what advances the slice pointer; I initially re-ran `implement`
    without it and Codex redundantly re-did slice 0.
  - `tasks.md`'s intro prose contained the literal `[CHECKPOINT]`, so the runner
    miscounted 5 markers instead of 4 and kept recommending "dispatch next slice"
    after the work was done.
  - `deliver --create-pr` failed because the branch wasn't pushed and was 1 commit
    behind main; had to push + create the PR by hand.

## Proposed workflow changes (for human approval)

1. **Run the full suite, not just slice tests, before declaring a slice done** —
   or at minimum run the full Preflight Gate after the *first* slice that changes
   a shared route, to catch cross-cutting test breakage early instead of at the end.
2. **Count `[CHECKPOINT]` markers only on their own line** (e.g. lines matching
   `^- .* complete \[CHECKPOINT\]$`), so prose mentions in `tasks.md` don't inflate
   the slice count and confuse `next-action`.
3. **Make `deliver --create-pr` push the branch first** (and warn/rebase when
   behind) instead of failing on a missing remote ref.
4. **Add a short codex-runner stdin-hang guard / faster fail** so a hung review
   doesn't cost a full round before the retry.
