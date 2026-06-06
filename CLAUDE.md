# Hoard Hurt Help — Project Constitution

This file is the shared working contract for Claude, Codex, and any other agent that works in this repo.

Hoard Hurt Help is a multiplayer Prisoner's Dilemma game where LLM agents compete against each other.

## Communication Style

- Use plain, direct language at a high-school reading level.
- Use short sentences. Explain jargon if you need it.
- Start with a short summary, then details.
- When there are real options, use a table and give a recommendation with a reason.
- Be honest about risk, uncertainty, or disagreement.

## Clarifying Questions

- If you need clarifying questions, decide the full set first.
- Say how many questions you have before asking the first one.
- Ask them one at a time.

## Never Do

- Push commits directly to `main`.
- Merge a PR unless Chris directly asks.
- Suppress errors to make checks pass (`# type: ignore`, `# noqa`, swallowed exceptions).
- Commit secrets or credentials.

## Delivery Paths

- Chris picks the delivery path: **Direct Path** (just do it), **Feature Factory** (spec → plan → tasks → implement), or **Experiment Workflow** (A/B test two approaches).
- If the path is unclear, ask before starting.
- Do not switch paths mid-feature unless Chris asks.
- One feature per branch. Do not stack new work on top of an open feature PR unless Chris asks.

## PR And Push Rules

- All changes to `main` go through a feature branch + PR against `chrislawcodes/hoard-hurt-help`.
- For invoked delivery actions (`/ship`, "push and open the PR"): the invocation is the consent — push and open the PR without re-prompting.
- For ad-hoc work where no explicit delivery instruction was given: ask before `git push`.
- Run the Preflight Gate before any `git push` or PR creation.
- Every PR must include a `Validation` section listing exact commands run and pass/fail results.
- To merge a PR, invoke `/ship` — not bare `gh pr merge`. `/ship` rebases onto main, runs preflight, watches CI, and squash-merges.

## Preflight Gate

Run from the repo root before any `git push` or PR creation:

```bash
cd $(git rev-parse --show-toplevel)
python3 -m ruff check . && \
mypy app/ mcp_server/ && \
pytest -q
```

Hard rules:
- Do not push if any preflight command fails.
- Fix the root cause. Do not use suppressions to silence errors.
- If unrelated code breaks checks, validate in a clean worktree from `origin/main` before pushing.

## Python Standards

### No Suppressions

Never use `# type: ignore` or `# noqa` to silence a real error. Fix the root cause. The only exception is a known upstream bug in a third-party library — document it with a comment explaining the specific issue.

### Type Annotations

All function signatures must have type annotations. Use `from __future__ import annotations` at the top of files that need forward references.

### No Bare `except`

Always catch a specific exception type. `except Exception` is acceptable at the top of a route or task; bare `except:` is not.

### Async Consistency

This is an async app. Route handlers and DB calls must be `async def`. Do not mix sync DB calls into async paths.

## Testing Requirements

- Test business logic and data transformations.
- Mock external API calls (Claude, Hermes). Do not mock the database in integration tests — use the test DB.
- Always write tests for new game logic in `app/engine/`.
- The test DB is SQLite in-memory. Do not require a live Postgres instance for `pytest`.

## File Structure

- Keep files focused. If a file is doing more than one thing, split it by responsibility with a domain-meaningful name.
- No vague filenames like `utils.py` or `helpers.py`.
- App code lives in `app/`. MCP server code lives in `mcp_server/`. Do not mix them.

## When Something Breaks

Diagnose before fixing. Find the smallest reproducing case. Fix the root cause. Do not retry blindly or change multiple things at once.

## Project Status

- Update `STATUS.md` (if it exists) when a meaningful task is complete.
- Mark work done and note what is now unblocked.

## Parallel Agents — One Worktree Each

Multiple agent sessions (Claude, Codex, Gemini) must never edit the same working
directory at once. Concurrent edits clobber each other — a file flips between
half-finished states between commands, and one session's work gets swept into
another's commit.

Before starting work that another agent might also be doing, give yourself an
isolated git worktree:

```bash
scripts/agent-worktree.sh new <branch-name>   # fresh worktree off origin/main
scripts/agent-worktree.sh list                # show all worktrees
scripts/agent-worktree.sh rm <branch-name>    # remove it + delete the branch after merge
```

Work, commit, push, and open the PR from inside that worktree. Tear it down once
the PR is squash-merged.

## Read First

Always read:
- This file (`CLAUDE.md`) for coding standards and preflight
- `docs/platform/AGENT_LUDUM_DESIGN.md` + `AGENT_LUDUM_ARCHITECTURE.md` for the platform's design & architecture
- `docs/games/<game>/` (e.g. `hoard-hurt-help/`) for that game's design & architecture

Read when relevant:
- `specs/` for feature specs
- `MEMORY.md` for persistent project references
