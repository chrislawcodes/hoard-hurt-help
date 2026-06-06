---
name: feature-plan
description: The PLAN stage of this repo's Feature Factory (spec → plan → tasks → implement). Authors the technical plan for an initialized feature run and checkpoints it. Drives the repo-owned engine (run_factory.py) and writes to docs/workflow/feature-runs/<slug>/plan.md — not speckit-style files. Normally auto-entered after the spec stage; use directly to resume a run at the plan stage.
---

# Feature Factory — Plan Stage (this repo)

hoard-hurt-help has a **repo-owned Feature Factory engine**. Drive it; do not hand-roll a plan or write to `specs/NNN-…`.

## Who orchestrates (set by execution context, not a default)

- From a **Claude** session → **Claude** orchestrates (Claude Orchestrator column in the engine guide).
- From a **Codex** session → **Codex** orchestrates (`CODEX-ORCHESTRATOR.md`).

## Read first

- Engine guide: `docs/workflow/operations/codex-skills/feature-factory/SKILL.md`

## How to run the plan stage

```bash
RUN=docs/workflow/operations/codex-skills/feature-factory/scripts/run_factory.py
python3 $RUN status --slug <slug>            # confirm spec stage is checkpointed first
```

Author the plan to `docs/workflow/feature-runs/<slug>/plan.md` (architecture decisions, risks — every residual risk needs a concrete `verification:` line; see the engine guide), then:

```bash
python3 $RUN checkpoint --slug <slug> --stage plan
```

## Keep moving

Read the `→ next:` line after each command. When the plan checkpoint passes, auto-continue into the **tasks** stage (`feature-tasks`) — stop only on `mark_blocked`, an explicit user stop, or a decision that needs the user.
