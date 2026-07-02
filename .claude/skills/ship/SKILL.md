---
name: ship
description: Merge a finished PR the repo-approved way — rebase onto main, run the Preflight Gate, watch CI, squash-merge, then prune the branch and worktree. This is the ONLY sanctioned way to merge (never bare `gh pr merge`). Invoking /ship is Chris's consent to push and merge — do not re-prompt. Use when asked to "ship it", "merge the PR", or "/ship".
argument-hint: [pr-number-or-branch]
---

# Ship Skill

Take a finished feature branch through the full delivery gate and merge it.
The pipeline is: **rebase → preflight → push → CI green → squash-merge → prune**.

Invoking this skill IS the consent to push and merge (per `CLAUDE.md` PR rules).
Do not ask again before pushing or merging. Do stop and ask if anything in the
pipeline fails in a way you cannot fix (rebase conflicts, CI failures you
cannot reproduce, unrelated breakage).

## Step 0 — Identify the PR and branch

- If the user gave a PR number or branch name, use it.
- Otherwise use the current branch: `git branch --show-current` and find its
  open PR (`gh pr view --json number,title,state,url` locally, or the GitHub
  MCP `list_pull_requests` tool on the web).

Stop and ask if:

- No open PR exists for the branch (offer to create one first).
- The branch is `main` — never ship from `main`.
- The PR has unresolved change-requesting reviews.

Work from the branch's own worktree, not the main checkout.

## Step 1 — Rebase onto main

```bash
git fetch origin main
git rebase origin/main
```

- If the rebase is clean and moved the branch, continue.
- If there are conflicts: abort (`git rebase --abort`), summarize which files
  conflict, and ask how to proceed. Do not resolve conflicts silently in
  someone else's changes.

## Step 2 — Run the Preflight Gate

Run the `preflight` skill (it picks the fast or full lane and reports
pass/fail per command).

- **All pass:** continue, and keep the results table for the PR's
  `Validation` section.
- **Any fail:** stop. Fix the root cause (never suppress), re-run preflight,
  and only then continue. If the failure is unrelated pre-existing breakage
  confirmed against a clean `origin/main` worktree, tell the user and ask
  whether to ship anyway.

## Step 3 — Push

```bash
git push --force-with-lease -u origin <branch>
```

`--force-with-lease` is required after a rebase and safe: it refuses to push
if someone else pushed to the branch in the meantime. Never use plain
`--force`.

Make sure the PR body has a `Validation` section listing the exact preflight
commands and their pass/fail results. Add or update it if missing
(`gh pr edit`, or the MCP `update_pull_request` tool).

## Step 4 — Watch CI

CI is the real gate — it runs the full `pytest` suite even when preflight used
the fast lane. Wait for the `CI / Lint, type-check, test` check on the PR's
head commit.

- Local CLI: `gh pr checks <number> --watch`
- Web / MCP: check status with the `pull_request_read` tool (method
  `get_status`) or `get_check_run`. Do not poll in a tight loop with `sleep` —
  re-check at sensible intervals or use the harness's wait mechanisms.

Outcomes:

- **Green:** continue to merge.
- **Red:** read the failing job's log (`gh run view --log-failed`, or the MCP
  `get_job_logs` tool with `failed_only`). Diagnose before fixing — find the
  root cause, fix it on the branch, and restart from Step 2. Do not retry
  blindly. If it fails for the same reason twice, or the failure is unrelated
  to the branch, stop and report the diagnosis instead of kicking it again.

## Step 5 — Squash-merge

Only when CI is green on the current head commit:

```bash
gh pr merge <number> --squash
```

(Web / MCP: `merge_pull_request` with `merge_method: "squash"`.)

Use the PR title as the squash commit title. Never merge with failing or
still-running checks, and never use a different merge method.

## Step 6 — Prune immediately

This is the rule that keeps the repo clean — do not skip it:

```bash
cd <main-checkout>            # the primary hoard-hurt-help/ folder
git checkout main && git pull origin main
scripts/agent-worktree.sh rm <branch>
```

This removes the worktree and deletes the local branch (squash-merges rewrite
history, so the script force-deletes — that is expected). If you are on the
web with no local worktree, just confirm the remote branch was deleted by the
merge.

## Step 7 — Close the loop

- Update `STATUS.md` if this PR completes a meaningful task: mark it done and
  note what is now unblocked.
- Report to the user: merged PR link, the squash commit on `main`, CI result,
  and confirmation that the worktree and branch were pruned.
