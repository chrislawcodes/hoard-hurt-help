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

- Chris picks the delivery path: **Direct Path** (just do it), **Feature Factory** (spec → plan → tasks → implement), or **Experiment Workflow** (A/B test two approaches — run the `experiment` skill in `.claude/skills/experiment/`; results log to `experiments.md`).
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

## PR Watching

- After opening a PR, do not watch it on a timer. Report the PR link and stop — opening the PR is the end of the task.
- Never schedule recurring self check-ins (`send_later` / `ScheduleWakeup`) to re-read a PR "in case something changed". Each wakeup is a full uncached turn that usually finds nothing, and it is the main source of wasted tokens here.
- Passive webhook events (`<github-webhook-activity>`: review comments, CI failures, merges) still arrive on their own and can be acted on when they fire — they cost nothing until a real event happens. The thing to avoid is *timed polling* of a PR with no pending event; do not unsubscribe reflexively, just don't poll.
- Enter a watch/poll loop **only** when Chris asks for a terminal delivery action — `/ship`, "squash-merge when it's ready", "merge when green", or "babysit this PR". Then watch until the merge/close completes and prune. Do not proactively offer to babysit a PR.

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

### Small-Change Lane (Direct Path only)

Small changes do not need the full ritual. A change is "small" when **all** of
these hold: ≤40 lines changed, ≤5 files touched, no DB migration, no model or
schema change, no new dependency, and not a new subsystem.

For a small change, the local gate is the **fast test lane** plus lint and types:

```bash
cd $(git rev-parse --show-toplevel)
python3 -m ruff check . && \
mypy app/ mcp_server/ && \
pytest -q -m "not integration"   # fast lane (~13s); CI still runs the full suite
```

Also for a small change:
- **Skip** the spec / plan / tasks docs and the `STATUS.md` update.
- **Keep** the worktree-per-task rule and the PR's `Validation` section.

CI runs the full `pytest` suite on every PR, so the full suite is still the real
gate — the fast lane is just quicker local signal while you iterate.

Anything that is **not** small (any migration, model change, or cross-cutting
feature) runs the full Preflight Gate above and the normal delivery path.

## Python Standards

### No Suppressions

Never use `# type: ignore` or `# noqa` to silence a real error. Fix the root cause. The only exception is a known upstream bug in a third-party library — document it with a comment explaining the specific issue.

### Type Annotations

All function signatures must have type annotations. Use `from __future__ import annotations` at the top of files that need forward references.

### No Bare `except`

Always catch a specific exception type. `except Exception` is acceptable at the top of a route or task; bare `except:` is not.

### Fail Loud — No Swallowed Errors

Surface failures; never hide them. Do not catch an exception only to return a
default, `None`, an empty value, or a fake success — re-raise it or propagate it
so the caller sees the real failure. Do not `except: pass` or `except: continue`
(the Preflight Gate's `ruff` rules S110/S112 reject these). Always check the
return code / stderr of a subprocess or shell command; a non-zero exit, missing
file, or empty output is a failure, not a quiet success. The only acceptable
silent catch is a deliberate, non-gating advisory path (e.g. an optional status
banner) — and it must say so in a comment (`# fail-open: advisory only`).

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

## How We Work — Worktrees, Clean Main, Prune On Merge

The goal: the main checkout always sits on a fresh `main`, every task gets its own
isolated worktree, and branches never pile up. Skipping the prune step is what
silently rots the repo — dozens of merged and abandoned branches accumulate until
sessions start landing on stale branches with bad assumptions.

### Keep the main checkout pristine

The primary repo folder (`hoard-hurt-help/`) stays on `main`, always fast-forwarded
to `origin/main`. Treat it as read-only: explore, read, and answer questions here
freely. Create your worktree at the moment you are about to make your first change —
not at session start (a worktree per question just breeds new clutter), and never
after the first edit (writing in `main` is the bug we are preventing). It is the
trunk — branches grow *off* it, not *in* it. If you find it parked on a feature
branch, that is the bug: return it to `main` first.

### One worktree per task

Multiple agent sessions (Claude, Codex, Gemini) must never edit the same working
directory at once. Concurrent edits clobber each other — a file flips between
half-finished states between commands, and one session's work gets swept into
another's commit. Give every task its own isolated worktree, branched fresh off
`origin/main` (never reuse an old branch as a starting point):

```bash
scripts/agent-worktree.sh new <branch-name>   # fresh worktree off origin/main
scripts/agent-worktree.sh list                # show all worktrees
scripts/agent-worktree.sh rm <branch-name>    # remove worktree + delete branch after merge
```

Work, commit, push, and open the PR from inside that worktree.

### Rebase each session

If work spans more than one sitting, sync before continuing so you never build on a
stale base:

```bash
git fetch origin main && git rebase origin/main
```

### Prune the moment it's done — this is the rule that keeps the repo clean

- When a PR squash-merges, tear the branch down immediately: `scripts/agent-worktree.sh rm <branch-name>`.
- When an A/B experiment ends, delete the loser's branch the same day.
- When you stop using a Codex/Gemini branch, delete it. "I might look later" is not a
  reason to keep it — the branch is already on GitHub, so deleting the local copy
  loses nothing.

Many branches is fine and expected (one feature per branch, phased work, experiments).
The mess is *un-pruned* branches, not many branches. Prune as you go.

## Read First

Always read:
- This file (`CLAUDE.md`) for coding standards and preflight
- `docs/platform/AGENT_LUDUM_ARCHITECTURE.md` for the platform's architecture — start with its **"Where to make a change (quick index)"** task→file table and **"Notable shapes & tensions"** invariants, then read `AGENT_LUDUM_DESIGN.md` for the design rationale
- `docs/games/<game>/` (e.g. `hoard-hurt-help/`) for that game's design & architecture

Read when relevant:
- `specs/` for feature specs
- `MEMORY.md` for persistent project references
- `docs/operations/debugging-history.md` when something is broken or frozen in
  prod — past incidents, how to diagnose a stuck match, and manual recovery.
  Add an entry whenever you debug a non-trivial production issue.
