## Findings

Confirmed round-2 majors resolved: AC2a green-on-base-first pinning, not-a-true-duplicate byte-unchanged proof, AC4 name-level test-ID diff. No remaining blocker/major.

- [minor] AC2a pins one input per site; a closure defect not exercised by that input could survive — backstopped by the `_seed_int` tuple rg check + full suite. Plan should use >=2 inputs per D5 site test (incl. a turn-varying case for _probe_target).
- [minor] AC1 D3 test relies on BotProfile equality; assert field-by-field or confirm it is a dataclass(eq=True).

## Residual Risks

- Single-input per-site characterization is narrower than "behavior-preserving" implies; mitigated by the seed-tuple rg check + suite. Plan uses >=2 inputs.
- not-a-true-duplicate floor is review-judgment; D3 hard floor + audited per-site ledger make a zero-D5 outcome explicit, not silent.
