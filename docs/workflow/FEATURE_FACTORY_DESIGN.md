# Feature Factory — Design

This is the high-level design doc for the **Feature Factory** (FF): the repo's
end-to-end workflow for taking a non-trivial feature from a rough idea to shipped,
reviewed code. It explains what FF is for, the problems it solves, the core ideas,
and the high-level workflows. It is intentionally *not* the operating manual.

**Related docs:**
- `operations/codex-skills/feature-factory/SKILL.md` — the authoritative phase table, commands, and rules (the runbook).
- `operations/codex-skills/feature-factory/CODEX-ORCHESTRATOR.md` — operational guide for Codex-driven runs.
- `operations/codex-skills/feature-factory/feedback.md` — post-mortem feedback that shaped the current design.
- `../../CLAUDE.md` — the project constitution (preflight gate, push/PR rules, Python standards).
- The four Claude-facing stage skills live in `.claude/skills/feature-{spec,plan,tasks,implement}/`.

---

## 1. What we're trying to accomplish

Most features fail not in the coding but in the thinking: a bad assumption in the
spec, a duplicated module nobody noticed, an "accepted" risk that was never
actually checked. By the time those mistakes show up in code review or production,
they are expensive to undo.

The Feature Factory exists to **move the catch point earlier** — to the spec and
plan stages, where a mistake costs a paragraph instead of a rewrite. It does this
by turning a feature into a disciplined pipeline with **adversarial review at the
cheap stages**, **multiple AI agents checking each other**, and a **single source
of truth for state** so a run can survive being paused, handed off, or resumed.

### Goals

- **Catch bad assumptions before code.** Concentrate review on the spec and plan, where fixes are cheapest.
- **Never rebuild what exists.** A reuse audit forces every capability to be checked against the current codebase before the plan is written.
- **Keep design docs true.** The scoped design/architecture docs are updated *as part of the feature* and reconciled at closeout, so they stay an accurate view of the system.
- **Make "accepted risk" honest.** Every residual risk must name a concrete, cheap, pre-merge check that would catch it if it fired.
- **Ship in small, reviewable slices.** Diffs are sized at planning time (~300 lines per checkpoint), not discovered to be huge at review time.
- **Be resumable and handoff-safe.** State lives in one file; any agent can pick up a run from the earliest incomplete stage.
- **Use the cheapest capable worker.** Orchestration, implementation, and review are split across agents by cost and strength.

### Non-goals

- **Not for trivial work.** Copy edits, one-file tweaks, and changes under ~100 lines should use the **Direct Path** (just implement it, run preflight, open a PR). The overhead of FF only pays off when an independent review would actually catch something.
- **Not a replacement for the preflight gate or CI.** FF wraps them; it does not weaken them.
- **Not a way to auto-merge.** A human still squash-merges. Post-mortem changes to the workflow itself require human approval.

---

## 2. Core ideas

**Spec before code, questions before assumptions.** A run starts with discovery
(clarifying questions or explicitly stated assumptions) and never silently drifts
into implementation before the spec and plan are stable.

**Adversarial review is the unit of quality.** Each major artifact is *attacked*
by independent reviewers looking for ways it is wrong, incomplete, or risky — not
politely proofread. Reviews concentrate at the spec and plan stages by default.

**Multiple agents, distinct roles.** No single agent both writes and signs off on
its own work. The orchestrator authors and judges; a separate implementer writes
code; separate reviewers attack the artifacts.

**One source of truth for state.** `state.json` per run is authoritative. Phase,
blockers, delivery state, and discovery state are read from it — never inferred
from which files happen to exist.

**The engine is durable; the skill is the front door.** The workflow logic lives
in versioned Python scripts (`run_factory.py` and friends) with their own tests.
The skills are thin prompts that drive the engine instead of re-implementing it.

**Hard gates, not just doctrine.** The runner refuses to advance past key points
(e.g. no plan checkpoint without a reuse audit; no `done` without an
architecture-doc decision) so the safety steps can't be quietly skipped.

---

## 3. Vocabulary

- **Run** — one feature moving through the pipeline, identified by a `slug`. Lives in `docs/workflow/feature-runs/<slug>/`.
- **Stage** — a step in the pipeline (discover, spec, plan, tasks, implement, deliver, closeout, …).
- **Checkpoint** — the adversarial review gate at the end of a stage. Checkpoint stages are `spec`, `plan`, `tasks`, `diff`, `closeout`.
- **Slice** — one `[CHECKPOINT]`-bounded chunk of implementation work (~300 lines max).
- **Orchestrator** — the agent driving the run (authors artifacts, judges findings, runs delivery).
- **Reconcile** — resolving each review finding to a terminal status: `accepted`, `rejected`, or `deferred`.
- **Scoped docs** — the design/architecture docs in scope for this feature, resolved from `scope.json` (platform docs vs. a specific game's docs).

---

## 4. The high-level workflow

A run flows through these stages in order. It auto-advances stage to stage — the
orchestrator reads the `→ next:` action after each runner command and keeps moving,
stopping only when blocked, done, or explicitly told to stop. The
`autopilot` command automates this loop — it runs the mechanical steps (checkpoints,
implement + preflight + diff) and stops at decision points (authoring, open review
findings, delivery).

```
  Discovery
     │   (clarifying questions OR explicit assumptions)
     ▼
  Spec ───────────────► Spec checkpoint  (adversarial: feasibility + requirements)
     │                        │ reconcile findings into spec
     ▼                        ▼
  Design  ◄── reuse audit (reuse-report.md) + update scoped design/arch docs
     │        (Spec → Design → Plan: design the target state before planning the build)
     ▼
  Plan ───────────────► Plan checkpoint  (adversarial: implementation + testability)
     │   (every residual    │ reuse-report + scoped docs reviewed too
     │    risk needs a       ▼
     │    verification:)  reconcile findings into plan
     ▼
  Tasks ──────────────► Tasks checkpoint (no default review)
     │   (executable slices, [CHECKPOINT] markers, ~300 lines each, [P:] parallel tags)
     ▼
  ┌─────────────────────────────────────────────┐
  │  per slice:                                  │
  │    Implement slice → build + preflight       │
  │    Diff checkpoint  (scoped to the slice)    │ ◄── repeats per [CHECKPOINT]
  └─────────────────────────────────────────────┘
     │
     ▼
  Deliver  (push branch, open PR, watch CI; the invocation is the consent)
     │   CI failure → fix → re-run CI
     ▼
  Closeout  (what shipped / open / deferred risks)
     │   + reconcile scoped design/arch docs if implementation drifted
     ▼
  Post-mortem  +  STATUS.md update
     │
     ▼
  Done   (human squash-merges; human approves post-mortem changes)
```

The authoritative, fully-detailed phase table — including which agent does what in
each orchestration mode — lives in the engine `SKILL.md`. The flow above is the
shape; the SKILL.md is the contract.

### Stage-by-stage intent

| Stage | What it produces | Why it exists |
|---|---|---|
| **Discovery** | Resolved questions or stated assumptions | Stop bad requirements from entering the spec |
| **Spec** | `spec.md` (scope, acceptance criteria) | A reviewable statement of *what* and *why* |
| **Spec checkpoint** | Reconciled review findings | Attack the spec before any design work |
| **Design — reuse audit** | `reuse-report.md` (reuse / extend / justified-new per capability) | Don't rebuild existing functionality |
| **Design — doc update** | Edits to scoped design/architecture docs | Design the target state before planning the build; keep docs current |
| **Plan** | `plan.md` (architecture, waves, verifiable residual risks) | A reviewable statement of *how* |
| **Plan checkpoint** | Reconciled findings (plan + reuse report + docs) | Attack the design where fixes are cheapest |
| **Tasks** | `tasks.md` (slices, checkpoints, sizes, deps, `[P:]` tags) | Make the build small and ordered |
| **Implement (per slice)** | Code + passing preflight | Build one bounded slice at a time |
| **Diff checkpoint (per slice)** | Reconciled findings on the slice diff | Catch correctness issues slice by slice |
| **Deliver** | Pushed branch, PR, green CI | First-class delivery as a workflow stage |
| **Closeout** | `closeout.md` + doc reconciliation | Record what shipped; true up the docs |
| **Post-mortem** | `postmortem.md` + `STATUS.md` | Feed improvements back into the workflow |

---

## 5. The multi-agent model

FF deliberately splits work across agents so that no agent reviews its own output,
and so the cheapest capable worker does each job.

| Role | Typical agent | Responsibility |
|---|---|---|
| **Orchestrator** | Claude *or* Codex (set by which session drives the run) | Authors spec/plan/tasks, judges review findings, runs delivery |
| **Implementer** | Codex (tokens are free for the operator) | Writes the code for each slice |
| **Adversarial reviewers** | Codex + Gemini (independent lenses) | Attack the artifacts at each checkpoint |

### Two orchestrator modes

The orchestrator is **whichever agent the run is driven from** — not a fixed
default. Both are first-class:

- **Claude Orchestrator** — run driven from a Claude session. Claude leads; Codex implements; Codex + Gemini review.
- **Codex Orchestrator** — run driven from a Codex session (or Claude handed off). Codex leads *and* implements; Gemini reviews; the human approves PR creation and post-mortem changes.

**Handoff** is allowed in both directions (e.g. Claude hands to Codex on token
exhaustion). State and a handoff note travel through `state.json`, so the next
agent resumes from `status`. If a task fails under *both* orchestrators, the run
halts for a human instead of looping (the **oscillation rule**).

---

## 6. The adversarial review model

Reviews are the heart of FF's quality story.

- **Two default lenses, at two stages.** Spec and plan each get one Codex and one Gemini adversarial review. Tasks, diff, and closeout have no default review (operators can opt in with `--extra-gemini-lens`).
- **Why concentrate there?** The spec and plan are the cheapest places to catch bad assumptions. Tasks/diff/closeout mistakes are caught downstream by failed implementations, CI, and operator review of the closeout artifact.
- **All reviews are adversarial.** Each reviewer looks for ways the artifact is wrong, incomplete, or risky — not for things to praise.
- **Independent lenses.** Codex and Gemini reviews stay independent from each other so they don't collapse into one opinion.
- **Convergent (and measured).** Review rounds are tracked per stage and surfaced by `analyze-reviews` (it flags any stage that needed 3+ rounds). Reviewers stay rigorous but are expected to converge — convergence is a discipline surfaced by telemetry, not a hard runner cap.
- **Reconciliation is mandatory.** Every finding must reach a terminal status (`accepted` / `rejected` / `deferred`) before the stage advances.

---

## 7. Safety mechanisms (the hard gates)

These are what make FF more than a checklist. The runner enforces them.

1. **Reuse audit gates the plan.** `checkpoint --stage plan` aborts unless `reuse-report.md` exists with real content. You cannot plan a build without first checking what already exists.

2. **Living docs gate `done`.** A run never reaches `done` until a scoped design/architecture doc was actually changed, *or* an explicit "no change needed" ack is recorded with a reason. This keeps the architecture view trustworthy over time.

3. **Residual risks must be verifiable.** Every entry in the plan's *Residual Risks* section must carry a `verification:` line naming a concrete, cheap, pre-merge check. An "accepted" risk with no way to check it is treated as `unverified` and blocks the plan. (This rule exists because a real feature once shipped a data-model misunderstanding that both reviewers had flagged as an unverified assumption — the verification line forces the "how will we know this is OK?" conversation *before* it becomes a production bug.)

4. **Diffs stay scoped and small.** Slices are sized at ~300 lines in `tasks.md`; the diff checkpoint reviews only the current slice (base = previous diff checkpoint HEAD), not the whole branch.

5. **Preflight at every slice.** "Build and tests" means this repo's Preflight Gate (`ruff` + `mypy` + `pytest`) from the repo root. No slice advances and no PR delivers until it's green. Root-cause fixes only — no suppressions.

6. **Background dispatch discipline.** Long-running background work is paired with a heartbeat monitor, and dispatch specs are never written to `/tmp` (it's GC'd). A silently dead dispatch is assumed when the monitor sees no process and no new commit.

---

## 8. Concurrency & isolation

FF is a multi-agent system that *actively looks for parallel work*, so two
sessions touching the same run is a real failure mode — and historically the
worst one (a branch switched mid-run, half-written files, commits landing on the
wrong branch). The design answer has two layers, plus a state assumption that ties
them together.

1. **Per-slug run locks (enforced).** The long-running mutating commands —
   `implement` and `autopilot` — each take an exclusive per-slug lock (flock-based,
   so the OS auto-releases it if the process crashes — no stale locks to clean up).
   A second concurrent run of the *same* command for the *same* slug exits with an
   "already running" error instead of clobbering files or state. Locks are keyed by
   name, so an `implement` lock and an `autopilot` lock don't block each other; the
   lock guards against two of the same kind racing.

2. **Worktree-per-agent (the real fix for multi-agent).** The run locks do **not**
   protect two agent sessions that share one *git checkout* — that's the collision
   that hurts most. The rule is **one git worktree per agent**. `init` warns loudly
   when it detects it's running in the shared primary checkout rather than a linked
   worktree; genuine single-agent use silences it with `FF_ALLOW_PRIMARY_CHECKOUT=1`.

3. **Single-writer state assumption.** Full-state writes bypass the state lock;
   incremental field updates are lock-guarded. The model assumes one mutator per
   slug at a time — which the run locks and worktree isolation uphold.

The tradeoff is deliberate and honest: the runner **cannot force** an agent into a
worktree, so layer 2 is a loud nudge, not a hard refusal (a refusal would break
legitimate single-agent runs in the primary checkout). The *enforced* protection is
the per-slug run locks; worktree isolation remains operator practice, now surfaced
at `init`.

## 9. When to use FF vs. the Direct Path

| Situation | Path |
|---|---|
| New game logic in `app/engine/`, schema/migration changes, multi-file or risky work | **Feature Factory** |
| Copy edits, single-file tweaks, small bug fixes, additions under ~100 lines | **Direct Path** (implement → preflight → PR) |

The test is simple: **would an independent adversarial review plausibly catch
something?** If yes, the spec/plan overhead pays for itself. If no, FF is pure
overhead and the Direct Path is correct. The `discover --complete` command will
print a loud "SKIP FF ENTIRELY" block when it detects trivial work.

---

## 10. State and artifacts

Each run lives in `docs/workflow/feature-runs/<slug>/`:

| File | Role |
|---|---|
| `state.json` | **Authoritative runtime state** — phase, blockers, delivery state, discovery state. The runner's single source of truth. |
| `spec.md`, `plan.md`, `tasks.md`, `closeout.md`, `postmortem.md` | **Authored artifacts** — the source of truth for intent, scope, and decisions. |
| `reuse-report.md` | **Reuse audit** — overlapping modules, each marked reuse / extend / justified-new; reviewed at the plan checkpoint. |
| Scoped design/architecture docs (`docs/platform/…`, `docs/games/<game>/…`) | **Living system docs** — updated up front and reconciled at closeout. Durable, not per-run. |
| `reviews/*.md`, `reviews/*.checkpoint.json` | **Generated + reconciled review state** — produced by the checkpoint runner; don't hand-edit except resolution fields. |

When in doubt about where a run stands, read `state.json` or run
`status --slug <slug>`. Do not infer state from which files exist.

---

## 11. The engine behind the skill

FF is split into a thin front door and a durable backend:

- **Skills** (`.claude/skills/feature-{spec,plan,tasks,implement}/`) — short prompts that tell an agent how to drive the engine for each stage. They hold no workflow logic of their own.
- **Engine** (`docs/workflow/operations/codex-skills/feature-factory/scripts/`) — `run_factory.py` plus ~30 command modules and a full test suite. This is where checkpoint manifests, review validation, diff writing, reconciliation, and the hard gates actually live.

The runner exposes a command surface (`init`, `status`, `doctor`, `discover`,
`checkpoint`, `reconcile`, `parallel`, `implement`, `deliver`, `closeout`,
`arch-docs`, `block`, `advance`, `autopilot`, `analyze-reviews`, …). The guiding
principle: **the skill stays lean and prefers the scripts; the scripts are the
durable machinery.** If a script is missing or broken, fall back to the closest
manual equivalent but keep the artifact structure intact.

This engine was ported from the ValueRank project and is being verified
end-to-end in this repo (see `STATUS.md`).

---

## 12. Measuring cost

FF records its own cost so runs can be compared over time:

- **Per-command wall clock** and **per-call tokens** (Codex `gpt-*`, Gemini `gemini-*`) land in `state.json`.
- **TTL crossings** flag when a command ran past the prompt-cache TTL (a likely uncached re-read next command).
- `analyze-reviews` rolls these up per feature on demand, writing a report under `docs/workflow/analysis/`. (No scheduled GitHub Action is wired up in this repo — there is no automatic weekly snapshot; run it manually.)
- What FF can't see: Claude orchestrator session tokens (use `/cost` in Claude Code for those).

---

## 13. Design principles, in one place

- Spec before code; questions before assumptions.
- Catch mistakes where they're cheapest — the spec and plan.
- No agent reviews its own work.
- Reuse before build; keep the design docs true.
- "Accepted risk" must come with a way to check it.
- Small, scoped, preflight-green slices.
- One authoritative state file; resumable and handoff-safe.
- One mutator per slug; one worktree per agent.
- The engine is durable; the skill is just the front door.
- Don't use FF for trivial work.
