---
name: feature-implement
description: The IMPLEMENT stage of this repo's Feature Factory (spec → plan → tasks → implement). Implements the checkpoint-bounded slices from an approved tasks.md, runs the Preflight Gate, diff-checkpoints, and delivers a PR. Drives the repo-owned engine (run_factory.py). Normally auto-entered after the tasks stage; use directly to resume a run at the implement stage.
---

# Feature Factory — Implement Stage (this repo)

hoard-hurt-help has a **repo-owned Feature Factory engine**. Drive it through `run_factory.py`.

## Who orchestrates (set by execution context, not a default)

- From a **Claude** session → **Claude** orchestrates: Claude drives the runner and **dispatches Codex** to write each slice (Codex tokens are free); Codex + Gemini run the reviews. Follow the Claude Orchestrator column in the engine guide.
- From a **Codex** session → **Codex** orchestrates and implements directly (`CODEX-ORCHESTRATOR.md`).

## Read first

- Engine guide: `docs/workflow/operations/codex-skills/feature-factory/SKILL.md`

## How to run the implement stage

```bash
RUN=docs/workflow/operations/codex-skills/feature-factory/scripts/run_factory.py
python3 $RUN status    --slug <slug>          # confirm tasks stage is checkpointed first
python3 $RUN implement --slug <slug>          # dispatch the next ready slice (Claude orchestrator)
```

For each slice: implement it, then run this repo's **Preflight Gate** from the repo root before advancing —

```bash
cd "$(git rev-parse --show-toplevel)"
python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q
```

Never silence errors with `# type: ignore` / `# noqa` (see `CLAUDE.md`). Then diff-checkpoint and deliver:

```bash
python3 $RUN checkpoint --slug <slug> --stage diff
python3 $RUN deliver    --slug <slug>          # opens the PR; a human squash-merges via /ship
```

## Keep moving

Read the `→ next:` line after each command and proceed. Stop only on `mark_blocked`, a failed Preflight Gate you cannot fix, an explicit user stop, or a decision that needs the user. Do not run `gh pr merge` — delivery opens the PR; merging is `/ship`.
