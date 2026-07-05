# Templates

Skeletons for Step 3 (CLAUDE.md) and Step 4 (companions). `{{...}}` slots are
filled from detection/answers; `<<pile1:...>>` means paste that section
verbatim from `portable-principles.md`. Omit whole sections where the skill
says they don't apply — a short honest constitution beats a long aspirational
one.

---

## CLAUDE.md skeleton

```markdown
# {{Project Name}} — Project Constitution

This file is the shared working contract for Claude, Codex, and any other
agent that works in this repo.

{{One-sentence description of what the project is.}}

## Communication Style
<<pile1:Communication Style>>

## Clarifying Questions
<<pile1:Clarifying Questions>>

## Never Do
<<pile1:Never Do — with the suppression examples named for THIS stack,
e.g. Python: `# type: ignore`, `# noqa`; TS: `@ts-ignore`, `eslint-disable`;
Rust: `#[allow(...)]` on real errors>>

## PR And Push Rules
<<pile1:PR And Push Rules>>

## Preflight Gate

Run from the repo root before any `git push` or PR creation:

    {{real command 1: lint}}
    {{real command 2: type/compile check — include whenever the stack has one
      (`tsc --noEmit`, `mypy`, `cargo check`); omit only when the test command
      already compiles everything}}
    {{real command 3: tests}}

Hard rules:
- Do not push if any preflight command fails.
- Fix the root cause. Do not use suppressions to silence errors.
- If unrelated code breaks checks, validate in a clean worktree from
  `origin/main` before pushing.

{{IF full gate is slow AND repo is production-rigor: Small-Change Lane —
all-of: ≤{{N}} lines, ≤{{M}} files, no migration/schema change, no new
dependency, not a new subsystem → fast lane: {{fast test command}}.
CI still runs the full suite; the fast lane is local signal only.}}

## {{Language}} Standards
<<pile1:No Suppressions>>
<<pile1:Fail Loud — No Swallowed Errors>>
{{2-4 stack-specific bullets from Pile 2 "Language standards":
typed signatures, no bare catch-alls, async consistency if async app.}}

## Testing Requirements
{{From Pile 2: what to always test, what to mock, what never to mock,
what the test DB/harness is. 3-5 bullets max, all true today.}}

## File Structure
<<pile1:File Structure>>
{{One line on this repo's actual layout, e.g. "App code lives in src/."}}

## When Something Breaks
<<pile1:When Something Breaks>>
{{IF prod: point at docs/operations/debugging-history.md — read it when
something is broken in prod; append an entry after any non-trivial fix.}}

## How We Work — Branches{{IF multi-agent: , Worktrees}}, Prune On Merge
<<pile1:Prune On Merge>>
{{IF multi-agent: worktree-per-task rule + the three
scripts/agent-worktree.sh commands (new / list / rm), and: create the
worktree at the moment of first change, never after the first edit.}}

## Project Status
<<pile1:Ledger Habit — first bullet always; the debugging-history bullet
only IF prod>>


## Read First
{{Only docs that exist today. Minimum: this file.}}
```

---

## Companion: `.claude/skills/preflight/SKILL.md`

Copy hoard-hurt-help's preflight skill structure (lane check → run commands
separately → verdict table → unrelated-breakage worktree check), substituting
the real commands. Two variants:

- **Full** (production rigor, slow gate): keep the lane check with this repo's
  thresholds and the worktree-based unrelated-breakage step (via
  `agent-worktree.sh` if multi-agent, plain `git worktree add` if solo).
- **Solo/no-lane** (prototype, or gate under ~30s): DELETE the lane step
  entirely (Step 2 runs everything, always) and use plain
  `git worktree add ../<repo>-baseline main` for the baseline check
  (`main`, not `origin/main`, if there's no remote).

Either way the verdict table doubles as the PR's Validation section — keep
that line.

## Companion: `.claude/skills/ship/SKILL.md`

Minimal version — the steps, each of which must pass before the next:

1. Rebase the PR branch onto `origin/main`; re-run preflight after rebase.
2. Push; watch CI to green (do not merge on yellow).
3. Squash-merge with a clean one-line title.
4. Prune: delete the branch{{IF multi-agent: , remove the worktree}}; return
   the main checkout to fresh `main`.

Invoking /ship is consent to push and merge — do not re-prompt.

## Companion: `docs/operations/debugging-history.md` (prod only)

```markdown
# Debugging History

Past incidents, root causes, and how they were found. Append an entry after
any non-trivial production debugging session — at the moment of discovery.

Entry format: ### {{date}} — {{one-line symptom}}
Symptom → diagnosis (queries/commands used) → root cause → fix (SHA/PR) →
prevention. Write for the next agent who sees the same symptom.

---

(no entries yet)
```

## Companion: `STATUS.md`

```markdown
# Status

## In Progress
- (nothing)

## Recently Shipped
- {{date}} — Constitution bootstrap (this commit).

## Blocked / Open Questions
- (nothing)
```

## Companion: `scripts/agent-worktree.sh` (multi-agent only)

Copy verbatim from hoard-hurt-help `scripts/agent-worktree.sh` — it is fully
generic (derives repo name/paths from git; zero project references).
`chmod +x` it.
