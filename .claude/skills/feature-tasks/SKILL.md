---
name: feature-tasks
description: The TASKS stage of this repo's Feature Factory (spec → plan → tasks → implement). Breaks an approved plan into executable, checkpoint-bounded slices and checkpoints them. Drives the repo-owned engine (run_factory.py) and writes to docs/workflow/feature-runs/<slug>/tasks.md. Normally auto-entered after the plan stage; use directly to resume a run at the tasks stage.
---

# Feature Factory — Tasks Stage (this repo)

hoard-hurt-help has a **repo-owned Feature Factory engine**. Drive it; do not write task lists to `specs/NNN-…`.

## Who orchestrates (set by execution context, not a default)

- From a **Claude** session → **Claude** orchestrates (Claude Orchestrator column in the engine guide).
- From a **Codex** session → **Codex** orchestrates (`CODEX-ORCHESTRATOR.md`).

## Read first

- Engine guide: `docs/workflow/operations/codex-skills/feature-factory/SKILL.md`

## How to run the tasks stage

```bash
RUN=docs/workflow/operations/codex-skills/feature-factory/scripts/run_factory.py
python3 $RUN status --slug <slug>            # confirm plan stage is checkpointed first
```

Author `docs/workflow/feature-runs/<slug>/tasks.md` with executable slices and `[CHECKPOINT]` markers at slice boundaries (no slice over ~300 changed lines). Record the parallel-safety analysis, then checkpoint:

### Slicing rules (each checkpoint must be passable)

Every `[CHECKPOINT]` runs the Preflight Gate (`ruff`, `mypy`, `pytest -q`). A phase boundary that cannot pass its own gate is a planning bug, not an implementation problem. Before you write the slices:

- **Every phase must leave the tree green.** Do not split a change from the work that keeps preflight passing across a checkpoint. If a slice removes or replaces a tested route/function, the matching test updates (and any replacement code the tests need) belong in the **same** phase — never "the tests get fixed next phase." If green at phase N genuinely depends on phase N+1, they are one phase.
- **Enumerate every call site for a rename/signature change.** When a slice renames or changes the signature of a symbol, run `grep -rn "<symbol>" app/ mcp_server/` first and list *every* file the grep returns as an explicit edit in the task. A site missed here only surfaces as a `mypy`/`pytest` failure at the checkpoint. Re-grep after planning to confirm no file outside the task list still references the old symbol.

```bash
python3 $RUN parallel   --slug <slug> --note "..." [--found]
python3 $RUN checkpoint --slug <slug> --stage tasks
```

## Keep moving

Read the `→ next:` line after each command. When the tasks checkpoint passes, auto-continue into the **implement** stage (`feature-implement`) — stop only on `mark_blocked`, an explicit user stop, or a decision that needs the user.
