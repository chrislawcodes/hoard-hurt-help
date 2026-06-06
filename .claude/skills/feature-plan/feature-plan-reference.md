---
name: feature-plan-reference
description: Examples and error messages for the feature-plan skill. Load on demand when you need to diagnose an error or match output format to a specific scenario.
type: reference
---

# Feature Plan Skill — Reference

Load this file when you need output format examples for a specific scenario or are diagnosing an error. The main SKILL.md contains the complete workflow; this file is supplementary.

---

## Examples

### Example 1: Simple UI-Only Feature

**Input**: Spec for "Dark mode toggle in settings"

**Detected**:
- Tech: React + TypeScript (from package.json)
- Structure: Monolithic `src/` (no services/)
- No database changes

**Output**:
- `plan.md` - Frontend only, CSS custom properties approach
- `plan-summary.md` - File scope, no migration steps
- `quickstart.md` - Manual testing steps
- NO data-model.md (no entities)
- NO contracts/ (no API changes)
- NO research.md (straightforward implementation)

---

### Example 2: Full-Stack Feature with Database

**Input**: Spec for "Email notifications for critique responses"

**Detected**:
- Tech: Node.js + PostgreSQL (from package.json + docs)
- Structure: Multi-service `services/` (4 services detected)
- Database changes required

**Output**:
- `plan.md` - Full architecture (Frontend + API + Database + job queue)
- `plan-summary.md` - File scope table, migration steps, key constraints
- `data-model.md` - Notification entity, migration, indexes
- `contracts/notifications-api.graphql` - 4 queries/mutations (GraphQL detected)
- `quickstart.md` - Testing all 3 user stories
- `research.md` - Decision: job queue library selection

---

### Example 3: API-Only Feature (Python)

**Input**: Spec for "Batch update API endpoint"

**Detected**:
- Tech: Python + FastAPI (from requirements.txt)
- Structure: Monolithic `app/`
- Uses existing database models

**Output**:
- `plan.md` - API service only, batch processing strategy
- `plan-summary.md` - File scope, no migration steps, key constraints
- `contracts/batch-api.yaml` - OpenAPI schema for new endpoint
- `quickstart.md` - API testing examples
- NO data-model.md (uses existing models)
- NO research.md (clear implementation path)

---

## Error Messages

### Spec Not Found

```
ERROR: No specification found

Searched: specs/074-critique-notifications/spec.md
         features/074-critique-notifications/spec.md

Please run `feature-spec` skill first to create the feature specification.
```

### Cannot Detect Tech Stack

```
WARNING: Could not detect technology stack

Searched:
- CLAUDE.md (not found)
- README.md (no "Stack" section)
- package.json (not found)

Please specify tech stack manually:
- Language: [?]
- Framework: [?]
- Database: [?]

Or create CLAUDE.md with "### Stack" section documenting your technologies.
```

### Constitution Violation

```
CRITICAL: Constitution violation detected

Found section: "API Design Patterns"
Requirement: "All write operations must use [pattern] per § X.Y.Z"

Issue: Plan specifies [different pattern]

Action Required:
1. Update plan.md to use constitutional pattern
2. Document reason if exception needed
3. Re-run constitution validation
```
