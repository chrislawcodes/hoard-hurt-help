# Specification Quality Checklist

**Purpose**: Validate spec completeness before implementation
**Feature**: [spec.md](../spec.md)

## Content Quality
- [ ] No implementation details in spec (HOW lives in plan.md)
- [ ] Focused on user value (first-timer reaches "confirmed playing")
- [ ] Readable by a non-technical stakeholder
- [ ] All mandatory sections present

## Requirement Completeness
- [ ] No [NEEDS CLARIFICATION] markers remain
- [ ] Requirements (FR-001..013) testable and unambiguous
- [ ] Success criteria (SC-001..006) measurable / outcome-based
- [ ] All acceptance scenarios defined (US1–US6)
- [ ] Edge cases identified (page-closed connect, paused bot, reissue-vs-disconnect, multi-tab, no open games)
- [ ] Scope bounded (panel + signal + small copy fixes; auto-join and message redesign out)

## Feature-Specific
- [ ] Bad-key handling stated honestly as passive (Decision 5), not a promised live event
- [ ] Data-critical migration is additive + backfill-free (FR-013)
- [ ] Paste-once credential model preserved (FR-011)
- [ ] Owner-only privacy stated (FR-010)
