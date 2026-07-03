---
name: feature-spec
description: Start a non-trivial feature through this repo's Feature Factory — the SPEC stage of the spec → plan → tasks → implement workflow with adversarial-review checkpoints. Use when the user wants to take a sizeable or risky feature from idea to shipped code. This drives the repo-owned engine (run_factory.py); it does NOT author standalone speckit-style specs/NNN files. For small/low-risk changes, use the Direct Path instead (just implement it).
---

# Feature Factory — Spec Stage (this repo)

hoard-hurt-help has a **repo-owned Feature Factory engine**. Do **not** author a standalone spec or write to `specs/NNN-…`. Drive the engine — it owns the spec/plan/tasks/implement flow, the adversarial-review checkpoints, and the run artifacts.

## Should you even use Feature Factory?

Trivial work — copy edits, one-file tweaks, small bug fixes, additions under ~100 lines — should use the **Direct Path**: implement it directly, run the Preflight Gate (`ruff` + `mypy` + `pytest`), open a PR. Skip the engine.

For anything bigger, ask the two routing questions the experiment log (`experiments.md`) validated — they predict Feature Factory value better than backend-vs-UI:

1. **Silent risk?** Would a bug be invisible to tests, CI, and manual poking — pass green, break in prod? **Yes → full Feature Factory.** Its adversarial reviews have a real catch record on exactly these bugs (wrong-key, data-model/semantics). No → a single self-review catches test-visible risk; the factory's extra rounds are overhead.
2. **Design settled?** Is the design already decided up front? **Settled + no silent risk → Direct Path** — the planning stages would just re-derive your existing design at ~2× the cost. **Open design + no silent risk → the middle lane**: spec + one adversarial spec review to settle the design, then a direct build + one independent whole-branch review before the PR — no plan/tasks ceremony (operator-driven; see "Middle lane" in the engine guide).

`discover` records both answers (`--silent-risk yes|no "<note>"`, `--design-settled yes|no "<note>"`) and prints this routing when discovery completes.

## Who orchestrates (set by execution context, not a default)

- Running from a **Claude** session → **Claude** orchestrates: follow the *Claude Orchestrator* column in the engine guide.
- Running from a **Codex** session → **Codex** orchestrates: read `CODEX-ORCHESTRATOR.md`.

## Read first

- Engine guide (phase table + rules): `docs/workflow/operations/codex-skills/feature-factory/SKILL.md`
- Codex orchestrator guide: `docs/workflow/operations/codex-skills/feature-factory/CODEX-ORCHESTRATOR.md`

## How to start the spec stage

Run from the repo root:

```bash
RUN=docs/workflow/operations/codex-skills/feature-factory/scripts/run_factory.py
python3 $RUN status   --slug <slug>                         # always start here
python3 $RUN init     --slug <slug> --path <scope-path>     # if not yet initialized (repeat --path per scope dir)
python3 $RUN discover --slug <slug> ...                     # MANDATORY discovery before spec — see engine guide
```

Then author the spec to `docs/workflow/feature-runs/<slug>/spec.md` and checkpoint it:

```bash
python3 $RUN checkpoint --slug <slug> --stage spec
```

**Run artifacts live in `docs/workflow/feature-runs/<slug>/` — never `specs/NNN-…`.**

## Keep moving

After each runner command, read the `→ next:` line and proceed to it. When the spec checkpoint passes, auto-continue into the **plan** stage (`feature-plan`) without waiting — stop only if a command returns `mark_blocked`, the user asked to stop, or a decision genuinely needs the user.
