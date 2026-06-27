---
name: experiment-thin-vs-factory
description: Run a repeatable A/B that pits the Claude-only Feature Factory engine (run_factory.py) against the Thin path (Claude Code + GitHub Spec Kit stages + a plain adversarial-subagent review, no engine) on the SAME feature, to decide build-vs-switch with real data. Use when you want to test whether the ~40-module factory engine catches enough more than the engine-free thin path to justify maintaining it. Logs results to docs/workflow/experiments-thin-vs-factory.md so signal accumulates across features.
argument-hint: <feature-description>  [scope: path/glob ...]
---

# Experiment — Thin path vs Feature Factory engine

Decides one thing: **does the custom Feature Factory engine catch enough MORE than the engine-free Thin path to justify maintaining ~40 Python modules?** Run it on several real features; the verdict is the accumulated tally, not any single run.

**Burden of proof is on the engine (the incumbent).** If the Thin path lands *within noise* of the Factory on correctness and real findings, that is a vote to **switch**, not a tie — because Thin is cheaper and has near-zero maintenance. Keep the engine only if the Factory shows a **repeatable, material** catch advantage, especially on silent-failure-risk features.

The two arms differ ONLY in orchestration. Hold everything else constant: same feature + acceptance criteria, same author model, the **same review lenses/personas**, the same preflight gate (`.venv/bin/ruff && .venv/bin/mypy app/ mcp_server/ && .venv/bin/pytest`). We are testing the harness, not the prompts.

> **Always use the repo `.venv` for `pytest`/`mypy`/`ruff`** in both arms and in the judge. System `python3` lacks app deps and produces bogus "broken baseline" findings.

---

## Step 0 — Setup

Make a kebab-case slug (≤5 words). Pick a feature that is **representative and ideally silent-failure-prone** (data-model/semantics/wrong-key) — that is where the Factory is supposed to earn its keep.

```bash
git worktree add /tmp/wt-<slug>-factory -b exp-factory/<slug> origin/main
git worktree add /tmp/wt-<slug>-thin    -b exp-thin/<slug>    origin/main
mkdir -p docs/workflow/feature-runs/<slug>-factory docs/workflow/feature-runs/<slug>-thin
```

Each arm keeps notes in its `experiment.md` with this table (SHA rule: read artifact as UTF-8, normalize CRLF/CR→LF only, hash bytes with SHA-256; for the implement stage hash the saved scoped diff):

```markdown
| Stage | Artifact | started_at | finished_at | sha_before | sha_after | review_rounds | findings_raised | findings_accepted | artifact_revised |
|-------|----------|------------|-------------|------------|-----------|---------------|-----------------|-------------------|------------------|
| Spec | spec.md | | | | | | | | |
| Plan | plan.md | | | | | | | | |
| Tasks | tasks.md | | | | | | | | |
| Implement | code (diff) | | | | | | | | |
```

Also keep, in each arm's `experiment.md`:
- `Session JSONL: <absolute path>` — for token counting in Stage C.
- A **Friction log**: one bullet per breakage/workaround/babysitting event (e.g. "engine crashed in factory_telemetry", "had to rebase to reach done", "Spec Kit command missing"). This is a first-class metric — the engine's brittleness vs the thin path's simplicity is half the decision.

---

## Stages A and B — run in parallel (separate worktrees)

Report a brief status every 5 min: `**[5 min]** Factory: <stage> | Thin: <stage> | Friction: <count> | Notes:`.

### Arm A — Feature Factory engine (incumbent)

> You are running the **Feature Factory** arm in `/tmp/wt-<slug>-factory` on `exp-factory/<slug>`.
> Build the feature with the repo's Claude-only Feature Factory: `run_factory.py` for init→discover→spec→plan→tasks→implement, using the `feature-review-claude` skill for the adversarial review at each checkpoint and `feature-implement-claude` for the build. Use `FF_REVIEWER=claude`.
> Use the same review lenses the engine selects per stage. Run preflight via `.venv/bin/...`.
> Fill `docs/workflow/feature-runs/<slug>-factory/experiment.md` (table + Session JSONL + Friction log). Log EVERY engine breakage/workaround in the Friction log.
> Do not open a PR unless the human asks; otherwise leave the branch ready and report.

### Arm B — Thin path (candidate, engine-free)

> You are running the **Thin** arm in `/tmp/wt-<slug>-thin` on `exp-thin/<slug>`. Use NO part of `run_factory.py` / the factory engine.
> Stages: install/use **GitHub Spec Kit** on Claude Code (`uvx --from git+https://github.com/github/spec-kit specify init . --integration claude` if not present) and drive `/speckit.specify → /speckit.plan → /speckit.tasks → /speckit.implement`. (If Spec Kit can't be installed in this environment, author spec.md/plan.md/tasks.md directly using plan mode — note that in the Friction log.)
> **Adversarial review gate (plain subagents, no engine):** at the spec, plan, and final-diff stages, spawn one fresh adversarial subagent PER LENS — use the SAME lenses/personas as `feature-review-claude` (e.g. feasibility-adversarial, requirements-adversarial for spec; testability/implementation for plan; regression-adversarial for diff). Give each only the artifact + repo context; instruct "find the flaw, reject unless proven safe." Read their findings directly, revise, repeat up to 3 rounds. No manifest, no verify, no checkpoint — just spawn → collect → revise.
> Track the same table; for tokens rely on the Session JSONL (and `/usage`). Run preflight via `.venv/bin/...`. Log any friction (Spec Kit install issues, missing capability vs the engine) in the Friction log.
> Do not open a PR unless the human asks; otherwise leave the branch ready and report.

Wait for both arms to finish before Stage C.

---

## Stage C — Comparison (the rubric)

### 1. Correctness — blind neutral judge
Spawn ONE fresh judge subagent. Give it the feature's acceptance criteria and **both final diffs labeled only "Implementation 1" and "Implementation 2"** (randomize which arm is which; record the mapping privately). Ask it to find real correctness bugs, missed acceptance criteria, and scope gaps in each, and say which is more correct and why. Also record: did each arm's preflight + tests pass? This is the primary signal — blind, so it can't favor a harness.

### 2. Review value
From each arm's table: real findings caught, false positives, which stage caught them, and whether findings actually changed the artifact (`sha_before != sha_after`). Note any finding one arm caught that the other missed.

### 3. Cost — tokens + wall-clock
Count Claude tokens from each arm's JSONL (quote the **real-work = billed_input + output** ratio, not cache_read):

```python
import json, sys
def count_tokens(path):
    bi = cr = out = 0
    with open(path) as f:
        for line in f:
            u = (json.loads(line).get("message") or {}).get("usage") or {}
            bi += u.get("input_tokens",0) + u.get("cache_creation_input_tokens",0)
            cr += u.get("cache_read_input_tokens",0); out += u.get("output_tokens",0)
    return bi, cr, out
for p in sys.argv[1:]:
    bi,cr,out = count_tokens(p); print(f"{p}\n  real-work {bi+out:,} (billed {bi:,} + out {out:,}); cache_read {cr:,}")
```

Also record wall-clock per arm and # human interruptions.

### 4. Operational friction
Count Friction-log entries per arm. (Strong prior: the engine breaks more — this thread fixed telemetry recursion, gh-dependency, marker parsing, diff-never-done, token miscount. Count honestly anyway.)

### 5. Ergonomics / maintenance
One line each: how hard to drive, and what maintenance surface each touched (engine modules vs none).

### Write the comparison file
`docs/workflow/feature-runs/<slug>-comparison.md`:

```markdown
# Thin vs Factory — <Feature Name>

## Outputs
- Factory: <branch/PR>   |  Thin: <branch/PR>

## 1. Correctness (blind judge)
- Judge verdict: <which impl more correct, why>
- Preflight/tests: Factory <pass/fail> | Thin <pass/fail>
- Acceptance criteria met: Factory <N/N> | Thin <N/N>

## 2. Review value
| | Factory | Thin |
|--|---------|------|
| Real findings | | |
| False positives | | |
| Unique catch (other arm missed) | | |
| Stages where review changed the artifact | | |

## 3. Cost
| | Factory | Thin |
|--|---------|------|
| Real-work tokens | | |
| Wall-clock | | |
| Human interruptions | | |

## 4. Friction (breakages/workarounds)
| Factory | Thin |
|---------|------|
| <count + bullets> | <count + bullets> |

## Verdict
<2-3 sentences. Did the engine out-catch the thin path enough to justify its maintenance? Apply the burden-of-proof rule.>
```

---

## Step D — Append to the results log (required)

Append to `docs/workflow/experiments-thin-vs-factory.md` (create from its header if missing). Newest-first. Use the row template there, then update the **Running Tally** and the **Switch recommendation** line at the bottom (e.g. "After N runs: Thin within noise on correctness in M/N; engine's unique catches: K — recommend SWITCH / KEEP / UNDECIDED").

This log — not any single run — is the decision artifact. Switch when the tally says the engine isn't earning its maintenance.

---

## Step E — Report to user
- comparison file path + both branch names
- the blind judge's call
- real-work token ratio (Thin vs Factory) + friction counts
- the current Running-Tally recommendation (switch / keep / need more runs)

## Notes
- **n is small.** One run is directional. State the current sample size every time; don't over-claim from n=1.
- **Keep this bench thin.** It's a playbook + a markdown log. Do not grow it into an engine — that would repeat the mistake it exists to evaluate.
- **Cost caveat:** both arms run on the Claude subscription, so token counts are the comparable cost; there's no separate per-call dollar bill.
