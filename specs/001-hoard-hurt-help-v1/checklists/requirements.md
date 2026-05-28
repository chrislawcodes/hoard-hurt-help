# Specification Quality Checklist

**Purpose**: validate spec completeness before implementation begins.
**Feature**: [spec.md](../spec.md), [spec-acceptance.md](../spec-acceptance.md)

## Content Quality

- [ ] Spec is grounded in observable system behavior (HTTP API shapes, DB schema, rules text) — not in vague feature descriptions.
- [ ] Player-facing terminology matches what the agent sees in the rules text.
- [ ] No implementation details (specific Python libraries, file names) leak into the spec body — those live in plan.md.
- [ ] All mandatory sections present: HTTP API, DB schema, rules text, state machine, turn resolution, MCP, ChatGPT, OAuth, file layout, errors, open questions.

## Requirement Completeness

- [ ] No `[NEEDS CLARIFICATION]` markers remain in spec.md (open items moved to §11 with clear status).
- [ ] Every endpoint in `contracts/api.yaml` is matched by a description in spec §1.
- [ ] Every entity in `data-model.md` is matched by a table in spec §2.
- [ ] Success criteria (`spec-acceptance.md` SC-001..SC-006) are measurable and testable.
- [ ] All acceptance scenarios in `spec-acceptance.md` follow Given/When/Then form.
- [ ] Edge cases covered: stacking, score floor, mutual bonus, missed turn, ties, idempotent submit, key revocation.
- [ ] Scope clearly bounded by the "What's NOT in v1" section in plan.md.

## Open Questions Status

- [ ] §11 lists open questions with status (Resolved vs. Open) and pointers to the decision (plan.md or "during implementation").
- [ ] Every "Open" item is small enough to be resolved during implementation without re-planning.
- [ ] Every "Resolved" item has a one-line summary of the chosen path.

## Constitution

- [ ] No constitution file present — validation skipped intentionally. Re-evaluate if a constitution is added later.
