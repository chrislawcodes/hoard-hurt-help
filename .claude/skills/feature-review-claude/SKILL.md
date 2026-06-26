---
name: feature-review-claude
description: Run a Feature Factory checkpoint's adversarial reviews as pure-Claude subagents on the subscription (spec 020), instead of the Gemini/Codex CLIs. Use when running the Feature Factory from Claude Code on the web (e.g. from your phone) where no Gemini/Codex binaries exist, or any time you want Claude-only reviews. Drives prepare-claude-reviews → parallel review subagents → checkpoint. Keeps all token/findings reporting.
---

# Feature Factory — Claude-only review path (this repo)

This is the **review** step of a Feature Factory checkpoint, staffed by **Claude
subagents on the subscription** instead of the Gemini/Codex CLIs. It exists so the
factory runs end-to-end inside a Claude Code web sandbox (no `gemini`/`codex`
binaries, no API key). See `specs/020-claude-only-feature-factory/spec.md`.

It is **additive**: the default Gemini/Codex path is unchanged. This path only
activates when reviews are staffed by Claude (via prepare-claude-reviews, which
also persists the choice so the later `checkpoint` matches).

## When to use

- Running the factory from Claude Code on the web / mobile.
- You want pure-Claude reviews for a `spec`, `plan`, `tasks`, or `diff` checkpoint.

Author the stage artifact (spec.md / plan.md / tasks.md / the diff) exactly as you
normally would. This skill replaces only the **review** half of the checkpoint.

## The dance (per stage)

Run from the repo root. `RF=docs/workflow/operations/codex-skills/feature-factory/scripts/run_factory.py`.

### 1. Prepare — build the manifest + emit one prompt per lens

```bash
python3 $RF prepare-claude-reviews --slug <slug> --stage <stage>
```

This prints a JSON plan: one entry per lens with `prompt_path`, `response_path`,
and `review_path`. It also persists `review_policy.reviewer = "claude"` so the
later `checkpoint` rebuilds a matching Claude manifest. If `reviews` is empty, the
stage has no default reviews — skip straight to `checkpoint`.

### 2. Review — spawn one adversarial subagent per lens, IN PARALLEL

For each entry in the plan, spawn a **fresh** subagent (send them in a single
message so they run concurrently — this is the speed win over the old serialized
Gemini path). Give each subagent:

- **Only** the contents of its `prompt_path` (the artifact + the adversarial lens
  instruction). Fresh context — do not paste your own reasoning; the reviewer must
  not be anchored by the author.
- Instruction to **return only** the review markdown: a `## Findings` section and a
  `## Residual Risks` section, ordered by severity, nothing else.

Write each subagent's reply verbatim to its `response_path`. For each subagent note
two things from its result: its **session transcript path** (`agent-<id>.jsonl`) and
its **reported total tokens** (the `subagent_tokens: N` figure in the agent's usage).
Pass both in the next step so the review's token usage is recorded accurately — the
transcript gives input/cache, and the total recovers true output tokens (the
transcript alone only records a streaming-start output snapshot).

### 3. Assemble — turn each reply into a checkpoint-compatible review file

`RCR=docs/workflow/operations/codex-skills/review-lens/scripts/run_claude_review.py`

For each entry:

```bash
python3 $RCR --mode assemble \
  --artifact <artifact path for the stage> \
  --lens <lens> --stage <stage> \
  --output <review_path> \
  --workspace-dir "$(git rev-parse --show-toplevel)" \
  --git-base-ref origin/main \
  --response-file <response_path> \
  --session-jsonl <the subagent's agent-*.jsonl> \
  --subagent-total-tokens <the subagent's reported total tokens>
```

This writes `<stage>.claude.<lens>.review.md` (byte-compatible with Gemini/Codex
review files) and records the review's tokens into `state.json` token_usage
(input/cache from the transcript, true output recovered from the total). A
malformed/empty reply writes a failed review (exit 5) — re-run that subagent.

### 4. Checkpoint — parse findings, run the round, advance

```bash
python3 $RF checkpoint --slug <slug> --stage <stage>
```

The pre-assembled Claude reviews are already healthy, so `repair` skips dispatch
and `verify` accepts them; the checkpoint counts findings, advances the adversarial
round, and reports the findings summary exactly as for Gemini/Codex. Address
findings by revising the artifact, then repeat from step 1 for the next round (cap:
3 rounds, same as always).

## Defaults (spec 020)

- **Pure Claude** — accepted blind-spot risk; mitigated by fresh context + the
  adversarial lens prompt. No foreign reviewer.
- **k = 1** — one subagent per lens, all lenses parallel. (Re-sampling a lens k>1
  is a future opt-in; not on by default.)
- **Model** — labeled `claude-opus-4-8` for telemetry/pricing; the subagent runs
  on whatever the session uses.

## Reporting

Nothing special to do. Token usage (per stage/lens, sourced from the subagent
transcripts) and the findings summary flow into the same `state.json`,
`analyze-reviews` report, and `closeout.md` as the Gemini/Codex path. On the
subscription there is no per-call dollar bill, so cost is reported as token counts
plus an API-equivalent estimate.
