---
reviewer: "claude"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/agent-model-selection/spec.md"
artifact_sha256: "a28b01500f58ea9a9604f43969ad54b0de5495ea1ab503181317462303194c44"
repo_root: "."
git_head_sha: "ee8138143e38aef51b57796968d6bf2f5d5e3737"
git_base_ref: "origin/main"
git_base_sha: "ee8138143e38aef51b57796968d6bf2f5d5e3737"
generation_method: "claude-subagent"
resolution_status: "open"
resolution_note: ""
raw_output_path: "docs/workflow/feature-runs/agent-model-selection/reviews/spec.claude.feasibility-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

**1. [MEDIUM] [CODE-CONFIRMED] The connector is a single-threaded poll loop with a 300s idle nap, so the "~60s verification cadence" and "isolated-from-live-turns verification path" (FR-005, SC-001) need new connector-side concurrency the spec doesn't fully own.** `agentludum_connector.py:main()` is a single `while True:` loop that, on the no-game branch, sleeps `next_poll_after_seconds`, which the server sets to `POLL_WAITING_SECONDS = 300` in the idle/pre-match state SC-001 must cover (`app/engine/agent_idle.py`). With one thread and `time.sleep(300)` it cannot call a verification endpoint every ~60s, and FR-005's "must not consume a live-turn concurrency slot" can't be met by the connector's single `ThreadPoolExecutor(max_workers=_MAX_CONCURRENCY)` (used only for `executor.submit(_handle_turn, ...)`). Both force a new connector-side timer/thread that does not exist today. The spec states the requirements but presents them as if adding server endpoints suffices; the plan must explicitly add the connector concurrency, or a naive "verify on the idle continue" will silently miss SC-001.

**2. [LOW] [CODE-CONFIRMED] The up-channel rationale ("a missed-deadline turn never submits, so a reason on the submit body never leaves the machine") is only partly true.** `_decide` returns `None` (no POST) only when the deadline had already passed *before* the model was called — in that branch the model was never invoked, so there is no model-failure reason. The genuine model-failure path (hang/`TimeoutExpired`, non-zero exit, parse failure) hits the `except` and returns a fallback move that `_handle_turn` *does* POST with `is_connector_fallback=True`. So the submit body IS a viable carrier in the model-failure case. The up-channel is still independently justified (verification has no turn to ride on; a true timeout may leave no time to POST), but the plan should tighten the rationale rather than rely on "submit can never carry it."

## Residual Risks

- SC-001's ~60s target is gated by server pacing the spec doesn't change; unless verification is polled on its own clock (Finding 1), it rides the 300s idle nap.
- FR-009a classification matches against each CLI's free-form, version-varying stderr; the conservative default (unclassifiable → retryable) does the real safety work, and the sticky-failed path will need tuning + tests.
- Two-machines/one-connection last-writer-wins (Open decision 3) can flip a model's cached status each refresh and produce intermittent FR-014 warnings.
- FR-017 (deprecated model) cleanup has no defined trigger (migration / startup sweep / lazy-on-read); the plan must name it so orphaned `model_verifications` rows don't linger.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: open
- note: 