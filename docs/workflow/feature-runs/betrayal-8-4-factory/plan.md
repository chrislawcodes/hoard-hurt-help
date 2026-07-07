# Plan

## Review Reconciliation

- review: reviews/spec.claude.feasibility-adversarial.review.md | status: accepted | note: Round 2: no HIGH/MED feasibility defects — reviewer CODE-CONFIRMED all round-1 resolutions are sound. 2 LOW: ARCHITECTURE.md has no BETRAYAL_HURT_POINTS token (redundant listing, harmless — keep for prose refresh); betrayal_bonus needs a feed-chip consumer -> already fixed by adding turn_block.html to scope + AC5 in the final revision.
- review: reviews/spec.claude.requirements-adversarial.review.md | status: accepted | note: Round 2: MED F1 (turn_block.html out of scope but AC5 needs it) -> FIXED in final revision: turn_block.html added to scope with an explicit +betrayal_bonus chip render, §3.4 + AC5 updated. LOW F2 (no feed-render test) -> §8 now asserts the +4 reaches rendered HTML. LOW F3 (stale 'decays each round' legend text) -> explicit decision: leave it (pre-existing, out of scope), edit only the Hurt clause.
