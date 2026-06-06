---
name: feature-tasks
description: Generate executable task breakdown from feature plan. Creates tasks.md organized by user story priority with dependency tracking, plus auto-generated quality checklists that reference project constitution.
---

# Feature Tasks Skill

## ⚠️ Keep Moving Between Phases

Do **not** pause between phases of the feature workflow (spec → plan → tasks → implement) unless one of these is true:
- A runner or checkpoint command returns `mark_blocked`
- The user explicitly asked to stop at a specific point
- Constitution check returns **FAIL**
- The repo state makes the next action unsafe

When tasks are complete, auto-continue into `feature-implement` (or `/feature-implement <slug>` in ValueRank) without waiting for the user to say "continue." Report a one-sentence status and proceed.

---

## ⚠️ THIS REPO: Use the repo-owned Feature Factory instead

hoard-hurt-help (`chrislawcodes/hoard-hurt-help`) has a repo-owned Feature Factory engine. **Do not follow the generic skill below** — it references `.specify/` and constitution files that do not exist in this repo. Drive the workflow through the runner instead.

Use the repo-specific factory workflow instead:
- Entry point: `docs/workflow/operations/codex-skills/feature-factory/SKILL.md`
- Codex guide: `docs/workflow/operations/codex-skills/feature-factory/CODEX-ORCHESTRATOR.md`
- Runner: `python3 docs/workflow/operations/codex-skills/feature-factory/scripts/run_factory.py`

---

You help developers break down technical plans into executable, dependency-ordered tasks. This skill generates a complete tasks.md file with phase-based organization and automatic quality checklists.

## ⚠️ IMPORTANT: Speckit Replacement

**This skill REPLACES the speckit workflow.** When executing this skill:
- ✅ Follow ONLY the instructions in this skill prompt
- ❌ DO NOT invoke any speckit bash scripts (in `.specify/scripts/`)
- ❌ DO NOT suggest speckit slash commands (`/speckit.*`)
- ❌ DO NOT reference speckit commands in your responses

This is a complete, standalone workflow for task generation.

## What This Skill Does

Creates implementation tasks that:
- Break plan into small, reviewable work items
- Organize by user story priority (P1 → P2 → P3)
- Define clear dependencies (sequential vs parallel)
- Specify exact file paths for each task
- Auto-generate quality checklists for validation
- Enable independent testing of each user story

## When to Use This Skill

- After completing `feature-plan` (plan.md exists)
- Before implementation begins
- When you need a clear execution roadmap
- When organizing work for multiple developers

## Prerequisites

- Completed `plan.md` in feature directory
- Completed `spec.md` in feature directory
- Optional: `data-model.md`, `contracts/`, `research.md`
- Working in repository root directory

## Workflow

### Step 1: Load Context

**Read Required Files**:
1. Spec context — load whichever exists (prefer the compact form):
   - **`<feature-dir>/spec-acceptance.md`** (preferred — acceptance criteria + constraints, ~10 lines): user story titles/priorities, acceptance scenarios, success criteria, key constraints with rationale
   - **`<feature-dir>/spec.md`** (fallback — load only if spec-acceptance.md does not exist): extract user stories with priorities (P1, P2, P3), functional requirements (FR-NNN), success criteria

2. Plan context — load whichever exists (prefer the compact form):
   - **`<feature-dir>/plan-summary.md`** (preferred — file paths, migration steps, constraints + rationale, ~3KB): file scope table, migration steps, data model, key constraints
   - **`<feature-dir>/plan.md`** (fallback — load only if plan-summary.md does not exist): technology stack, architecture decisions, project structure, service changes

**Read Optional Files** (if they exist):
3. `<feature-dir>/data-model.md`:
   - Entities and migrations
   - Relationships

4. `<feature-dir>/contracts/`:
   - GraphQL schema or API contracts

5. `<feature-dir>/research.md`:
   - Technical decisions

**Output**: Complete understanding of what to build and how

---

### Step 2: Extract File Paths from Plan

**Parse plan.md "Project Structure" section**:

Example from plan:
```markdown
## Project Structure

src/
├── components/notifications/  - New components
├── hooks/                      - New hooks
├── services/                   - Business logic
```

**Extract paths for task descriptions**:
- Component directory: `src/components/notifications/`
- Hooks directory: `src/hooks/`
- Services directory: `src/services/`

**If paths not in plan**: Use generic placeholders
- `<component-dir>/ComponentName.tsx`
- `<service-dir>/ServiceName.ts`
- `<model-dir>/ModelName.py`

---

### Step 3: Identify Task Phases

**Standard Phase Structure**:

1. **Phase 1: Setup** (Shared Infrastructure)
2. **Phase 2: Foundation** (Blocking Prerequisites)
3. **Phase 3+: User Stories** (One phase per story, P1 → P2 → P3)
4. **Final Phase: Polish & Cross-Cutting**

---

### Step 4: Generate Tasks by Phase

**Task Format** (STRICT REQUIREMENT):
```markdown
- [ ] T001 [P?] [Story?] Description with exact file path
```

**Components**:
- `- [ ]` - Markdown checkbox (unchecked)
- `T001` - Sequential task ID
- `[P: path/to/file.ext]` - Optional parallel marker with **required** file scope (comma-separated repo-relative paths). A bare `[P]` without a file list is treated as unannotated (serial) by the runner.
- `[US1]` - Story label (for user story phases ONLY)
- Description - Clear action with file path from plan.md

---

#### Phase 1: Setup Tasks

**Purpose**: Initialize project infrastructure

**Example Tasks**:
```markdown
## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [ ] T001 Create feature branch per plan.md
- [ ] T002 Install dependencies listed in plan.md
- [ ] T003 [P] Configure linting/formatting tools
```

**Rules**:
- NO story labels
- Can have [P] markers for parallel work
- Keep minimal (only essential setup)

---

#### Phase 2: Foundation Tasks

**Purpose**: Blocking prerequisites for ALL user stories

**Map from plan.md**:
- If data-model.md exists → Create migration tasks
- If contracts/ exists → Create schema/type definition tasks
- Extract "Foundation" items from plan.md structure

**Example Tasks**:
```markdown
## Phase 2: Foundation (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story

⚠️ **CRITICAL**: No user story work can begin until this phase is complete

- [ ] T004 Create database migration from data-model.md
- [ ] T005 [P] Define types from data-model.md in <types-dir>/
- [ ] T006 [P] Create repository in <repo-dir>/ per plan.md
- [ ] T007 Add schema definitions from contracts/ to <schema-dir>/

**Checkpoint**: Foundation ready - user story implementation can now begin
```

**Rules**:
- NO story labels (shared foundation)
- Use [P] for tasks on different files
- Must complete before user stories
- Use paths from plan.md structure

---

#### Phase 3+: User Story Tasks

**One phase per user story, prioritized P1 → P2 → P3**

**Map from spec.md + plan.md**:
- User story title and goal from spec.md
- File paths from plan.md structure
- Components/services needed from plan architecture

**Structure**:
```markdown
## Phase 3: User Story 1 - [Title from spec] (Priority: P1) 🎯 MVP

**Goal**: [What this story delivers from spec]

**Independent Test**: [How to verify from spec acceptance criteria]

### Implementation for User Story 1

- [ ] T009 [P] [US1] Create [Component] in <path-from-plan>/ComponentName.ext
- [ ] T010 [P] [US1] Create [Component2] in <path-from-plan>/Component2.ext
- [ ] T011 [US1] Implement [Service] in <path-from-plan>/ServiceName.ext (depends on T009, T010)
- [ ] T012 [US1] Integrate [components] in <path-from-plan>/IntegrationPoint.ext

**Checkpoint**: User Story 1 should be fully functional and testable independently
```

**Rules**:
- ALL tasks MUST have [US1]/[US2]/[US3] story label
- Use [P] for tasks on different files with no dependencies
- Use exact paths from plan.md
- Order: Components → Services → Integration

---

#### Final Phase: Polish Tasks

**Purpose**: Cross-cutting improvements

**Example Tasks**:
```markdown
## Phase N: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T042 [P] Update project documentation
- [ ] T043 Run validation from quickstart.md
- [ ] T044 Performance optimization per success criteria
- [ ] T045 Security audit per constitution (if exists)
```

---

### Step 5: Generate Quality Checklists

**Auto-generate** in `<feature-dir>/checklists/`

**Checklist Generation Strategy**:
1. Load constitution (if exists)
2. Search for relevant requirements
3. Generate checklist items from found requirements
4. Reference constitution sections, don't duplicate content

---

#### requirements.md (Spec Quality)

**Always generate**:
```markdown
# Specification Quality Checklist

**Purpose**: Validate spec completeness before implementation
**Feature**: [Link to spec.md]

## Content Quality

- [ ] No implementation details in spec
- [ ] Focused on user value
- [ ] Written for non-technical stakeholders
- [ ] All mandatory sections completed

## Requirement Completeness

- [ ] No [NEEDS CLARIFICATION] markers remain
- [ ] Requirements testable and unambiguous
- [ ] Success criteria measurable
- [ ] All acceptance scenarios defined
- [ ] Edge cases identified
- [ ] Scope clearly bounded
```

---

#### implementation.md (Code Quality)

**Generate from constitution** (if exists):

1. **Search constitution for**:
   - Code quality requirements (search: "code quality", "TypeScript", "strict mode")
   - Logging requirements (search: "logging", "observability", "console")
   - URL construction (search: "URL", "hardcoded")
   - API patterns (search: "API", "GraphQL", "REST")

2. **Extract requirements** and create checklist items

3. **Reference sections**, don't copy content

**Example output**:
```markdown
# Implementation Quality Checklist

**Purpose**: Validate code quality during implementation
**Feature**: [Link to tasks.md]

[If constitution exists:]

## Code Quality (per constitution)

- [ ] [Requirement found in constitution, e.g., "Strict type checking enabled"]
  - Reference: Constitution § [section found by search]
- [ ] [Another requirement]
  - Reference: Constitution § [section]

## Logging (per constitution)

- [ ] Use project logging utilities (not console.*)
  - Reference: Constitution § [section on logging]
  - Find utilities: Search codebase for logger imports

## URL Construction (per constitution)

- [ ] No hardcoded service URLs
  - Reference: Constitution § [section on URLs]
  - Use URL builders from project

[If no constitution:]

## Code Quality (Best Practices)

- [ ] Consistent with existing code style
- [ ] Functions have clear, single purpose
- [ ] Error handling implemented
- [ ] No hardcoded values
```

---

#### testing.md (Test Quality)

**Generate from constitution** (if exists):

1. **Search for testing requirements**:
   - Test coverage (search: "coverage", "testing", "test")
   - Test quality (search: "test quality", "test structure")
   - Pre-commit (search: "pre-commit", "commit requirements")

2. **Create checklist from findings**

**Example output**:
```markdown
# Testing Quality Checklist

**Purpose**: Validate test coverage and quality
**Feature**: [Link to tasks.md]

[If constitution has pre-commit requirements:]

## Pre-Commit Requirements (per constitution § X)

- [ ] All tests pass
  - Command: [Extract from constitution or detect from project]
- [ ] Builds succeed
  - Command: [Extract or detect]
- [ ] [Other requirements from constitution]

[If constitution has coverage requirements:]

## Test Coverage (per constitution § X)

- [ ] Coverage ≥ [threshold from constitution]
- [ ] [Other coverage requirements]

[If no constitution:]

## Testing (Best Practices)

- [ ] All tests pass before commit
- [ ] New functionality has tests
- [ ] Edge cases covered
- [ ] Tests are maintainable
```

---

### Step 6: Constitution Validation

**If constitution exists:**

1. Load constitution file
2. Validate task format follows any specified standards
3. Check if tasks address constitutional requirements
4. Ensure checklists reference constitution properly

**Output**:
- **PASS**: Tasks well-formed, checklists reference constitution
- **WARN**: Missing task types that constitution requires
- **FAIL**: Task format violations

**If no constitution**: Skip validation

---

### Step 7: Generate tasks.md

**File**: `<feature-dir>/tasks.md`

**Complete Structure**:
```markdown
# Tasks: [FEATURE NAME]

**Prerequisites**: plan.md, spec.md [, data-model.md] [, contracts/]

## Format: `[ID] [P: file]? [Story]? Description`

- **[P: repo/relative/file.ext]**: Can run in parallel — file list is **required** (comma-separated). Bare `[P]` without a file list is treated as serial by the runner.
- **[Story]**: User story (US1, US2, US3)
- Include exact file paths from plan.md

---

## Phase 1: Setup
[Tasks from Step 4]

---

## Phase 2: Foundation
[Tasks from Step 4]
**Checkpoint**: Foundation ready

---

## Phase 3: User Story 1 - [Title] (Priority: P1) 🎯 MVP
[Tasks from Step 4]
**Checkpoint**: US1 complete

---

[Additional user story phases...]

---

## Phase N: Polish & Cross-Cutting Concerns
[Tasks from Step 4]

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundation (Phase 2)**: Depends on Setup - BLOCKS all user stories
- **User Stories (Phase 3+)**: Depend on Foundation
  - Can proceed in parallel (if staffed)
  - Or sequentially by priority (P1 → P2 → P3)
- **Polish (Final)**: Depends on desired user stories complete

### User Story Dependencies

- **User Story 1 (P1)**: Independent after Foundation
- **User Story 2 (P2)**: Independent after Foundation
- **User Story 3 (P3)**: Independent after Foundation

### Parallel Opportunities

- Tasks marked [P] can run in parallel within each phase
- User stories can be worked on in parallel by different developers
```

---

### Step 8: Report Completion

**Output Message**:
```
✓ Task breakdown created: <feature-dir>/

Generated Files:
- tasks.md ([N] tasks across [M] phases)
  - Phase 1: Setup ([N] tasks)
  - Phase 2: Foundation ([N] tasks) - BLOCKS user stories
  - Phase 3: User Story 1 - MVP ([N] tasks)
  [...]

- checklists/requirements.md ([N] items)
- checklists/implementation.md ([N] items, references constitution)
- checklists/testing.md ([N] items, references constitution)

Task Statistics:
- Total: [N] tasks
- Parallel opportunities: [N] tasks marked [P]
- User Story 1 (MVP): [N] tasks

Constitution Check: [PASS/WARN/SKIPPED]

Tasks complete — proceeding to implementation.
```

**Handoff Instructions**:
- Report task breakdown completion in one sentence.
- **Auto-continue into `/feature-implement <slug>` (ValueRank) or the `feature-implement` skill (other repos) immediately** unless one of the following is true:
  - Constitution check returned **FAIL**
  - The user explicitly asked to stop after tasks
  - The repo state makes proceeding unsafe
- Do not wait for the user to say "continue" or "start implementation."
- DO NOT suggest using `/speckit.implement` or any other speckit command.

---

## Task Quality Rules

### Required Task Elements

Every task MUST have:
1. ✅ Checkbox: `- [ ]`
2. ✅ Sequential ID: `T001`, `T002`, etc.
3. ✅ Story label: `[US1]` (for user story phases ONLY)
4. ✅ Parallel marker: `[P]` (if applicable)
5. ✅ Description: Action + exact file path from plan.md

### Parallel Marker [P] Rules

**Format**: `[P: repo/relative/file.ext]` or `[P: file1.ts, path/to/file2.ts]`

The file list is **required** whenever you mark a task `[P]`. A bare `[P]` without a file list is treated as unannotated (serial) by the `run_factory.py implement` runner. The runner uses the file list for overlap detection — tasks whose file sets are disjoint can run in parallel; tasks whose file sets overlap are forced serial with a warning.

**Correct format example**:
```
- [ ] T005 [P: src/services/foo.ts] [US1] Add FooService in src/services/foo.ts
- [ ] T006 [P: src/components/Bar.tsx] [US1] Add Bar component in src/components/Bar.tsx
```

Use `[P: ...]` when:
- ✅ Different file than previous task
- ✅ No dependency on incomplete tasks
- ✅ Can run simultaneously with other [P] tasks
- ✅ You can list all files the task will modify

Do NOT use `[P]` when:
- ❌ Same file as another [P] task in the same slice (use serial instead)
- ❌ Depends on previous task output
- ❌ Must run in specific order
- ❌ You cannot enumerate the files in advance (omit [P] entirely — it will run serially)

---

## Reference

For output format examples (TypeScript React, Python Django, Rust microservice) and error message templates (missing prerequisites, invalid user stories, path extraction failures), see `feature-tasks-reference.md` in this directory. Load it on demand when you need to match a specific scenario or diagnose an error — it is not needed on every invocation.

---

## Quality Guidelines

### What Makes Good Tasks

✅ **Specific**: Clear action with exact file path
✅ **Testable**: Can verify completion
✅ **Scoped**: One logical change per task
✅ **Organized**: Grouped by user story
✅ **Independent**: Stories testable standalone

### What to Avoid

❌ **Vague**: "Implement notifications" (too broad)
❌ **No paths**: "Create component" (which file?)
❌ **Hardcoded paths**: Using 206mp paths instead of plan.md paths
❌ **Dependent stories**: US2 cannot be tested without US1
❌ **Giant tasks**: Single task doing 5 things

---

## Configuration

Defaults:

- **Auto-generate checklists**: Yes (3 files always created)
- **Constitution validation**: Enabled if constitution found
- **Task format enforcement**: Strict (checkbox, ID, story label, path)
- **User story organization**: Required (tasks grouped by story)
- **Reference constitution**: Yes (don't duplicate content)

---

## Next Skill in Workflow

**`feature-implement`** - Execute tasks and track progress
- Input: Reads tasks.md, plan-summary.md (preferred) or plan.md, spec-acceptance.md (preferred) or spec.md, checklists/
- Output: Implemented code + updated tasks.md with [X] checkboxes
- Purpose: Build the feature phase by phase

---

## Notes

- This skill generates tasks.md + checklists/
- All tasks follow strict checkbox format
- Constitution validation runs if constitution exists
- Checklists **reference** constitution, don't duplicate it
- User stories organized independently (MVP-first delivery)
- File paths come from plan.md, not hardcoded
- Works with any language/framework
