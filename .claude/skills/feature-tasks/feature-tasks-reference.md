---
name: feature-tasks-reference
description: Examples and error messages for the feature-tasks skill. Load on demand when you need to match output format to a specific scenario or diagnose an error.
type: reference
---

# Feature Tasks Skill — Reference

Load this file when you need output format examples for a specific scenario or are diagnosing an error. The main SKILL.md contains the complete workflow; this file is supplementary.

---

## Examples

### Example 1: TypeScript React Feature

**Input**: Plan with React components, spec with 2 user stories

**Output**:
- tasks.md with 25 tasks organized by user story
- Paths use plan-summary.md (or plan.md) structure: `src/components/`, `src/hooks/`
- Checklists reference TypeScript/React best practices
- If constitution exists: References logging, URL construction requirements

### Example 2: Python Django Feature

**Input**: Plan with Django views, spec with 3 user stories

**Output**:
- tasks.md with 35 tasks
- Paths use plan-summary.md (or plan.md) structure: `app/views/`, `app/models/`, `app/tests/`
- Checklists reference Django patterns
- If constitution exists: References testing requirements

### Example 3: Rust Microservice

**Input**: Plan with Axum handlers, spec with 1 user story

**Output**:
- tasks.md with 15 tasks
- Paths: `src/handlers/`, `src/models/`, `src/db/`
- Checklists reference Rust idioms
- Constitution check for error handling, testing

---

## Error Messages

### Missing Prerequisites

```
ERROR: Missing required files

Expected: <feature-dir>/plan.md
Found: None

Please run `feature-plan` skill first to generate technical plan.
```

### Invalid User Stories

```
ERROR: No user stories found in spec.md

Spec must have at least one user story with priority (P1, P2, or P3).

Please update spec.md or re-run `feature-spec` skill.
```

### Cannot Extract Paths

```
WARNING: Could not find "Project Structure" section in plan.md

Using generic placeholder paths:
- <component-dir>/
- <service-dir>/
- <model-dir>/

Recommendation: Update plan.md with specific paths, then regenerate tasks.
```
