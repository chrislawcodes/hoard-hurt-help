# The Principles / Instances / Scar-Tissue Split

Distilled from hoard-hurt-help's CLAUDE.md (as of 2026-07-04). Three piles:
copy the first verbatim, re-derive the second per repo, never copy the third.

---

## Pile 1 — Portable principles (copy verbatim into every CLAUDE.md)

Verbatim means verbatim — except `{{...}}` slots and inline stack-syntax
substitutions marked with `⟨...⟩`, which you MUST fill for the target stack
(comment syntax, throw/raise vocabulary). If the repo has no remote yet,
replace `origin/main` with `main` throughout and revisit when one appears.

### Communication Style

- Use plain, direct language at a high-school reading level.
- Use short sentences. Explain jargon if you need it.
- Start with a short summary, then details.
- When there are real options, use a table and give a recommendation with a reason.
- Be honest about risk, uncertainty, or disagreement.

### Clarifying Questions

- If you need clarifying questions, decide the full set first.
- Say how many questions you have before asking the first one.
- Ask them one at a time.

### Never Do

- Push commits directly to `main`.
- Merge a PR unless Chris directly asks.
- Suppress errors to make checks pass (type-ignore comments, lint-disable
  comments, swallowed exceptions — whatever this stack's equivalents are).
- Commit secrets or credentials.

### No Suppressions

Never silence a real error with a suppression comment. Fix the root cause. The
only exception is a known upstream bug in a third-party library — document it
with a comment explaining the specific issue.

### Fail Loud — No Swallowed Errors

Surface failures; never hide them. Do not catch an error only to return a
default, null, an empty value, or a fake success — ⟨re-throw / re-raise⟩ or
propagate it so the caller sees the real failure. ⟨If the project shells out:⟩
Always check the return code / stderr of a subprocess or shell command; a
non-zero exit, missing file, or empty output is a failure, not a quiet
success. The only acceptable silent catch is a deliberate, non-gating advisory
path — and it must say so in a comment (⟨this stack's comment syntax⟩
`fail-open: advisory only`).

### PR And Push Rules

- All changes to `main` go through a feature branch + PR.
- For invoked delivery actions (⟨`/ship`, only if that skill was scaffolded;⟩
  "ship it", "push and open the PR"): the invocation is the consent — push
  and open the PR without re-prompting.
- For ad-hoc work where no explicit delivery instruction was given: ask before
  `git push`.
- Run the Preflight Gate before any `git push` or PR creation. Do not push if
  any preflight command fails.
- Every PR must include a `Validation` section listing exact commands run and
  pass/fail results.
- One feature per branch. Do not stack new work on top of an open feature PR
  unless Chris asks.

### When Something Breaks

Diagnose before fixing. Find the smallest reproducing case. Fix the root
cause. Do not retry blindly or change multiple things at once.

### Prune On Merge

- The main checkout stays on `main`, always fast-forwarded to `origin/main`.
  If you find it parked on a feature branch, that is the bug: return it to
  `main` first.
- When a PR merges, delete its branch (and worktree, if any) immediately.
  "I might look later" is not a reason to keep it — the branch is on the
  remote's history already. The mess is *un-pruned* branches, not many
  branches.

### File Structure

- Keep files focused. If a file is doing more than one thing, split it by
  responsibility with a domain-meaningful name.
- No vague filenames like `utils` or `helpers`.

### Ledger Habit

- Update `STATUS.md` when a meaningful task is complete: mark work done and
  note what is now unblocked. (Always — every repo gets this.)
- ⟨Prod repos only:⟩ Add a debugging-history entry whenever you debug a
  non-trivial production issue — at the moment of discovery, not "later".
  Entry format: symptom → diagnosis → root cause → fix → prevention, with
  SHAs and PRs.

---

## Pile 2 — Project-shaped instances (re-derive from the target repo)

| Instance | How to derive |
|----------|---------------|
| **Preflight Gate commands** | The repo's real lint + type-check + test commands, in that order, runnable from the repo root. Verify each one runs before writing it down. Include the invocation prefix that actually works (`uv run`, `pnpm`, `cargo`, ...). |
| **Small-Change Lane** | Keep the shape (all-of: line cap, file cap, no schema/migration change, no new dependency, not a new subsystem → fast test lane). Scale caps to the repo; DROP the lane if the full gate is fast anyway. |
| **Language standards** | Port the intent, not the Python text: typed signatures where the language is gradually typed; no bare catch-alls; async consistency only if the app is async. One short section, this stack's idioms. |
| **Testing requirements** | What to always test (core logic), what to mock (external APIs), what never to mock (the DB in integration tests, if there is one), what the test DB is. Derive from the repo's actual test setup. |
| **Worktree machinery** | Multi-agent repos: copy `scripts/agent-worktree.sh` (generic) and the worktree-per-task rule. Solo repos: pristine-main + prune-on-merge only. |
| **Delivery paths** | Default: Direct Path only. Add a spec-first path ("write the spec, get approval, then implement") for repos expecting large features. Do NOT reference the Feature Factory engine — it lives in hoard-hurt-help and its value is still unproven (see experiments log). |
| **Read First list** | Point at the docs this repo actually has. An architecture doc earns a bullet only once it exists. |
| **Remote / no remote** | No `origin` yet → generate the local-only variant: `origin/main` becomes `main`, skip the ship skill, and Step 5's delivery stops at commit-on-feature-branch. Note in STATUS.md to upgrade the wording when a remote appears. |

---

## Pile 3 — Scar tissue (NEVER copy to a new repo)

These are hoard-hurt-help rules created by *its* incidents. In a new repo they
are noise that teaches agents the constitution contains arbitrary rules:

- Everything about games, turns, matches, bots, MCP wiring, seat names vs
  agent ids, and `app/` vs `mcp_server/` layout.
- Incident-derived invariants (the two turn-row openers, the un-DRY wait
  loops, `rounds_awarded`, sweeper semantics) and every adjudicated
  do-not-do. Those live in that repo's failure-archaeology, where they belong.
- The named-skill roster (`/ship`, `debugging-playbook`, `experiment`, ...).
  Scaffold fresh minimal versions where Step 4 says to; don't reference ones
  the new repo doesn't have.
- The specific preflight commands (`ruff` / `mypy app/ mcp_server/` /
  `pytest -q`) — they are Pile 2, re-derived, even when the new repo happens
  to be Python.

The test for pile membership: **would this rule have been written on day one
of a brand-new project, before any code existed?** Yes → principle. Only
after seeing the code → instance. Only after an incident → scar tissue.
