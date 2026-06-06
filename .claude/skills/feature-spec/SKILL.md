---
name: feature-spec
description: Create a high-quality feature specification with prioritized user stories, requirements, and constitution validation. Generates specs/NNN-feature-name/spec.md with clear acceptance criteria. Works with any project that has a constitution file.
---

# Feature Specification Skill

## ⚠️ Keep Moving Between Phases

Do **not** pause between phases of the feature workflow (spec → plan → tasks → implement) unless one of these is true:
- A runner or checkpoint command returns `mark_blocked`
- The user explicitly asked to stop at a specific point
- Discovery questions have unresolved items that require the user's answer
- The repo state makes the next action unsafe

When the spec is complete, auto-continue into `feature-plan` without waiting for the user to say "continue." Report a one-sentence status and proceed.

---

## ⚠️ THIS REPO: Use the repo-owned Feature Factory instead

hoard-hurt-help (`chrislawcodes/hoard-hurt-help`) has a repo-owned Feature Factory engine. **Do not follow the generic skill below** — it references `.specify/` and constitution files that do not exist in this repo. Drive the workflow through the runner instead.

Use the repo-specific factory workflow instead:
- Entry point: `docs/workflow/operations/codex-skills/feature-factory/SKILL.md`
- Codex guide: `docs/workflow/operations/codex-skills/feature-factory/CODEX-ORCHESTRATOR.md`
- Runner: `python3 docs/workflow/operations/codex-skills/feature-factory/scripts/run_factory.py`

---

You help developers create high-quality feature specifications using a structured, constitution-compliant process. This skill generates a complete spec.md file in a numbered feature directory.

## ⚠️ IMPORTANT: Speckit Replacement

**This skill REPLACES the speckit workflow.** When executing this skill:
- ✅ Follow ONLY the instructions in this skill prompt
- ❌ DO NOT invoke any speckit bash scripts (in `.specify/scripts/`)
- ❌ DO NOT suggest speckit slash commands (`/speckit.*`)
- ❌ DO NOT reference speckit commands in your responses

This is a complete, standalone workflow for feature specification.

## What This Skill Does

Creates a feature specification document that:
- Defines WHAT users need and WHY (not HOW to implement)
- Organizes requirements into prioritized user stories (P1, P2, P3)
- Provides testable acceptance criteria for each story
- Validates against project constitution (if exists)
- Asks clarifying questions (max 3) for critical unknowns only
- Generates a specification ready for technical planning

## When to Use This Skill

- Starting a new feature from a natural language description
- Converting a user request into a structured specification
- Before any technical planning or implementation begins
- When you need to clarify requirements with stakeholders

## Prerequisites

- Working in the repository root directory
- Git repository initialized (for feature branch creation)
- Optional: Project constitution file (`.specify/memory/constitution.md` or similar)
- Optional: CLAUDE.md or equivalent project documentation

## Workflow

### Step 1: Parse Feature Description

**Input**: User provides natural language feature description (from skill invocation)

**Actions**:
1. Extract key concepts: actors, actions, data, constraints
2. Identify primary user value proposition
3. Determine scope boundaries (what's included/excluded)
4. List assumptions you're making about unspecified details

**Output**: Mental model of the feature requirements

---

### Step 2: Assign Feature Number and Create Directory

**Actions**:
1. Scan for existing specs directory (common locations: `specs/`, `features/`, `docs/features/`)
2. Find highest feature number: List directories matching pattern `NNN-*` where NNN is numeric
3. Assign next sequential number (NNN format, e.g., 074)
4. Generate short-name (2-4 words, kebab-case):
   - Use action-noun format: "add-user-auth", "fix-payment-timeout"
   - Preserve technical terms: "oauth2-integration", "graphql-migration"
   - Keep concise but descriptive
5. Create directory: `<specs-dir>/NNN-short-name/`

**Example**:
- Input: "Add email notifications for critique responses"
- Specs location: `specs/` (detected)
- Highest existing: `specs/073-job-queue-inspector/`
- Output: `specs/074-critique-notifications/`

**If no specs directory exists**: Ask user where to create feature directories or use `specs/` as default

---

### Step 3: Generate Prioritized User Stories

**Structure**: Each user story must be:
- **Independently testable**: Can validate without other stories complete
- **Value-focused**: Delivers specific user/business value
- **Prioritized**: P1 (critical) → P2 (important) → P3 (nice-to-have)

**User Story Format**:
```markdown
### User Story N - [Brief Title] (Priority: PN)

[Describe user journey in plain language: As a [role], I need to [action] so that [value]]

**Why this priority**: [Explain value and priority level justification]

**Independent Test**: [How to test this story standalone and verify it delivers value]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]
2. **Given** [initial state], **When** [action], **Then** [expected outcome]
```

**Quality Rules**:
- P1 stories: Core functionality, system unusable without them
- P2 stories: Important but system functions without them
- P3 stories: Enhancements, nice-to-haves
- Each story should deliver value independently
- Stories should be testable in isolation

---

### Step 4: Identify Clarification Needs

**Goal**: Ask ONLY critical questions that significantly impact scope/design

**Rules**:
- **Maximum 3 questions** total across entire spec
- Make informed guesses for everything else (document as assumptions)
- Only ask when:
  - Choice significantly impacts feature scope or UX
  - Multiple reasonable interpretations with different implications
  - No reasonable default exists

**Question Format**:
```markdown
## Clarification Needed

### Question 1: [Topic]

**Context**: [Quote relevant requirement or user story]

**What we need to know**: [Specific question]

**Suggested Options**:

| Option | Answer | Implications |
|--------|--------|--------------|
| A | [First option] | [What this means for feature] |
| B | [Second option] | [What this means for feature] |
| C | [Third option] | [What this means for feature] |
| Custom | Your own answer | Provide details below |

**Your choice**: _[Wait for user response]_
```

**After receiving answers**:
- Update spec with user's choices
- Remove all `[NEEDS CLARIFICATION]` markers
- Document chosen option in spec

---

### Step 5: Define Functional Requirements

**Format**: Sequential FR-NNN identifiers with testable statements

```markdown
### Functional Requirements

- **FR-001**: System MUST [specific capability with measurable outcome]
- **FR-002**: Users MUST be able to [key interaction with success criteria]
- **FR-003**: System MUST [data requirement with validation rules]
```

**Quality Rules**:
- Every requirement must be testable (not subjective)
- Use "MUST" for required, "SHOULD" for recommended, "MAY" for optional
- Be specific about quantities, limits, timeouts
- Reference user stories they support (e.g., "Supports US1")

**Examples**:
- ✅ Good: "System MUST send notification within 5 seconds of critique response"
- ❌ Bad: "System should notify users quickly" (not measurable)

---

### Step 6: Create Success Criteria

**Goal**: Define measurable, technology-agnostic outcomes

**Rules**:
- Must be measurable (include metrics: time, percentage, count)
- Technology-agnostic (no frameworks, tools, implementation details)
- User-focused (outcomes from user/business perspective)
- Verifiable without knowing implementation

**Format**:
```markdown
### Success Criteria

- **SC-001**: [Measurable outcome, e.g., "Users can complete task in under 2 minutes"]
- **SC-002**: [Performance metric, e.g., "System handles 1000 concurrent users"]
- **SC-003**: [User satisfaction, e.g., "90% task completion rate on first attempt"]
```

**Examples**:
- ✅ Good: "Users see search results in under 1 second"
- ❌ Bad: "API response time < 200ms" (implementation detail)
- ✅ Good: "95% of uploads complete without errors"
- ❌ Bad: "Use S3 multipart upload for reliability" (technology-specific)

---

### Step 7: Identify Edge Cases

**Purpose**: Document boundary conditions and error scenarios

```markdown
### Edge Cases

- What happens when [boundary condition]? → [Expected behavior]
- How does system handle [error scenario]? → [Graceful degradation]
- What if user [unexpected action]? → [Validation or fallback]
```

**Common patterns**:
- Empty states (no data exists)
- Maximum limits (too many items)
- Missing/null data
- Permission boundaries
- Network failures
- Concurrent operations

---

### Step 8: Constitution Validation

**Locate Constitution** (check these locations in order):
1. `.specify/memory/constitution.md`
2. `CONSTITUTION.md` (project root)
3. `docs/constitution.md`
4. Search for files containing "constitution" or "governance"

**If constitution exists**, validate spec against it:

1. **Scan constitution for relevant sections**:
   - Search for: "User Experience", "UX", "Design Principles"
   - Search for: "Performance", "Response Time", "Optimization"
   - Search for: "Security", "Authentication", "Data Protection"
   - Search for: "API", "Service Design", "Architecture"

2. **Extract requirements** from found sections:
   - Note any MUST/SHOULD requirements that apply to this feature
   - Note performance targets (e.g., "API < 200ms", "FCP < 1.5s")
   - Note security requirements (e.g., "all endpoints require auth")
   - Note accessibility standards (e.g., "WCAG 2.1 Level AA")

3. **Validate spec addresses requirements**:
   - Does spec define authentication if required?
   - Does spec include performance targets from success criteria?
   - Does spec address accessibility if mentioned in constitution?
   - Does spec consider security for sensitive operations?

4. **Output validation result**:
   - **PASS**: Spec addresses all constitutional requirements
   - **WARN**: Spec may need to address specific constitutional section
   - **FAIL**: Spec violates constitutional requirement (must resolve)

**If no constitution exists**: Skip validation, proceed to Step 9

---

### Step 9: Generate spec.md

**File**: `<specs-dir>/NNN-short-name/spec.md`

**Sections** (in order):
1. **Header**: Feature branch, created date, status, input description
2. **User Scenarios & Testing**: Prioritized user stories (P1 → P2 → P3)
3. **Edge Cases**: Boundary conditions and error scenarios
4. **Requirements**: Functional requirements (FR-NNN)
5. **Success Criteria**: Measurable outcomes (SC-NNN)
6. **Key Entities** (optional): If feature involves data models
7. **Assumptions** (optional): Document informed guesses made

**Quality Checklist** (verify before writing):
- [ ] No implementation details (languages, frameworks, APIs)
- [ ] All requirements testable and unambiguous
- [ ] Success criteria measurable and technology-agnostic
- [ ] User stories prioritized and independently testable
- [ ] Maximum 3 clarification questions (or all resolved)
- [ ] Edge cases identified
- [ ] Constitution compliance validated (if constitution exists)

---

### Step 10: Report Completion

**Output Message**:
```
✓ Feature specification created: specs/074-critique-notifications/spec.md

Summary:
- Feature #074: critique-notifications
- User Stories: 3 (P1: 1, P2: 1, P3: 1)
- Functional Requirements: 8
- Success Criteria: 4
- Constitution Check: PASS (or SKIPPED if no constitution)

Spec complete — proceeding to technical planning.
```

**Handoff Instructions**:
- Report spec completion in one sentence.
- **Auto-continue into the `feature-plan` skill immediately** unless one of the following is true:
  - Constitution check returned **FAIL**
  - The user explicitly asked to stop after the spec
  - Discovery questions left unresolved items that require the user's answer before planning
- Do not wait for the user to say "continue" or "use feature-plan skill."
- DO NOT suggest using `/speckit.plan` or any other speckit command.

---

## Constitution Integration

**If constitution exists**, this skill validates specifications against project governance:

**Common constitution sections to check**:
- Code quality principles (type safety, clarity, organization)
- User experience standards (design principles, accessibility, performance)
- Performance requirements (response times, optimization targets)
- Security principles (authentication, data protection, validation)
- API design patterns (RESTful conventions, service communication)

**How validation works**:
1. Load constitution file
2. Search for relevant sections based on feature type
3. Extract MUST/SHOULD requirements
4. Check if spec addresses each requirement
5. Flag violations or missing requirements

**Violations flagged with severity**:
- **CRITICAL**: Spec violates hard requirements (security, performance mandates)
- **WARNING**: Spec may violate best practices (ask user to confirm)
- **INFO**: Spec should document how it addresses constitutional requirement

---

## Examples

### Example 1: Simple Feature

**Input**: "Add a dark mode toggle to user settings"

**Output**:
- Feature #075: `specs/075-dark-mode-toggle/spec.md`
- User Stories: 2 (P1: Toggle in settings, P2: Persist preference)
- FR-001 through FR-005
- SC-001: Users can switch themes in under 5 seconds
- Constitution: PASS (accessibility verified, performance non-blocking)

### Example 2: Complex Feature with Clarification

**Input**: "Add real-time collaboration for collection editing"

**Clarification Asked**:
- Q1: Collaboration scope - same-time editing or comment-based review?
- Q2: Conflict resolution - last-write-wins or manual merge?

**After User Response**:
- Feature #076: `specs/076-collection-collaboration/spec.md`
- User Stories: 4 (P1: Multi-user viewing, P2: Commenting, P3: Live cursors, P3: Conflict UI)
- Constitution: WARNING (performance targets for WebSocket connections needed)

### Example 3: Security-Sensitive Feature

**Input**: "Add admin user impersonation for support debugging"

**Output**:
- Feature #077: `specs/077-admin-impersonation/spec.md`
- User Stories: 2 (P1: Login-as, P2: Audit logging)
- Constitution: CRITICAL - Must implement audit logging per security requirements
- Spec includes FR-008: All impersonation logged with admin ID, target user, timestamp
- Spec includes FR-009: Cannot impersonate other admins (escalation prevention)

---

## Error Handling

### No Feature Description Provided

```
ERROR: No feature description provided.

Please invoke this skill with a description of what you want to build.

Example: "Add email notifications when users receive critique responses"
```

### Cannot Determine User Stories

```
ERROR: Cannot determine user scenarios from description.

The feature description is too vague to extract concrete user stories.

Please provide more details about:
- Who will use this feature? (user roles)
- What actions will they perform?
- What value does this deliver?

Example: Instead of "improve UX", say "add keyboard shortcuts for photo navigation"
```

### Constitution Violations

```
WARNING: Potential constitution violations detected

Section found: "Authentication & Authorization"
Requirement: "All API endpoints require authentication (except public portfolios)"

Issue: Spec does not specify authentication requirements

Recommendation: Add FR-NNN: "System MUST require authentication for all endpoints"

Proceed anyway? (yes/no)
```

---

## Quality Guidelines

### What Makes a Good Spec

✅ **Clear user value**: Each story explains WHY it matters
✅ **Independently testable**: Stories can be validated standalone
✅ **Measurable outcomes**: Success criteria have numbers
✅ **Technology-agnostic**: No mention of specific frameworks or tools
✅ **Scoped appropriately**: P1 is MVP, P2/P3 are enhancements
✅ **Edge cases covered**: Boundary conditions documented

### What to Avoid

❌ **Implementation details**: "Use Redux for state management"
❌ **Vague requirements**: "System should be fast"
❌ **Untestable criteria**: "Users will like the interface"
❌ **Technology-specific**: "GraphQL mutation response time < 100ms"
❌ **Too many priorities**: Everything marked P1
❌ **Dependent stories**: P1 cannot be tested without P2

---

## Configuration

This skill uses these defaults:

- **Max clarification questions**: 3
- **User story format**: Given-When-Then acceptance scenarios
- **Requirement format**: FR-NNN with MUST/SHOULD/MAY
- **Constitution validation**: Enabled if constitution file found
- **Auto-directory creation**: Yes
- **Feature numbering**: Sequential from existing specs directory
- **Specs directory**: Auto-detect or use `specs/` as default

---

## Next Skill in Workflow

After `feature-spec` completes successfully, the next skill is:

**`feature-plan`** - Generate technical implementation plan
- Input: Reads the spec.md you just created
- Output: plan.md, data-model.md (if entities), contracts/ (if APIs)
- Purpose: Convert WHAT (spec) into HOW (technical approach)

---

## Notes

- This skill creates **only spec.md** (progressive file generation)
- Constitution validation runs if constitution file exists
- Clarification questions asked inline, no separate command needed
- Feature numbers auto-assigned sequentially
- Short-names generated from feature description (user can override)
- Works with any project structure (auto-detects specs directory)
