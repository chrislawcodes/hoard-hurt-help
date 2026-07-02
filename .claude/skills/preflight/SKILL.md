---
name: preflight
description: Run the Preflight Gate before any git push or PR creation. Decides automatically whether the change qualifies for the Small-Change Lane (fast test lane) or needs the full gate, runs the right commands from the repo root, and reports pass/fail per command. Use before every push, or whenever asked to "run preflight", "check if this is ready to push", or "is this a small change".
argument-hint: [--full | --fast]
---

# Preflight Skill

Run the repo's Preflight Gate and report the results. This skill decides which
lane applies, runs the commands, and gives a clear pass/fail verdict. It does
NOT push — it only tells you whether pushing is allowed.

Hard rules (from `CLAUDE.md`):

- Do not push if any preflight command fails.
- Fix the root cause. Never use `# type: ignore`, `# noqa`, or swallowed
  exceptions to silence an error.
- If unrelated code breaks the checks, validate in a clean worktree from
  `origin/main` before pushing (Step 4).

---

## Step 1 — Pick the lane

If the user passed `--full` or `--fast`, use that lane and skip the checks
below. Otherwise, measure the change against `origin/main`:

```bash
cd "$(git rev-parse --show-toplevel)"
git fetch origin main
git diff --stat origin/main...HEAD
git diff --name-only origin/main...HEAD
```

The change qualifies for the **Small-Change Lane** only when **all** of these
hold:

| Check | How to verify |
|-------|---------------|
| ≤40 lines changed | Total insertions + deletions from `--stat` |
| ≤5 files touched | File count from `--name-only` |
| No DB migration | No file under `migrations/` |
| No model or schema change | No file under `app/models/` or `app/schemas/` |
| No new dependency | `pyproject.toml` dependencies unchanged |
| Not a new subsystem | No new top-level package or major new module |
| Direct Path work | The Small-Change Lane never applies to Feature Factory runs |

If **any** check fails, use the **full lane**. When it is a close call, use the
full lane — it only costs extra test time.

State which lane you picked and why (one line, e.g. "Full lane: touches
`migrations/`").

Note: uncommitted work does not show up in `origin/main...HEAD`. If the
worktree is dirty, include the working-tree diff (`git diff --stat` and
`git diff --stat --cached`) in the line/file counts.

## Step 2 — Run the gate

Run from the repo root. Run the commands separately (not chained with `&&`) so
every command reports a result even if an earlier one fails.

**Full lane:**

```bash
cd "$(git rev-parse --show-toplevel)"
python3 -m ruff check .
mypy app/ mcp_server/
pytest -q
```

**Small-Change Lane (fast test lane):**

```bash
cd "$(git rev-parse --show-toplevel)"
python3 -m ruff check .
mypy app/ mcp_server/
pytest -q -m "not integration"   # fast lane (~13s); CI still runs the full suite
```

## Step 3 — Report the verdict

Report a table like this, then a one-line verdict:

```markdown
| Command | Result |
|---------|--------|
| python3 -m ruff check . | PASS |
| mypy app/ mcp_server/ | PASS |
| pytest -q -m "not integration" | FAIL — 2 failed |

Verdict: NOT ready to push (fast lane).
```

- **All pass:** the change is clear to push. Remind that CI runs the full
  `pytest` suite on every PR, so the fast lane is local signal only.
- **Any fail:** do NOT push. Show the failing output. Fix the root cause and
  re-run the gate. Never suppress the error to make it pass.

This table is also the `Validation` section the PR will need — save it.

## Step 4 — Unrelated breakage

If a failure is in code your change never touched, confirm it is pre-existing
before fixing anything:

```bash
scripts/agent-worktree.sh new preflight-baseline-check
# run the same failing command in that clean worktree off origin/main
```

- **Fails there too:** the breakage is on `main`, not from your change. Tell
  the user; do not bundle the fix into this branch without being asked.
- **Passes there:** your change caused it after all. Fix the root cause.

Tear the check worktree down when done:

```bash
scripts/agent-worktree.sh rm preflight-baseline-check
```
