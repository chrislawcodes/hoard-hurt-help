# Feature Factory — Claude-only run diagnostic (dedup-engine-cseries)

Purpose: hand another AI enough to diagnose and fix the FF engine gaps surfaced by
the first full **Claude-only** run. Engine lives in
`docs/workflow/operations/codex-skills/feature-factory/scripts/` (a vendored fork
of ValueRank; excluded from `ruff`). Review-lens helpers live in
`docs/workflow/operations/codex-skills/review-lens/scripts/`.

## Environment facts (the context these bugs live in)

- This ran in **Claude Code on the web** (sandboxed). **No `gh`, no `codex`, no
  `gemini` binaries.** GitHub actions go through the GitHub **MCP** tools.
- Two Python interpreters matter: the repo **`.venv`** has all deps (sqlalchemy,
  fastapi, …); the **system `python3` does not**. The FF runner scripts run under
  system `python3` and that's fine (they don't import the app). But review
  **subagents** that run `pytest`/import the app must use `.venv` — one reviewer
  used system `python3`, got `1149 collected, 23 errors` and wrongly flagged the
  test baseline as unverifiable. The real baseline (`.venv/bin/pytest -q
  --collect-only`) was **1317**.

## What worked (the Claude-only path is viable)

The supported flow ran cleanly for spec, plan, and diff:

```
prepare-claude-reviews --slug S --stage STAGE [--context …]   # writes manifest + per-lens prompt files
# → spawn one Claude subagent per lens (parallel), write each reply to its response_path
run_claude_review.py --mode assemble --artifact A --lens L --stage STAGE \
    --output …review.md --git-base-ref origin/main \
    --response-file …response.md --session-jsonl <agent-*.jsonl> --subagent-total-tokens N
checkpoint --slug S --stage STAGE        # verify accepts the pre-assembled reviews; counts findings
reconcile --slug S --review …review.md --status accepted --note "…"
```

- `prepare-claude-reviews` correctly persisted `review_policy.reviewer=claude` and
  emitted matching manifests; `checkpoint` then skipped dispatch and accepted the
  files. Verified via `verify_review_checkpoint.py` (frontmatter keys + section
  headers + `artifact_sha256` match + body/frontmatter resolution sync).
- Token accounting worked: `--session-jsonl` (the subagent transcript) + a
  `--subagent-total-tokens` figure recovered input/cache/output into
  `state.json:token_usage`.
- `init`, `discover`, `status`, `parallel`, `reconcile`, and `closeout
  --pr-number` all worked without `gh`.
- Multi-round loop worked: revise artifact → re-`prepare` → re-review → re-`checkpoint`
  (spec took 3 rounds, plan 2). Findings were real and high-value (caught a wrong
  cancel-site count, a mis-classified "duplicate", and an inverted count filter).

## What didn't work — ranked, with evidence and fix pointers

### 1. [HIGH] `deliver` hard-requires `gh` — the one remaining gh dependency on the web path
- **Symptom:** `run_factory.py deliver --slug S --refresh` →
  `deliver requires the gh CLI to be installed`.
- **Why it matters:** the Claude-only path exists *for* the web sandbox, which has
  no `gh`. So the final stage of the workflow can't run there. Worked around by
  creating the PR via the GitHub MCP and running
  `closeout --slug S --pr-number 559 --pr-url … --note …` (closeout already accepts
  explicit PR args; deliver does not).
- **Fix:** in `scripts/factory_cmd_deliver.py`, add a no-`gh` path mirroring
  closeout — accept `--pr-number`/`--pr-url`/`--merge-sha` and skip the `gh`
  detection/creation when provided (or shell the GitHub MCP). Grep the file for the
  `gh` precondition and the PR-create/CI-watch calls.

### 2. [HIGH] `diff` stage is stuck `repairable` because post-diff doc commits advance HEAD
- **Symptom:** after the diff checkpoint passed, every later commit (closeout,
  postmortem, STATUS, the reconcile ledger) flipped `status` back to
  `diff: repairable`. The run can never reach `done`.
- **Repro:** checkpoint diff (passes) → commit any doc under
  `docs/workflow/feature-runs/<slug>/` → `status` shows `diff: repairable`.
- **Root cause (hypothesis):** the diff artifact is `origin/main...HEAD` over the
  whole branch (`reviews/implementation.diff.patch`) and its recorded
  `git_head_sha`/`artifact_sha256` go stale on any new commit — but FF stage order
  puts `closeout`/`postmortem`/`STATUS` *after* `diff`, and those are commits. So
  diff can only be momentarily fresh. See `factory_stages.py` (`stage_repairable`,
  `diff_review_budget_state`, the head-mismatch logic) and
  `verify_review_checkpoint.py` (`artifact_hash_matches`).
- **Fix options:** (a) scope the diff to code paths only (exclude
  `docs/workflow/**`) so doc commits don't re-stale it; (b) treat diff as fresh when
  the only commits since its recorded head touch paths outside the diff scope;
  (c) allow `done` when diff was reviewed-clean and subsequent commits are
  docs-only. (a) or (b) is cleanest.

### 3. [MED] `[CHECKPOINT]` markers in `tasks.md` not detected
- **Symptom:** `checkpoint --stage diff` warned: *"tasks.md has no [CHECKPOINT]
  markers — diff review will cover the full branch."* The tasks file uses markers as
  headers, e.g. `### [CHECKPOINT] Slice 1 — C1 turn_clock (~40 LOC)`.
- **Why it matters:** without detection, per-slice diff scoping is impossible; you
  only get whole-branch diff review.
- **Fix:** find the marker parser (grep the scripts for `CHECKPOINT`) and confirm
  the expected format — likely it wants the marker on its own line or a specific
  prefix, not embedded in a `###` heading. Either relax the regex or document the
  exact required format in the tasks skill.

### 4. [MED, already fixed by #558 — verify the fix covers this path] telemetry recursion
- **Symptom (pre-rebase):** `checkpoint` (real dispatch path) crashed with
  `RecursionError: maximum recursion depth exceeded` in
  `scripts/factory_telemetry_commands.py` (`_run` → `_saved_subprocess_run()` →
  `_run` …). Triggered when the runner shelled out to a (missing) reviewer binary.
- **Status:** #558 ("Fix telemetry subprocess-patch recursion + loud missing-binary
  error") landed and the Claude-only path no longer hits it. Worth a regression test
  that the subprocess monkeypatch can't re-enter itself, and that a missing
  reviewer binary fails loudly (it now should).

### 5. [MED] Hand-authored review files are rejected unless they carry Codex/Gemini provenance
- **Symptom:** before discovering `prepare-claude-reviews`, I hand-wrote
  `spec.codex.*.review.md` / `spec.gemini.*.review.md`. `verify_review_checkpoint.py`
  rejected them: `REQUIRED_KEYS`/`NONEMPTY_KEYS` plus reviewer-specific rules — a
  `reviewer: codex` file needs `generation_method ∈ {codex-session, codex-runner}`;
  a `reviewer: gemini` file needs an existing `raw_output_path`.
- **Assessment:** this is *correct* (don't fake provenance) — the right answer is to
  use `prepare-claude-reviews` (which stamps `reviewer: claude` + proper metadata).
  The gap is **discoverability**: nothing pointed me there until the engine was
  updated. Fix = docs (see #6), not code.

### 6. [LOW] The Claude-only end-to-end recipe isn't documented in one place
- The `feature-review-claude` / `feature-implement-claude` skills describe the
  review/implement halves, but the full path — including the `closeout --pr-number`
  + MCP-PR workaround for the missing `gh`, and the "subagents must use `.venv`"
  gotcha — isn't written down. Add it to the FF skill so the next run doesn't
  rediscover it.

### 7. [LOW] Closeout review-coverage table double-counts superseded reviews
- `closeout.md` lists `codex:feasibility-adversarial` AND
  `claude:feasibility-adversarial` as "lenses run" for spec, because my early
  hand-written `codex`/`gemini` files were left on disk (later superseded by the
  `claude` ones, but `git rm`'d only for spec). The coverage scan globs all
  `*.review.md`. Minor: either prune superseded review files or have the scan honor
  the final manifest's reviewer set.

## Suggested fix order
1. #1 `deliver` no-`gh` path (unblocks the web path's final stage).
2. #2 diff-stage staleness (lets a Claude-only run actually reach `done`).
3. #3 `[CHECKPOINT]` parsing (enables per-slice diff review).
4. #6 docs (cheap, prevents rediscovery).
5. #4 regression test, #5 (docs), #7 (cosmetic).

## Pointers
- Runner subcommands: `scripts/run_factory.py` + `scripts/factory_cmd_*.py`.
- Stage health / repairable logic: `scripts/factory_stages.py`.
- Review file schema + verification: `review-lens/scripts/verify_review_checkpoint.py`,
  `verify_reconciliation.py`, `update_review_resolution.py`, `run_claude_review.py`.
- Telemetry monkeypatch: `scripts/factory_telemetry_commands.py`, `factory_telemetry.py`.
- This run's artifacts (working examples of every stage): this directory.
