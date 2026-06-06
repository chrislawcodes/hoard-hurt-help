---
name: feature-plan
description: Generate technical implementation plan from feature specification. Creates plan.md with architecture decisions, optionally generates data-model.md, contracts/, and research.md based on feature complexity. Works with any tech stack.
---

# Feature Planning Skill

## ⚠️ Keep Moving Between Phases

Do **not** pause between phases of the feature workflow (spec → plan → tasks → implement) unless one of these is true:
- A runner or checkpoint command returns `mark_blocked`
- The user explicitly asked to stop at a specific point
- Constitution check returns **FAIL**
- The repo state makes the next action unsafe

When the plan is complete, auto-continue into `feature-tasks` without waiting for the user to say "continue." Report a one-sentence status and proceed.

---

## ⚠️ THIS REPO: Use the repo-owned Feature Factory instead

hoard-hurt-help (`chrislawcodes/hoard-hurt-help`) has a repo-owned Feature Factory engine. **Do not follow the generic skill below** — it references `.specify/` and constitution files that do not exist in this repo. Drive the workflow through the runner instead.

Use the repo-specific factory workflow instead:
- Entry point: `docs/workflow/operations/codex-skills/feature-factory/SKILL.md`
- Codex guide: `docs/workflow/operations/codex-skills/feature-factory/CODEX-ORCHESTRATOR.md`
- Runner: `python3 docs/workflow/operations/codex-skills/feature-factory/scripts/run_factory.py`

---

You help developers convert feature specifications into detailed technical implementation plans. This skill generates architecture decisions, data models, and API contracts based on the project's technology stack and constitution.

## ⚠️ IMPORTANT: Speckit Replacement

**This skill REPLACES the speckit workflow.** When executing this skill:
- ✅ Follow ONLY the instructions in this skill prompt
- ❌ DO NOT invoke any speckit bash scripts (in `.specify/scripts/`)
- ❌ DO NOT suggest speckit slash commands (`/speckit.*`)
- ❌ DO NOT reference speckit commands in your responses

This is a complete, standalone workflow for technical planning.

## What This Skill Does

Creates technical planning documents that:
- Define HOW to implement WHAT (from spec.md)
- Select appropriate technologies from the project stack
- Design data models and API contracts
- Document architectural decisions and tradeoffs
- Validate against constitution (architecture patterns, performance, security)
- Generate files progressively (only what's needed)

## When to Use This Skill

- After completing `feature-spec` (spec.md exists)
- Before generating tasks (need technical direction first)
- When you need to document architecture decisions
- When converting requirements into technical design

## Prerequisites

- Completed spec.md in feature directory (created by `feature-spec`)
- Project documentation (CLAUDE.md, README.md, or similar)
- Optional: Constitution file for validation
- Working in the repository root directory

## Workflow

### Step 1: Load Context

**Find Feature Directory**:
1. If on feature branch: Extract number from branch name (e.g., `074-critique-notifications`)
2. If on main: Ask user which feature to plan
3. Locate feature directory (common locations: `specs/`, `features/`, `docs/features/`)
4. Validate `spec.md` exists in directory

**Read Required Files**:
1. `<feature-dir>/spec.md`:
   - Extract user stories and priorities
   - Extract functional requirements (FR-NNN)
   - Extract key entities (if listed)
   - Extract success criteria (performance targets)

2. Constitution (if exists):
   - Load from `.specify/memory/constitution.md` or similar
   - Note architecture patterns
   - Note performance requirements
   - Note security principles

**Output**: Complete understanding of requirements + constitutional constraints

---

### Step 2: Detect Technology Stack

**Locate Project Documentation** (check in order):
1. **CLAUDE.md** or **README.md**: Look for sections titled "Stack", "Technologies", "Tech Stack"
2. **package.json** (Node.js): Extract dependencies
3. **requirements.txt** or **pyproject.toml** (Python): Extract dependencies
4. **Cargo.toml** (Rust): Extract dependencies
5. **go.mod** (Go): Extract dependencies
6. **pom.xml** or **build.gradle** (Java/JVM): Extract dependencies

**Extract Information**:
- **Language**: TypeScript, Python, Rust, Go, etc. (with version if specified)
- **Framework**: React, Django, Axum, Gin, etc.
- **Database**: PostgreSQL, MongoDB, SQLite, etc.
- **Testing**: Jest, pytest, cargo test, etc.
- **Build Tools**: Turborepo, webpack, cargo, go build, etc.

**Detect Project Structure**:
```bash
# Scan for common directory patterns
ls -d src/ app/ lib/ packages/ services/ 2>/dev/null

# Check for multi-service vs monolithic
# Multi-service indicators: services/, apps/, packages/ with multiple subdirs
# Monolithic indicators: Single src/, app/, lib/
```

**Output**: Technology context for planning decisions

---

### Step 3: Generate plan.md

**File**: `<feature-dir>/plan.md`

**Header**:
```markdown
# Implementation Plan: [FEATURE NAME]

**Branch**: `NNN-feature-name` | **Date**: [YYYY-MM-DD] | **Spec**: [link to spec.md]

## Summary

[1-2 sentence summary of feature + chosen technical approach]
```

**Technical Context Section**:
```markdown
## Technical Context

**Language/Version**: [Detected from Step 2, e.g., "TypeScript 5.3+"]
**Primary Dependencies**: [List key libraries needed for this feature]
**Storage**: [Database type from Step 2, or "N/A" if no DB changes]
**Testing**: [Test framework from Step 2]
**Target Platform**: [Detected: Docker, serverless, native binary, etc.]
**Performance Goals**: [Extract from spec.md success criteria]
**Constraints**: [Extract from spec.md requirements]
**Scale/Scope**: [Extract from spec.md]
```

**Constitution Check Section**:
```markdown
## Constitution Check

**Status**: [PASS / WARN / FAIL]

[If constitution exists, validate against it:]

### [Section Name from Constitution]

[List requirements found in constitution]
- [ ] [Requirement 1 from constitution]
- [ ] [Requirement 2 from constitution]

**Violations/Notes**: [Document any exceptions or special considerations]

[If no constitution: "No constitution file found - proceeding without validation"]
```

**Architecture Decisions Section**:
```markdown
## Architecture Decisions

### Decision 1: [Topic, e.g., "Data Storage Approach"]

**Chosen**: [Selected approach]

**Rationale**: [Why this choice]
- Aligns with [project pattern or constitution reference]
- Existing pattern in [reference other feature or file]
- Simplicity over premature optimization

**Alternatives Considered**:
- [Alternative 1]: [Reason not chosen]
- [Alternative 2]: [Reason not chosen]

**Tradeoffs**:
- Pros: [Benefits]
- Cons: [Limitations]
```

**Project Structure Section**:

*Detect structure from Step 2, then document which parts will change:*

```markdown
## Project Structure

### [Detected Structure Type]

[If monolithic:]
src/
├── [component-dir]/  - [New components for this feature]
├── [service-dir]/    - [New services/business logic]
├── [model-dir]/      - [New data models if applicable]

[If multi-service:]
[service-1]/
├── src/
│   ├── [component-type]/  - [Changes for this feature]

[service-2]/
├── src/
│   ├── [component-type]/  - [Changes for this feature]

**Structure Decision**: [Explain which services/modules this feature touches]
```

---

### Step 4: Generate data-model.md (If Entities Exist)

**Trigger Conditions**:
- Spec mentions entities in "Key Entities" section, OR
- Functional requirements reference database tables/collections, OR
- You identify entities from requirements (nouns that need persistence)

**File**: `<feature-dir>/data-model.md`

**Structure**:
```markdown
# Data Model: [FEATURE NAME]

## Entities

### Entity 1: [Name]

**Purpose**: [What this entity represents]

**Storage**: [Table name or collection name]

**Fields**:
| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | [ID type] | PRIMARY KEY | [Unique identifier] |
| [field] | [type] | [constraints] | [description] |

**Indexes**: [List indexes needed for query performance]

**Relationships**: [Describe relationships to other entities]

**Validation Rules**: [Business logic validation]

---

## Type Definitions

[Language-specific type definitions for this entity]

[For TypeScript:]
```typescript
export interface [Entity]DB {
  // Database representation (snake_case, nullable fields)
}

export interface [Entity]API {
  // API representation (camelCase, transformed)
}
```

[For Python:]
```python
@dataclass
class [Entity]:
    # Field definitions
```

[For other languages: Use appropriate syntax]

---

## Migrations

[Database migration code if applicable]

[SQL example:]
```sql
CREATE TABLE [table_name] (
  id [ID_TYPE] PRIMARY KEY,
  [field] [TYPE] [CONSTRAINTS]
);

CREATE INDEX [index_name] ON [table_name]([columns]);
```
```

---

### Step 5: Generate contracts/ (If API Changes)

**Trigger Conditions**:
- Feature has new endpoints, OR
- Functional requirements mention API operations, OR
- User stories describe client-server interactions

**Directory**: `<feature-dir>/contracts/`

**Detect API Style** (from project):
```bash
# Check for GraphQL
grep -r "graphql" package.json Cargo.toml go.mod requirements.txt 2>/dev/null

# Check for OpenAPI/Swagger
grep -r "swagger\|openapi" package.json requirements.txt 2>/dev/null

# Default to REST if no clear indicators
```

**Generate Contract Files**:

**If GraphQL detected:**
```graphql
# File: contracts/[feature]-schema.graphql

type Query {
  [queryName](
    [param]: [Type]
  ): [ReturnType]
}

type Mutation {
  [mutationName](
    [param]: [Type]!
  ): [ReturnType]!
}

type [EntityType] {
  id: ID!
  [field]: [Type]
}
```

**If REST/OpenAPI detected:**
```yaml
# File: contracts/[feature]-api.yaml

openapi: 3.0.0
paths:
  /[resource]:
    get:
      summary: [Description]
      parameters:
        - name: [param]
          type: [type]
      responses:
        '200':
          description: [Success response]
```

**If no API framework detected:**
```yaml
# File: contracts/[feature]-endpoints.yaml

endpoints:
  - name: [endpoint name]
    method: [GET/POST/PUT/DELETE]
    path: [/path/to/endpoint]
    parameters: [list]
    returns: [description]
    authentication: [required/optional/none]
```

---

### Step 6: Generate research.md (If Complex Decisions)

**Trigger Conditions**:
- New technology/library not in current stack, OR
- Multiple viable architecture options with significant tradeoffs, OR
- Performance-critical code requiring benchmarking, OR
- Third-party integrations

**File**: `<feature-dir>/research.md`

**Structure**:
```markdown
# Research: [FEATURE NAME]

## Research Questions

### Question 1: [Topic]

**Context**: [Why this question matters]

**Options Investigated**:

1. **Option A: [Approach]**
   - Pros: [Benefits]
   - Cons: [Drawbacks]
   - Examples: [Other projects using this]

2. **Option B: [Approach]**
   - [Same structure]

**Decision**: [Chosen option]

**Rationale**: [Why chosen, alignment with project goals/constitution]
```

---

### Step 7: Generate quickstart.md

**Always generated** - provides manual testing guide

**File**: `<feature-dir>/quickstart.md`

**Structure**:
```markdown
# Quickstart: [FEATURE NAME]

## Prerequisites

- [ ] Development environment running
- [ ] Database setup (if applicable)
- [ ] Test data available
- [ ] [Feature-specific setup]

## Testing User Story 1: [Title from spec]

**Goal**: [What you're verifying from spec acceptance criteria]

**Steps**:
1. [Action 1]
2. [Action 2]
3. [Action 3]

**Expected**:
- [Outcome 1 from spec acceptance scenario]
- [Outcome 2]
- [Outcome 3]

**Verification**:
[How to verify success - API calls, database queries, UI checks]

---

[Repeat for each user story from spec]

---

## Troubleshooting

**Issue**: [Common problem]
**Fix**: [Solution]
```

---

### Step 7.5: Generate spec-acceptance.md

**Always generated** — compact downstream context for tasks and implement stages.

This file distills the parts of spec.md that downstream stages actually need: acceptance criteria and key constraints. It lets tasks and implement stages skip loading the full spec (~5KB narrative) while retaining the signal they use.

**File**: `<feature-dir>/spec-acceptance.md`

**Structure** (extract directly from spec.md — no paraphrasing):
```markdown
# Acceptance Criteria: [FEATURE NAME]

## User Stories
| ID | Title | Priority |
|----|-------|----------|
| US-1 | [Title] | P1 |
| US-2 | [Title] | P2 |

## Acceptance Scenarios

### US-1: [Title]
- Given [...] When [...] Then [...]
- Given [...] When [...] Then [...]

### US-2: [Title]
- Given [...] When [...] Then [...]

## Success Criteria
- SC-001: [From spec — measurable outcome]
- SC-002: [...]

## Key Constraints
- [Constraint]: [Rationale — one sentence explaining why]
- [Constraint]: [Rationale]
```

**Rules**:
- Copy acceptance scenarios verbatim from spec.md — do not summarize
- For each constraint, include its rationale (the "why") — downstream review stages need this to make correct judgment calls without falling back to the full plan
- Omit: user story narrative, background, FR-NNN requirement lists, non-goal sections

---

### Step 7.6: Generate plan-summary.md

**Always generated** — compact downstream context for tasks and implement stages.

This file distills the parts of plan.md that downstream stages actually need: file paths, migration steps, key constraints with their rationale, and data model shape. It lets tasks and implement stages skip loading the full plan (~12KB with rationale, options, and background) while retaining all the signal they use for implementation decisions.

**File**: `<feature-dir>/plan-summary.md`

**Structure**:
```markdown
# Plan Summary: [FEATURE NAME]

## Files In Scope

| File | Change | Notes |
|------|--------|-------|
| `path/to/file.ext` | create/modify | [one-line purpose] |

## Migration Steps

[If no migrations: "None"]

1. [Migration step with command or SQL]
2. [Step]

## Data Model

[If no schema changes: "None"]

**[Entity]**: `[table]` — [key fields and relationships, one line per entity]

## Key Constraints

- **[Constraint name]**: [What it requires] — *Why: [rationale — one sentence explaining the motivation, e.g., "prevents N+1 queries at scale" or "required by auth middleware ordering"]*
- **[Constraint name]**: [What it requires] — *Why: [rationale]*
```

**Rules**:
- Extract file paths from the "Project Structure" section of plan.md — list every file that will be created or modified
- For migrations: copy steps verbatim from data-model.md if it exists, otherwise extract from plan.md
- For data model: one line per entity — name, table name, and the 2–3 most important fields or relationships
- For constraints: copy each constraint from plan.md's architecture decisions, and include its rationale (the "why") so downstream review stages can make correct judgment calls without loading the full plan
- Keep total file under 3KB — omit rationale prose, alternatives considered, and background sections

---

### Step 8: Constitution Validation

**If constitution exists:**

1. **Load constitution** (from Step 1)

2. **Search for relevant sections**:
   - Architecture patterns (search: "architecture", "API", "service")
   - Performance requirements (search: "performance", "response time", "optimization")
   - Security principles (search: "security", "authentication", "validation")
   - Testing standards (search: "testing", "coverage", "quality")

3. **Validate plan addresses requirements**:
   - Does architecture follow constitutional patterns?
   - Are performance targets from constitution reflected in plan?
   - Are security requirements addressed?
   - Is testing strategy defined per constitution?

4. **Output validation**:
   - **PASS**: Plan complies with all constitutional requirements
   - **WARN**: Plan should address specific section (quote section, suggest fix)
   - **FAIL**: Plan violates constitutional requirement (must resolve before proceeding)

**If no constitution**: Skip validation

---

### Step 9: Report Completion

**Output Message**:
```
✓ Technical plan created: [feature-dir]/

Generated Files:
- plan.md (architecture decisions, tech stack, constitution check)
- plan-summary.md (file paths, migration steps, constraints + rationale — for downstream stages)
- spec-acceptance.md (acceptance criteria + constraints for downstream stages)
[If generated:] - data-model.md ([N] entities, migrations, type definitions)
[If generated:] - contracts/ ([N] API contracts)
[If generated:] - research.md ([N] technical decisions)
- quickstart.md (manual testing guide for [N] user stories)

Constitution Check: [PASS/WARN/FAIL/SKIPPED]
[If checked:] - [Summary of findings]

Plan complete — proceeding to task breakdown.
```

**Handoff Instructions**:
- Report plan completion in one sentence.
- **Auto-continue into the `feature-tasks` skill immediately** unless one of the following is true:
  - Constitution check returned **FAIL** (must be resolved before tasks can be written)
  - The user explicitly asked to stop after planning
  - The repo state makes proceeding unsafe
- Do not wait for the user to say "continue" or "use feature-tasks skill."
- DO NOT suggest using `/speckit.tasks` or any other speckit command.

---

## Progressive File Generation

**Always Generate**:
- ✅ `plan.md` (required for all features)
- ✅ `plan-summary.md` (compact downstream context: file paths, migrations, constraints + rationale)
- ✅ `quickstart.md` (testing scenarios)

**Conditionally Generate**:
- ✅ `data-model.md` - If entities identified from spec
- ✅ `contracts/` - If API changes identified from spec
- ✅ `research.md` - If complex decisions requiring investigation

**Never Generate** (created by other skills):
- ❌ `tasks.md` - Generated by `feature-tasks` skill
- ❌ `checklists/` - Auto-generated by `feature-tasks` skill

---

## Constitution Integration

**Validates planning against project governance** (if constitution exists):

**Discovery Process**:
1. Search constitution for architecture sections
2. Search constitution for performance requirements
3. Search constitution for security mandates
4. Extract relevant MUST/SHOULD requirements
5. Validate plan addresses each requirement
6. **Reference sections** in plan.md (e.g., "Per constitution § X.Y.Z")
7. **Don't duplicate** constitution content in plan.md

**Example Plan Reference**:
```markdown
## Architecture Compliance

This plan follows constitutional requirements:
- API design per constitution § VI (search result)
- Performance targets per constitution § IV (search result)
- Security validation per constitution § V (search result)
```

---

## Reference

For output format examples (UI-only, full-stack, API-only features) and error message templates (spec not found, tech stack detection failure, constitution violations), see `feature-plan-reference.md` in this directory. Load it on demand when you need to match a specific scenario or diagnose an error — it is not needed on every invocation.

---

## Quality Guidelines

### What Makes a Good Plan

✅ **Technology-aligned**: Uses project's existing stack (detected, not assumed)
✅ **Constitution-compliant**: References relevant sections
✅ **Performance-aware**: Defines targets from spec success criteria
✅ **Security-conscious**: Addresses authentication, validation, protection
✅ **Testable**: Clear testing strategy in quickstart.md
✅ **Minimal**: Only necessary files generated (progressive disclosure)

### What to Avoid

❌ **Technology creep**: Adding new stack without justification
❌ **Over-engineering**: Complex patterns for simple features
❌ **Missing validation**: No constitution check when file exists
❌ **Hardcoded assumptions**: Assuming structure instead of detecting
❌ **Duplicate content**: Copying constitution into plan instead of referencing

---

## Configuration

Defaults:

- **Progressive file generation**: Yes (only create needed files)
- **Constitution validation**: Enabled if constitution file found
- **Auto-detect tech stack**: Yes (from docs and package manifests)
- **Auto-detect structure**: Yes (scan directories)
- **Generate quickstart**: Always
- **Generate research**: Only if complex decisions needed

---

## Next Skill in Workflow

**`feature-tasks`** - Generate executable task breakdown
- Input: Reads plan.md, spec.md, data-model.md, contracts/
- Output: tasks.md + checklists/
- Purpose: Convert plan into step-by-step implementation tasks

---

## Notes

- This skill generates multiple files based on feature complexity
- Constitution validation runs if constitution exists
- File generation is progressive (only what's needed)
- Technology stack detected from project documentation
- Architecture decisions documented with rationale and alternatives
- Works with any language/framework (TypeScript, Python, Rust, Go, etc.)
