# Specification Quality Checklist

**Feature**: [spec.md](../spec.md)

## Content Quality
- [ ] Scope is a rename only — no game-rule/payoff/art/move-format changes (FR-017)
- [ ] Focused on user value (clear vocabulary, no broken bots, no lost data)
- [ ] All mandatory sections completed

## Requirement Completeness
- [ ] No [NEEDS CLARIFICATION] markers remain (all decisions made by Chris)
- [ ] Requirements testable and unambiguous
- [ ] Success criteria measurable (SC-001…006)
- [ ] All acceptance scenarios defined (US1–US5)
- [ ] Edge cases identified (old-ID redirect, alias args, interrupted migration, ID collision, empty DB)
- [ ] Scope clearly bounded — `app/games/` semantics and `GameState` enum are explicit keepers
