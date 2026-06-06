---
name: feature-implement-reference
description: Examples and error messages for the feature-implement skill. Load on demand when you need to match output format to a specific scenario or diagnose an error.
type: reference
---

# Feature Implement Skill — Reference

Load this file when you need output format examples for a specific scenario or are diagnosing an error. The main SKILL.md contains the complete workflow; this file is supplementary.

---

## Examples

### Example 1: Fresh Implementation (TypeScript Project)

**Project Context**:
- Language: TypeScript 5.3
- Framework: React + Express
- Database: PostgreSQL
- Build: npm scripts
- Tests: Jest

**Input**: tasks.md with 0/45 tasks complete

**Execution**:
1. Check checklists (all complete) ✓
2. Load context (tasks, plan-summary.md or plan.md, spec-acceptance.md or spec.md)
3. Detect build/test commands: `npm test`, `npm run build`
4. Start Phase 1: Setup (3 tasks)
   - Execute T001, T002, T003
   - Commit: "Phase 1 - Setup"
5. Start Phase 2: Foundation (5 tasks)
   - Execute T004-T008
   - Run migration: `npm run migrate:dev` (detected from package.json)
   - Verify types: `npm run build`
   - Commit: "Phase 2 - Foundation"
6. Start Phase 3: User Story 1 (12 tasks)
   - Execute T009-T020
   - Run tests: `npm test`
   - Verify acceptance criteria
   - Commit: "Phase 3 - User Story 1 (MVP)"
7. Continue through remaining phases...
8. Final validation: `npm test && npm run build`
9. Report completion

**Output**: 45/45 tasks complete, 6 commits, feature ready

---

### Example 2: Resume from Checkpoint (Python Project)

**Project Context**:
- Language: Python 3.11
- Framework: Django
- Database: PostgreSQL
- Build: pytest, mypy
- Tests: pytest

**Input**: tasks.md with 14/45 tasks complete (stopped mid-Phase 3)

**Execution**:
1. Scan tasks.md: T001-T014 marked [X]
2. Find next task: T015 (in Phase 3)
3. Detect test command: `pytest`
4. Detect build command: `python manage.py check && mypy .`
5. Report status:
   ```
   Resuming implementation from T015
   Progress: 14/45 tasks (31%)
   Completed: Phase 1 ✓, Phase 2 ✓, Phase 3 (partial)
   ```
6. Continue Phase 3 from T015
7. Complete remaining phases
8. Final validation: `pytest && mypy .`

**Output**: 45/45 tasks complete, 4 new commits (phases 3-6)

---

### Example 3: MVP Only - Rust Project

**Project Context**:
- Language: Rust 1.70
- Framework: Axum (web) + SQLx (database)
- Database: PostgreSQL
- Build: cargo build
- Tests: cargo test

**Input**: tasks.md, user requests MVP only (User Story 1)

**Execution**:
1. Detect commands: `cargo test`, `cargo build`
2. Complete Phase 1: Setup
3. Complete Phase 2: Foundation
   - Run migration: `sqlx migrate run` (detected from project)
4. Complete Phase 3: User Story 1 (P1)
5. **STOP after Phase 3**
6. Run validation for MVP:
   - Tests: `cargo test` ✓
   - Build: `cargo build` ✓
   - User Story 1 acceptance criteria met ✓
7. Report:
   ```
   ✓ MVP complete (User Story 1)

   Progress: 20/45 tasks (44%)
   Completed Phases:
   - Phase 1: Setup ✓
   - Phase 2: Foundation ✓
   - Phase 3: User Story 1 (MVP) ✓

   Remaining:
   - Phase 4: User Story 2 (P2)
   - Phase 5: User Story 3 (P3)
   - Phase 6: Polish

   MVP ready for testing and feedback.
   Re-run feature-implement to continue with P2/P3.
   ```

---

## Error Messages

### Task Execution Failed

```
✗ T015 [US1] Failed: [Task description]

Error: [Error type]: [Error message]
File: [file path]:[line]

Likely Cause: [Analysis of error]

Suggested Fix:
1. [First suggested fix step]
2. [Second suggested fix step]
3. [Third suggested fix step]

Implementation halted at T015.
Progress saved: 14/45 tasks complete (T001-T014 marked [X])

Fix the error and re-run feature-implement to continue.
```

---

### Constitution Violation

```
⚠️ Constitution Violation Detected

Task: T013 [US1] [Task description]

Violation: [Description of violation]
File: [file path]:[line]
Code: [Violating code snippet]

Required Fix:
- Replace: [Current code]
- With: [Compliant code following project pattern]

Auto-fixing constitution violation...
✓ Fixed: [Description of fix applied]

Continuing implementation...
```

---

### Tests Failed

```
✗ Phase Validation Failed: Phase 3 checkpoint

Test Failures:
  [test file path]
    ✗ [test description]
      Expected: [expected value]
      Received: [actual value]

Build Status: ✓ PASS
Tests Status: ✗ FAIL (1 test failing)

Action Required:
1. Fix failing test in [test file]
2. Re-run: [test command for project]
3. Verify test passes before continuing

Implementation paused at Phase 3 checkpoint.
Tasks T009-T020 marked complete, but phase validation failed.
Fix tests and re-run feature-implement to continue.
```
