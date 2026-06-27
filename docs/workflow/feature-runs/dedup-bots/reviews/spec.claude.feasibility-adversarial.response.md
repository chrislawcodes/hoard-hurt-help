## Findings

None — all prior blockers/majors resolved (D3 floor + both-pack test, D5 per-site base-captured pinning, byte-unchanged proof for not-a-true-duplicate, name-level test-ID diff). Verified the six sites match the spec table exactly.

## Residual Risks

- [minor] pick_by_trust empty-return is redundant at routed sites (all callers pre-guard) — diff gate weighs it (round-2 #14).
- [minor] _choose_from_candidates favor_high keeps its pre-filter; readability win is thin — reinforces honest framing that D5 may stay mostly not-a-true-duplicate.
