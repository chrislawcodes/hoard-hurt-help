---
name: feature-implement-claude
description: The IMPLEMENT stage of the Feature Factory run pure-Claude on the subscription (spec 020) — the orchestrator writes each checkpoint slice itself instead of dispatching Codex, then reviews the diff with Claude subagents. Use when running the factory from Claude Code on the web (e.g. from your phone) where no Codex/Gemini binaries exist, or any time you want a Claude-only build step. Pairs with the feature-review-claude skill for the diff review.
---

# Feature Factory — Implement Stage, Claude-only (this repo)

This is the **implement** stage staffed entirely by Claude on the subscription —
no `codex exec`, no Gemini/Codex CLIs. It exists so the whole factory runs inside
a Claude Code web sandbox. See `specs/020-claude-only-feature-factory/spec.md`.

It is **additive**: the default Codex implement path (`feature-implement` /
`run_factory.py implement`) is unchanged. Use this skill instead when you want the
build step on Claude.

> **Use the repo virtualenv for every `pytest` / `mypy` / `ruff`.** You and any
> subagent MUST run the preflight tools through the repo venv —
> `.venv/bin/pytest`, `.venv/bin/mypy`, `.venv/bin/ruff` — **not** system
> `python3 -m pytest`. System python lacks the app's dependencies, so it reports
> bogus import errors and a broken baseline, which produces wrong findings. (A
> real run hit this: system python flagged the baseline as broken when it wasn't.)
> If `.venv` is missing, create/populate it first; never fall back to system
> python to "make it run."

## The core swap

In the Codex path, `run_factory.py implement` dispatches `codex exec` workers to
write each slice. Here, **the orchestrator (this Claude session) writes the code
itself** with its normal edit tools — exactly how a Direct-Path change is built —
slice by slice, bounded by the `[CHECKPOINT]` markers in `tasks.md`. The diff is
then reviewed by Claude subagents via the `feature-review-claude` skill.

Do **not** call `run_factory.py implement` or `dispatch-codex` in this mode — they
launch Codex, which isn't present.

## Per-slice loop

For each `[CHECKPOINT]`-bounded slice in `tasks.md`, in order:

### 1. Implement the slice directly
Write only that slice's tasks. Stay inside the slice's declared file scope; do not
work ahead into later slices. Do not modify `CLAUDE.md`, `AGENTS.md`, `MEMORY.md`,
or design docs.

### 2. Run the Preflight Gate
From the repo root, before reviewing or committing:

```bash
cd $(git rev-parse --show-toplevel)
.venv/bin/ruff check . && .venv/bin/mypy app/ mcp_server/ && .venv/bin/pytest -q
```

Run these through the repo venv (`.venv/bin/...`), never system `python3` — see
the venv note at the top of this skill. Fix the root cause of any failure — no
suppressions. (Small-change lane and the fast test lane from `CLAUDE.md` apply as
usual.)

### 3. Commit the slice
Commit the slice's changes so the diff has a stable HEAD to review against.

### 4. Review the diff with Claude
Run the diff stage of the `feature-review-claude` dance. The prepare step generates
the canonical diff for you (scope it with `--path`):

```bash
RF=docs/workflow/operations/codex-skills/feature-factory/scripts/run_factory.py
python3 $RF prepare-claude-reviews --slug <slug> --stage diff \
  --path <file-or-dir touched by this slice> [--path ...] --base-ref origin/main
```

- If the plan's `reviews` is empty, the slice is under the diff-review size gate
  (default 50 changed lines) — preflight + CI are the gate; skip to step 6.
- Otherwise spawn one adversarial subagent per lens, write each reply, and
  assemble (see feature-review-claude steps 2–3), passing `--stage diff`.

### 5. Checkpoint the diff
```bash
python3 $RF checkpoint --slug <slug> --stage diff --use-existing-artifact
```

The pre-assembled Claude reviews are healthy, so repair skips dispatch and verify
accepts them; findings are summarized and the slice advances. Address findings by
editing, re-committing, and re-running from step 4 (cap: 3 rounds).

### 6. Next slice
Repeat for the next `[CHECKPOINT]` slice until `tasks.md` is complete, then deliver
the PR as usual.

## Notes & limits

- **Parallel slices:** the Codex path fans parallel workers into isolated
  worktrees. Pure-Claude implementation here is sequential by default (the
  orchestrator builds one slice at a time). If you parallelize implementation with
  subagents, give each its own git worktree so concurrent edits don't clobber —
  same rule as `CLAUDE.md`'s one-worktree-per-task.
- **Diff scope:** `prepare-claude-reviews --stage diff` builds the canonical diff
  from `--path` scope against `--base-ref` (default the branch's merge-base). For a
  whole-branch review, pass the changed top-level paths.
- **Reporting:** review token usage and findings land in `state.json` exactly as in
  the Codex path (see feature-review-claude). Implementation itself runs on the
  orchestrator's subscription session, so its cost is part of the session, not a
  separate dispatch.
