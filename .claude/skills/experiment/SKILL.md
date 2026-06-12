---
name: experiment
description: Compare Direct Path and Feature Factory on the same feature so we can see whether the extra review steps are worth the overhead.
argument-hint: <feature-description>
---

# Experiment Skill

This skill compares two delivery paths:

- `Direct Path`
- `Feature Factory`

The goal is simple: did the extra review steps in Feature Factory change the outcome enough to be worth the overhead?

Use plain comparisons. Do not assume Claude is the integrator. The AI the human is currently working with is the default integrator unless the human says otherwise.

---

## Step 0 — Setup

Create a kebab-case slug with at most 5 words. Create separate worktrees and experiment folders:

```bash
git worktree add /tmp/wt-<slug>-direct -b direct/<slug>
git worktree add /tmp/wt-<slug>-factory -b factory/<slug>
mkdir -p docs/workflow/feature-runs/<slug>-direct
mkdir -p docs/workflow/feature-runs/<slug>-factory
```

Use this hash rule for every `artifact_*_sha256` field:

1. Read the saved artifact as UTF-8 text.
2. Normalize line endings only: convert `CRLF` and `CR` to `LF`.
3. Do not trim whitespace, collapse blank lines, or reorder content.
4. Hash the resulting UTF-8 bytes with SHA-256.

For document stages, hash the saved artifact file.
For the implementation stage, hash a saved scoped diff patch for that stage.

Each run writes its own `experiment.md` file with this exact table.
Token counts are collected in Stage C from the JSONL session file — do not
try to fill them in here.

```markdown
| Stage | Artifact | stage_started_at | stage_finished_at | artifact_before_sha256 | artifact_after_sha256 | review_rounds | issues_raised | issues_accepted | artifact_revised |
|-------|----------|------------------|-------------------|------------------------|-----------------------|---------------|---------------|-----------------|------------------|
| Spec | spec.md | | | | | | | | |
| Plan | plan.md | | | | | | | | |
| Tasks | tasks.md | | | | | | | | |
| Implement | code | | | | | | | | |
```

Save the path to this session's JSONL file in `experiment.md` so Stage C can
find it:

```
Session JSONL: <absolute path to .jsonl file>
```

---

## Stages A And B — Run In Parallel

Run both paths in parallel in separate worktrees.

While the agents are running, report status to the user every 5 minutes. Each update should be brief (3–5 lines):
- Which stage each path is currently on (Spec / Plan / Tasks / Implement / Done)
- Any blockers or escalation signals seen so far
- Estimated time remaining if known

Format: "**[5 min update]** Direct: <stage> | Factory: <stage> | Notes: <any issues>"

### Agent 1 — Direct Path

Use a prompt like this:

> You are running the Direct Path experiment in `/tmp/wt-<slug>-direct` on branch `direct/<slug>`.
> The current integrator is the AI the human is working with in this thread.
> Build the feature directly in this worktree. Ask questions only if needed. Use plain language.
> Keep notes in `docs/workflow/feature-runs/<slug>-direct/experiment.md`.
> Use the exact `experiment.md` table template from this skill.
> For each stage you actually use:
> 1. Record `stage_started_at` when the stage begins.
> 2. Save the stage artifact and record `artifact_before_sha256` with the hash rule from this skill.
> 3. Run exactly one structured self-review pass with this checklist:
>    - Is any acceptance criterion still unmet?
>    - Is there a concrete correctness or scope risk?
>    - Is a test or verification step missing?
>    - Is any workflow or user-facing wording stale or confusing?
> 4. Count distinct concrete issues from that checklist pass as `issues_raised`.
> 5. Apply only the issues you accept and record that count as `issues_accepted`.
> 6. Re-hash the saved artifact as `artifact_after_sha256`.
> 7. Set `artifact_revised` to `yes` only if the two hashes differ; otherwise `no`.
> 8. Record `review_rounds` as `1` for that self-review pass.
> 9. Record `stage_finished_at` after the review closes.
> 10. Record the absolute path to this session's JSONL file in `experiment.md` under "Session JSONL:" so Stage C can count tokens.
> If the human asked for PR creation, run preflight, push, and open a PR against `chrislawcodes/hoard-hurt-help`. If not, keep the result local and report readiness.
> Use repo-root `MEMORY.md` only as a short handoff file.

### Agent 2 — Feature Factory

Use a prompt like this:

> You are running the Feature Factory experiment in `/tmp/wt-<slug>-factory` on branch `factory/<slug>`.
> Follow the repo-owned Feature Factory docs and runner. Use `docs/workflow/feature-runs/<slug>-factory/state.json` as the runtime source of truth.
> Keep notes in `docs/workflow/feature-runs/<slug>-factory/experiment.md`.
> Use the exact `experiment.md` table template from this skill.
> Track `stage_started_at`, `stage_finished_at`, `artifact_before_sha256`, `artifact_after_sha256`, `review_rounds`, `issues_raised`, `issues_accepted`, `artifact_revised`.
> For document stages, hash the saved artifact file. For implementation, hash a saved scoped diff patch for that stage.
> Set `artifact_revised` to `yes` only when the before and after hashes differ.
> Record the absolute path to this session's JSONL file in `experiment.md` under "Session JSONL:" so Stage C can count tokens.
> If the human asked for PR creation, run preflight, push, and open a PR against `chrislawcodes/hoard-hurt-help`. If not, keep the result local and report readiness.
> Use repo-root `MEMORY.md` only as a short handoff file.

Wait for both paths to finish before comparison.

---

## Stage C — Comparison

### Count Claude tokens

Each agent session writes a JSONL file. Use the Python snippet below to extract
Claude Code token usage from those files. Ask the user for the JSONL paths if
they were not saved in Step 0.

```python
import json, sys

def count_tokens(path):
    billed_input = cache_read = output = 0
    with open(path) as f:
        for line in f:
            msg = json.loads(line)
            usage = (msg.get("message") or {}).get("usage") or {}
            billed_input  += usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
            cache_read     += usage.get("cache_read_input_tokens", 0)
            output         += usage.get("output_tokens", 0)
    return billed_input, cache_read, output

for path in sys.argv[1:]:
    bi, cr, out = count_tokens(path)
    print(f"{path}")
    print(f"  billed input : {bi:,}")
    print(f"  cache read   : {cr:,}  (~10x cheaper)")
    print(f"  output       : {out:,}")
    print(f"  real-work    : {bi + out:,}  (billed_input + output)")
```

Run: `python3 /tmp/count_tokens.py <direct-jsonl> <factory-jsonl>`

Record `billed_input`, `cache_read`, and `output` for each path in the
Token Efficiency table below.

**How to read the cost (don't quote the wrong multiple).** When you state a
"Feature Factory cost N× Direct" figure, base it on **real-work = billed_input +
output** — those are the full-price tokens (fresh input, cache *writes*, and
generated output). Do NOT base the multiple on `cache_read`: cached reads are
~10× cheaper per token and a longer Feature Factory session re-reads its prompt
many times, so its `cache_read` ratio looks huge (often ~9×) while the actual
cost ratio is far smaller (~2.5× in Experiment 7). Quote the real-work ratio,
and mention the cache_read ratio only as cheap context.

**These Claude totals UNDERCOUNT Feature Factory.** Its adversarial reviews run
on the **Codex and Gemini** CLIs, whose tokens are NOT in any of these JSONL
files. So Feature Factory's true cost is *higher* than the Claude-only multiple
shows. Always note this caveat next to the Token Efficiency table — the Claude
real-work ratio is a floor on the cost gap, not the whole bill.

### Write the comparison file

Write `docs/workflow/feature-runs/<slug>-comparison.md`:

```markdown
# Experiment — <Feature Name>

## Outputs

- Direct Path: <branch or PR>
- Feature Factory: <branch or PR>

## Did Reviews Change The Work?

| Stage | Path | Artifact | artifact_revised | issues_raised | issues_accepted | review_rounds |
|-------|------|----------|-----------------|---------------|-----------------|---------------|
| Spec | Direct Path | spec.md | | | | |
| Plan | Direct Path | plan.md | | | | |
| Tasks | Direct Path | tasks.md | | | | |
| Implement | Direct Path | code | | | | |
| Spec | Feature Factory | spec.md | | | | |
| Plan | Feature Factory | plan.md | | | | |
| Tasks | Feature Factory | tasks.md | | | | |
| Implement | Feature Factory | code | | | | |

## Token Efficiency

| Path | Billed Input | Cache Read | Output | Real-Work (billed+output) |
|------|-------------|-----------|--------|--------------------------|
| Direct Path | | | | |
| Feature Factory | | | | |

## Outcome

- Did Feature Factory catch problems the Direct Path missed?
- Did the extra review steps change the code, scope, or tests?
- Was the extra overhead worth it for this feature?
- Which path would we choose next time, and why?
```

---

## Step 4 — Write to experiments.md

**This step is required. Do not skip it.**

Append a new experiment entry to `experiments.md` in the repo root. Follow the
format of the existing entries exactly.

1. Determine the next experiment number (count existing `## Experiment N` headings + 1). Experiments 1–6 are ValueRank prior data — **do not edit or renumber them**; new local experiments continue the sequence (7, 8, …).
2. Insert the new entry newest-first: above the current top entry (the highest-numbered one).
3. Tag the entry with the repo it ran in (`hoard-hurt-help`) in the heading, so it is never confused with the ValueRank baseline. Use this template:

```markdown
## Experiment N — `<slug>` (hoard-hurt-help, <date YYYY-MM-DD>)

**Feature:** <one sentence description>

**Direct PR:** #NNN (<closed/merged>) | **Feature Factory PR:** #NNN (<closed/merged>)

| | Direct Path | Feature Factory |
|--|--------------|---------|
| Reviews that changed code | N/N (which stages) | N/N (which stages) |
| Critical catch | <what Direct Path uniquely found, or —> | <what Feature Factory uniquely found, or —> |
| False positives | <count or Low/None> | <count or Low/None> |
| Tests | <N new> | <N new> |
| Claude tokens (billed input / cache read / output) | X / X / X | X / X / X |
| Human interruptions | <N> | <N> |

**Verdict:** <2–3 sentences. Which path won and why. Any post-merge bugs and their PRs.>

**Lesson:** <One concrete routing rule learned from this experiment.>

---
```

4. Update the **Running Tally** table at the bottom of `experiments.md`:
   - Add a row for the new experiment, including the `Repo` column (`hoard-hurt-help`).
   - Update the **Pattern** sentence (data point count) — and note how many points are local vs ValueRank.
   - Update the **Recommendation** bullets if local data changes the guidance. Call out explicitly when local results agree with or contradict the ValueRank baseline.

---

## Step 5 — Update STATUS.md

Mark the experiment complete in `STATUS.md`:

1. Move the feature from "In Progress" or "Next" to "Recently Completed".
2. Note which path won and the PR number(s).
3. Update "Next" for anything now unblocked.

---

## Step 6 — Report to user

Tell the user:
- where the comparison file lives
- both branch names or PR links
- which path you recommend next time and why
- one-line summary of token counts for each path
