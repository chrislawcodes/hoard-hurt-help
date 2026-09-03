# Hoard Hurt Help — Project Constitution

This file is the working contract for any agent that works in this repo.

Hoard Hurt Help is a multiplayer Prisoner's Dilemma game where LLM agents compete against each other.

## Communication Style

Reading level and sentence length are handled by the `plain-language` output
style (`.claude/output-styles/plain-language.md`).

- When there are real options, use a table and recommend one with a reason.
- Be honest about risk, uncertainty, or disagreement.
- If you need clarifying questions, decide the full set first, say how many you
  have, then ask them one at a time.

## Never Do

- Push commits directly to `main`.
- Merge a PR unless Chris directly asks.
- Suppress errors to make checks pass (`# type: ignore`, `# noqa`, swallowed
  exceptions). The only exception is a known upstream bug in a third-party
  library — document it with a comment naming the specific issue.
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
- To merge a PR, invoke `/ship` — not bare `gh pr merge`.

## PR Watching

- After opening a PR, do not watch it on a timer. Report the PR link and stop —
  opening the PR is the end of the task. Recurring self check-ins (`send_later` /
  `ScheduleWakeup`) are the main source of wasted tokens here: each one is a full
  uncached turn that usually finds nothing.
- Passive webhook events (`<github-webhook-activity>`: review comments, CI
  failures, merges) arrive on their own and cost nothing until they fire. Act on
  those when they land; do not unsubscribe reflexively. The thing to avoid is
  *timed polling* of a PR with no pending event.
- Enter a watch loop **only** when Chris asks for a terminal delivery action —
  `/ship`, "merge when green", "babysit this PR". Then watch until the merge or
  close completes, and prune.

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

A small Direct Path change may run the fast test lane
(`pytest -q -m "not integration"`, ~13s) instead of the full suite. The
`/preflight` skill owns the size thresholds and picks the lane for you — run it
rather than judging by eye. CI runs the full `pytest` suite on every PR either
way, so the fast lane is local signal only.

A small change also **skips** the spec / plan / tasks docs and the `STATUS.md`
update. It still **keeps** the worktree-per-task rule and the PR's `Validation`
section.

## Python Standards

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

## One Home Per Rule

Before writing a query or helper that encodes a rule — who can play, what counts
as finished, which key is still valid — search for an existing one and call it.
This repo's worst bugs have all been the same shape: one rule written twice,
slightly differently, by two people who each had a reasonable definition in mind
and no way to see the other's.

- **The name is the index.** A rule belongs in a module named for the question it
  answers, so grepping the question finds it. There is deliberately no list of
  these to keep in sync — a list would be one more copy that drifts. If a search
  turns up nothing, that is the signal the rule has no home yet, so give it one.
- **Two forms need a test.** When a rule genuinely must exist twice — a SQL filter
  and an in-memory check, or code and the prose that describes it — write a test
  that exercises both and asserts they agree. Nothing else stops them drifting,
  and review does not catch it.

## When Something Breaks

Diagnose before fixing. Find the smallest reproducing case. Fix the root cause. Do not retry blindly or change multiple things at once.

## Project Status

When a meaningful task is complete, write it down in **two places, at two lengths**:

- `STATUS.md` — **one line**: what shipped, and what it unblocks. This is the
  dashboard someone reads at the start of a session. Keep it scannable.
- `docs/operations/what-shipped-and-why.md` — **the full account**: the reasoning,
  what was tried and rejected, what it deliberately left undone. Link to it from
  the `STATUS.md` line.

The long form goes in the second file. STATUS.md once held both and reached 26,796
words, which made it useless as a dashboard.

## How We Work — Worktrees, Clean Main, Prune On Merge

The main checkout stays on a fresh `main`, every task gets its own worktree, and
branches get deleted the day they merge. Skipping the prune step is what rots the
repo — abandoned branches pile up until a session lands on a stale one and builds
on bad assumptions.

**Keep the main checkout pristine.** `hoard-hurt-help/` stays on `main`,
fast-forwarded to `origin/main`. Explore and answer questions there freely, but
create your worktree the moment you are about to make your first change — not at
session start, and never after the first edit. If you find it parked on a feature
branch, put it back on `main` first.

**One worktree per task.** Two agent sessions editing the same directory clobber
each other, and one session's work gets swept into another's commit. Branch fresh
off `origin/main`; never reuse an old branch as a starting point.

```bash
scripts/agent-worktree.sh new <branch-name>   # fresh worktree off origin/main
scripts/agent-worktree.sh list                # show all worktrees
scripts/agent-worktree.sh rm <branch-name>    # remove worktree + delete branch after merge
```

Work, commit, push, and open the PR from inside that worktree. If the work spans
more than one sitting, `git fetch origin main && git rebase origin/main` first.

**Prune the moment it's done.** When a PR merges, an experiment ends, or you stop
using an agent branch, run `scripts/agent-worktree.sh rm <branch-name>` that day.
The branch is already on GitHub, so deleting the local copy loses nothing. Many
branches is fine and expected; *un-pruned* branches are the mess.

## Read First

**Before changing code**, find your task's row in the **"Where to make a change
(quick index)"** table near the top of `docs/platform/AGENT_LUDUM_ARCHITECTURE.md`.
Each row names the file *and* the trap. If the change touches an invariant, read
**"Notable shapes & tensions"** directly below it.

That is normally enough. Do **not** read the whole architecture doc by default —
the module map below those two sections is ~8,000 words of reference. Look things
up in it; don't read it through.

Read when relevant:
- The **"Subsystems"** section of that doc for a module inventory — or ask
  `repowise`, which is regenerated from the live tree and cannot go stale
- `docs/platform/AGENT_LUDUM_DESIGN.md` when changing *why* something is shaped
  this way, rather than *what* it does
- `docs/games/<game>/` (e.g. `hoard-hurt-help/`) for that game's design & architecture
- `specs/` for feature specs
- `docs/operations/debugging-history.md` when something is broken or frozen in
  prod — past incidents, how to diagnose a stuck match, and manual recovery.
  Add an entry whenever you debug a non-trivial production issue.
