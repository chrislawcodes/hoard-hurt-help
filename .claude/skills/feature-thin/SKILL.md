---
name: feature-thin
description: The engine-free delivery path — carry a feature from spec to a shipped PR with lightweight adversarial reviews and no Feature Factory engine (no run_factory.py, no checkpoints). Use when the design is settled up front or settles after one spec review, and the change is a settled backend or single-subsystem feature. Route to the full Feature Factory (the feature-spec skill) instead when one value threads through many consumers/render paths, or a bug could pass tests and still be wrong in prod (silent-failure risk). Reviews run in the foreground; every finding is recorded in a findings-verdict table. Routing evidence lives in docs/workflow/experiments-thin-vs-factory.md.
argument-hint: <feature-description>
---

# Feature Thin — engine-free delivery path (this repo)

The **Thin path** carries a feature from spec to a shipped PR with lightweight
adversarial reviews and **no Feature Factory engine** — no `run_factory.py`, no
checkpoints, no per-slice machinery. You author the artifacts directly and run
the reviews yourself as foreground subagents. It sits between the **Direct Path**
(just build it) and the full **Feature Factory** (the `feature-spec` skill).

It earned its place from the head-to-head runs in
`docs/workflow/experiments-thin-vs-factory.md`: on a settled backend feature the
Thin path matched the engine's output for ~1.7× less cost. Its one recorded loss
(`docs/workflow/feature-runs/betrayal-8-4-comparison.md`) is designed out below —
see the verdict table in Stage 6.

## When to use — routing

| Feature shape | Path |
|---------------|------|
| Design settled up front, or settles after **one** spec review; a settled backend or single-subsystem change | **Thin (this skill)** |
| One value threads through many consumers / render paths, **or** silent-failure risk (a bug that passes tests yet ships wrong in prod) | **Full Feature Factory** — `feature-spec` skill |
| Trivial: copy edits, one-file tweaks, small fixes | **Direct Path** — just build it |

The routing is the experiment log's finding: settled backend → Thin; a value
threaded through several render paths (Run 2, `betrayal-8-4`) → the engine's
deeper plan review earns its keep. If a spec review round surfaces genuinely open
design questions a single round can't settle, stop and route to the full factory —
that is the signal this was never a Thin feature.

## Subagent discipline (applies to every review below)

- **Foreground, not background.** Spawn review subagents synchronously
  (`run_in_background: false`), batched into a single message so they still run
  concurrently, and collect every result in the same flow. Do **not** run them in
  the background — background reviewers stalled the experiment runs ~7 times
  waiting for completion notifications (`betrayal-8-4` needed multiple manual
  nudges).
- **Inputs only.** Each reviewer gets only its inputs — the artifact (or the
  diff) plus its lens instruction. Never paste the builder's reasoning; a reviewer
  anchored by the author stops being adversarial.
- **Fresh per lens.** One new subagent per lens; they share no context.

## The stages

Run folder for this feature: `docs/workflow/feature-runs/<slug>/` (use
`<slug>-thin/` when running as the Thin arm of an A/B experiment so it can't
collide with the factory arm).

### 1. Spec

Author `spec.md` directly. **Do not use GitHub Spec Kit** — it failed headless in
both experiment runs: it can't drive its `/speckit.*` slash commands
non-interactively, and its dropped-in files broke a repo test. Write the spec by
hand with prioritized stories, requirements, and acceptance criteria.

Then **one** adversarial spec review round: two fresh foreground subagents, lenses
`feasibility-adversarial` and `requirements-adversarial`. Apply their verdicts and
revise the spec.

### 2. Plan

Author `plan.md`, then **one** review round: `testability-adversarial` and
`implementation-adversarial`.

The plan **must include a consumer enumeration for every changed value** — every
code path, template, script, payload, doc, and test that reads it. This is where
Run 2's class of gap gets caught: list the consumers before the build so no render
path is forgotten. A changed value with an unlisted consumer is an incomplete plan.

### 3. Tasks

Author `tasks.md`. **One-shot is the default** — a single slice. Slice into
multiple checkpoints only per the criteria in the engine guide's **"Keep Diffs
Scoped"** section (`docs/workflow/operations/codex-skills/feature-factory/SKILL.md`):
ordered steps, a diff clearly over ~300 changed lines, or data-critical gates.
Reference those rules rather than duplicating them, and record the slicing
decision (with its reason) at the top of `tasks.md`.

### 4. Build

Build the feature in one pass (or slice by slice if you sliced). No per-slice
review ceremony — the whole-diff review fan in Stage 5 is the main defense, so put
the focus there, not on slice boundaries.

### 5. Review fan (whole diff)

On the complete diff, spawn fresh foreground subagents in parallel — one per lens:

- `regression-adversarial` — what existing behavior could this break?
- `completeness-adversarial` — "trace every consumer and render path of each
  changed value; report any consumer not updated."
- `silent-failure` — "could this pass tests and still be wrong in prod — wrong
  values stored, secrets/paths leaked in stored text, missing edge states?"
- `test-honesty` — "would each new/changed test still pass if the feature were
  NOT implemented? name any vacuous test."

Plus **one blind reviewer**: give it **only** the acceptance criteria and the diff
— no spec, no plan, no build reasoning — and instruct it to find what's wrong
assuming the feature is broken. The blind lens is what caught real gaps the
engine's own lenses missed in Run 1.

### 6. Findings verdict table (mandatory)

Write a findings-verdict table into the run folder. **Every finding from every
reviewer gets a row — none may be silently dropped:**

```markdown
| # | Reviewer/lens | Finding (one line) | Verdict: fix now / defer / reject | Reason |
|---|---------------|--------------------|-----------------------------------|--------|
```

A deferred finding needs a reason **and**, if it is real, a follow-up note (an
issue or a spawned task). This stage exists because Run 2's Thin arm **lost** by
waving off a flagged real gap — the animation under-count — with no recorded
decision. The table forces a recorded verdict on every finding so a real gap can
never be silently deferred. Apply the "fix now" rows, then re-run the affected
reviewer if a fix is substantial.

### 7. Preflight + PR

Run the **Preflight Gate** from the repo root per `CLAUDE.md` (`ruff` + `mypy` +
`pytest`; the fast lane qualifies only for a genuinely small change). Fix root
causes — never suppress. Then open the PR with a `Validation` section listing the
exact commands run and their pass/fail results, and link the findings-verdict
table.
