# 020 — Claude-Only Feature Factory (subagents, run from the cloud)

**Status:** Draft spec for review
**Author:** Claude (design session with Chris)
**Date:** 2026-06-26

## Summary

Add a **Claude-only path** to the existing Feature Factory so a full run can be
started from **Claude Code on the web** (phone or browser) with no desktop app,
no local CLI, and no Gemini/Codex binaries in the sandbox.

The reviewers become **Claude subagents spawned by the web session**, running on
your **subscription** — the same way the orchestrator runs today. No Anthropic
API key, no separate per-token bill.

This is **additive**. The existing Gemini+Codex Feature Factory stays the default
and is untouched. The engine, state machine, checkpoint logic, artifacts, and
reporting are all reused.

## Goals

1. Run the Feature Factory end-to-end from a phone via Claude Code on the web.
2. Reviewers are pure Claude subagents on the subscription (no API key).
3. Keep all existing reporting: token counts per stage/lens and the findings
   summary (severity buckets, resolution status, review coverage).
4. Exploit the subagent model to **parallelize** review work that is serialized
   today, to make runs faster.

## Non-Goals

- Replacing the Gemini/Codex Feature Factory. It remains the default.
- Replacing the Codex *implementer* in this slice. Reviews first; implementation
  strategy is a follow-up (see "Deferred").
- Cross-model diversity. We accept pure Claude (see "Accepted risk").
- Unattended/scheduled runs. The user starts each run from web/phone.

## Background — what is coupled to Gemini/Codex today

Provider coupling lives in three narrow places:

- **Review runners:** `review-lens/scripts/run_gemini_review.py`,
  `run_codex_review.py` shell out to the `gemini` / `codex` CLIs.
- **Reviewer selection:** `factory_review_specs.py` picks `reviewer="gemini"` or
  `"codex"` per stage/lens; `factory_review.py` maps that to `RUN_GEMINI_REVIEW` /
  `RUN_CODEX_REVIEW`.
- **Implementation dispatch:** `factory_cmd_dispatch.py` / `factory_cmd_implement.py`
  run `codex exec`.

Everything else — `run_factory.py`, `factory_stages.py`, checkpoint/adversarial-round
logic, `factory_telemetry.py`, `factory_cmd_analyze_reviews.py`, `closeout.md` —
is provider-neutral.

Crucially, the review dispatch is built for an **external CLI subprocess**. The
subscription constraint changes that: a subprocess can only reach Claude on
subscription by invoking the `claude` CLI (which we are avoiding) — so the Claude
reviewer is **not** a new `run_claude_review.py` subprocess. It is a **subagent
the orchestrator session spawns**.

## Architecture

```
Claude Code on the web (subscription)
└── Orchestrator session  ── authors spec/plan/tasks, drives run_factory.py
    └── spawns review subagents (subscription, parallel)
        ├── lens A subagent → writes reviews/<stage>.claude.<lens-a>.review.md
        ├── lens B subagent → writes reviews/<stage>.claude.<lens-b>.review.md
        └── ...
    run_factory.py checkpoint  ── parses findings, runs adversarial rounds, advances
```

- **Host:** Claude Code web session. You launch it from the Claude mobile app or
  browser, point at the repo, and ask for a Feature Factory run.
- **Orchestrator:** the web session's agent. Same role it has today — authors
  artifacts, calls `run_factory.py` subcommands, reads findings, revises.
- **Reviewers:** Claude subagents the orchestrator spawns. Each gets a single
  lens, **fresh context** (only the artifact + repo, never the author's
  reasoning), and an **adversarial persona** ("assume this is wrong; reject
  unless proven safe"). It writes the review file in the exact existing format:
  `reviews/<stage>.claude.<lens>.review.md` with the same frontmatter and
  `## Findings` / `## Residual Risks` sections.
- **Engine:** unchanged. `run_factory.py checkpoint` parses the review files,
  counts findings, runs up to `MAX_ADVERSARIAL_ROUNDS` (3), and advances stages.

### What changes in code

| Piece | Change |
|---|---|
| Reviewer selection (`factory_review_specs.py`) | Add a toggle (e.g. `FF_REVIEWER=claude`) that maps lens specs to `reviewer="claude"`. Existing Gemini/Codex specs stay default. |
| Review dispatch (`factory_review.py`) | When reviewer is `claude`, do **not** build a CLI subprocess command. The checkpoint manifest already lists the required reviews (reviewer/lens/stage/output path) — the orchestrator reads that manifest and fulfils each entry with a subagent. Add a "claude reviews are orchestrator-fulfilled" branch instead of `RUN_*_REVIEW`. |
| Orchestrator instructions (a skill) | A skill/playbook that tells the web session how to: read the checkpoint manifest, spawn one adversarial subagent per required review **in parallel**, have each write its review file, then call the checkpoint to parse. |
| Reporting (`factory_telemetry.py` + a recorder step) | After each review, record a `token_usage` entry (stage, round, lens, model, input/output tokens) sourced from the subagent's reported usage / session JSONL. See "Reporting". |
| `pricing.json` | Add `claude-*` rows so the optional dollar estimate populates. |

No change to: state schema, findings parser, severity buckets, review coverage
summary, `closeout.md`, `analyze-reviews`.

## Reporting (the part we must not lose)

Today `token_usage` records are written by the Python runner parsing CLI output.
With subagents there is no CLI output to parse, so we source token counts the same
way the **experiment bench already does** — from the session transcript
(`agent-*.jsonl`), which records per-subagent usage on the subscription.

For each review subagent the orchestrator records a `token_usage` entry with the
existing shape: `stage`, `round`, `activity_type="adversarial_review"`, `model`,
`lens`, `input_tokens`, `output_tokens`, `duration_seconds`, `timestamp`.

- **Token counts:** preserved, per stage and per lens.
- **Findings:** unchanged — parsed from the `## Findings` sections and frontmatter
  `resolution_status` exactly as today. `analyze-reviews` and `closeout.md` work
  with no change.
- **Honest tradeoff:** on subscription there is no per-call dollar bill, so
  `cost_usd_estimate` becomes **notional** — we report **token counts** and an
  optional API-equivalent dollar estimate for comparison (the convention
  `experiments.md` already uses). This actually removes the long-standing "the
  true bill is higher" caveat: every token is now one provider, in the transcript.

## Parallelization — the new speed opportunity

The subscription/subagent model removes the single biggest serialization
constraint in the current factory and unlocks fan-out the engine was already
shaped for.

### What was forcing things slow

`factory_review.py` runs Gemini reviews **30 seconds apart** (`GEMINI_STAGGER_SECONDS
= 30`, with a file lock fallback) **purely to dodge Gemini rate limits**. With
pure-Claude subagents there is no Gemini CLI and no shared lock, so the lenses for
a stage can run **fully in parallel**.

### Opportunities (ranked by value)

| # | Opportunity | Effect | Notes |
|---|---|---|---|
| 1 | **Parallel review fan-out per round** | Each round's review time drops from ~Σ(lenses, staggered) to ~max(lens). A 3-lens plan stage goes from roughly 3× to ~1× per round. | Biggest, cheapest win. Just remove the stagger/lock for Claude reviewers and spawn lenses together. |
| 2 | **Cheap extra lenses** | More coverage at ~no added wall-clock | Adding a lens no longer adds serial time, so we can afford more perspectives per stage. |
| 3 | **Repeated sampling per lens (variance reduction)** | Run each lens *k* times in parallel, union the findings | NEW capability — infeasible under Gemini rate limits. Partially offsets the pure-Claude blind-spot risk: more independent samples of the same model surface more of its own coverage. Vote/union to control false positives. |
| 4 | **Parallel implementation slices** | Independent slices build concurrently | The engine already models this (`factory_cmd_implement.py --max-workers 4`). With subagents, slices map to parallel subagents — **requires git worktree isolation per slice** so they don't clobber each other (lesson from `experiments.md`: one giant un-reviewable slice was a failure mode). Deferred with the implementer swap. |
| 5 | **Parallel independent features** | Run several FF runs at once | Orchestration-level; bounded by subscription rate limits. Out of scope here. |

### What does NOT parallelize (be honest)

- **Stage pipeline** spec → plan → tasks → diff: each stage depends on the prior
  artifact. Sequential.
- **Adversarial rounds within a stage:** round N+1 revises based on round N's
  findings. Sequential by construction. (Parallelism lives *inside* a round, across
  lenses — opportunity #1.)

### Real limits / honesty

- **Subscription rate limits + harness concurrency caps** bound the fan-out. Past
  some width you get throttled and gains flatten or reverse. Start modest (all
  lenses of one stage, ~2-5 wide), measure, then widen.
- **More samples = more tokens** burned against subscription limits. "Cheap" is
  relative to a rate-limited Gemini, not free.
- **Parallel implementation needs worktree isolation**, or concurrent edits
  clobber. Don't fan out impl in a shared tree.

## Accepted risk — pure Claude

Claude-reviewing-Claude shares blind spots; a bug class invisible to Claude won't
be caught by a Claude reviewer regardless of persona. We accept this. Mitigations
we *do* apply: fresh context per reviewer, adversarial persona, and (opportunity
#3) repeated independent sampling per lens to reduce variance. If a future run
shows real misses, the fallback is the **hybrid** (keep one foreign reviewer) —
out of scope here.

## Deferred

- **Implementer swap.** Today Codex runs implementation slices. In a Claude-only
  web run the implementer is either the orchestrator agent itself (it has
  Write/Edit) or a Claude impl subagent with worktree isolation. Decide after the
  review path is proven.
- **Hybrid diversity** (one foreign reviewer).
- **Unattended/scheduled runs** (would be Managed Agents, a different host).

## Acceptance criteria

1. From a Claude Code web session, with `FF_REVIEWER=claude`, a checkpoint at a
   stage spawns one adversarial Claude subagent per required lens, **in parallel**,
   each writing a correctly-formatted `reviews/<stage>.claude.<lens>.review.md`.
2. `run_factory.py checkpoint` parses those review files, counts findings, runs
   adversarial rounds, and advances exactly as it does for Gemini/Codex reviews.
3. `token_usage` records are written per review (stage, lens, model, input/output
   tokens); `analyze-reviews` and `closeout.md` render token + findings tables
   with no code change to the report generators.
4. The existing Gemini/Codex path is unchanged when the toggle is off.
5. A documented run from a phone produces a PR with the standard `Validation`
   section.

## Validation plan

- Unit: reviewer-selection toggle maps lens specs to `reviewer="claude"`; manifest
  entries get the `claude` reviewer + correct output paths.
- Unit: telemetry recorder writes a well-formed `token_usage` entry from a sample
  subagent usage payload.
- Integration: a small real feature run from a web session, reviews-only on Claude,
  confirming parallel fan-out, findings parsing, and reporting output.
- Preflight Gate (`ruff`, `mypy`, `pytest`) green before push.

## Decisions (resolved)

1. **Token-usage source:** post-hoc from the session transcript (`agent-*.jsonl`)
   — the proven, subscription-safe mechanism the experiment bench already uses.
2. **Default fan-out:** all lenses for a stage run in parallel, **k=1** (one
   sample per lens). This is the big speed win (removes the Gemini stagger) at the
   lowest rate-limit/token pressure. Repeated sampling (k>1, opportunity #3) is an
   opt-in we enable after measuring headroom — not on by default.
3. **Toggle:** an `FF_REVIEWER=claude` environment variable selects the Claude
   review path. Off by default; the Gemini/Codex path is unchanged when unset.
