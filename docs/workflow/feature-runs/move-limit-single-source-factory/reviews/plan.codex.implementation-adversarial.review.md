---
reviewer: "codex"
lens: "implementation-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/move-limit-single-source-factory/plan.md"
artifact_sha256: "7238d78add43022a37634164f5033561f73eaafed1cb546a1d03c85098056894"
repo_root: "."
git_head_sha: "f306a0bfb31b5b82cd49ce838231a9d8f47c428d"
git_base_ref: "origin/main"
git_base_sha: "f306a0bfb31b5b82cd49ce838231a9d8f47c428d"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "Round 4 = convergence confirmation; both findings are restatements of accepted decisions. MED (standalone _PROTOCOL prose still literal) = accepted by-design residual R3 reconciled in rounds 1-3: it's guidance text, not an enforced cap, can't drop a move. LOW (test reads schema metadata, not the live FastAPI route) noted: the route uses the same SubmitRequest/MessageRequest schema, so pinning the schema max_length to the source pins the route; a full route-level test is a reasonable optional add but out of scope for this non-drift refactor. No new defect; plan stays healthy/unchanged."
raw_output_path: "docs/workflow/feature-runs/move-limit-single-source-factory/reviews/plan.codex.implementation-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan implementation-adversarial

## Findings

1. MEDIUM [CODE-CONFIRMED] The plan leaves a second hard-coded copy of the cap text in the standalone connector path. `scripts/agentludum_connector.py` still falls back to a literal `_PROTOCOL` string when `app` is unavailable, and that string is what operators actually feed to models in standalone mode ([scripts/agentludum_connector.py](/private/tmp/wt-move-limit-single-source-factory/scripts/agentludum_connector.py#L54-L59), [scripts/agentludum_connector.py](/private/tmp/wt-move-limit-single-source-factory/scripts/agentludum_connector.py#L107-L115)). The proposed change only test-pins the numeric fallback constants, while the existing tests only assert the literal `"max 200 chars"` in the prompt text ([tests/test_connector_fallback.py](/private/tmp/wt-move-limit-single-source-factory/tests/test_connector_fallback.py#L607-L610), [tests/test_per_game_strategy.py](/private/tmp/wt-move-limit-single-source-factory/tests/test_per_game_strategy.py#L106-L114), [tests/test_agent_next_turn_fanout.py](/private/tmp/wt-move-limit-single-source-factory/tests/test_agent_next_turn_fanout.py#L255-L256)). That means the standalone prompt can drift independently of the new source of truth, so the plan does not actually eliminate duplication for every consumer.

2. LOW [UNVERIFIED] The server-side regression is described as checking four Pydantic field sites, but the plan only inspects model metadata. It does not exercise the actual FastAPI submit/message routes, so a future wiring bug that bypasses those schemas could still pass CI even though live validation is broken. The current code shows the enforcement lives in `SubmitRequest` and `MessageRequest` ([app/schemas/agent.py](/private/tmp/wt-move-limit-single-source-factory/app/schemas/agent.py#L277-L295)), but the proposed test strategy is still narrower than the runtime path.

## Residual Risks

- The connector still has to be updated on operator machines; CI can detect drift in the repo, but it cannot force a stale standalone copy to be refreshed.
- The Pydantic metadata-based helper remains version-coupled if it reads `model_fields[...]` internals directly.
- Existing tests that assert `"max 200 chars"` in prompt text will need coordinated updates if the cap ever changes, or they will fail loudly with churn.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: Round 4 = convergence confirmation; both findings are restatements of accepted decisions. MED (standalone _PROTOCOL prose still literal) = accepted by-design residual R3 reconciled in rounds 1-3: it's guidance text, not an enforced cap, can't drop a move. LOW (test reads schema metadata, not the live FastAPI route) noted: the route uses the same SubmitRequest/MessageRequest schema, so pinning the schema max_length to the source pins the route; a full route-level test is a reasonable optional add but out of scope for this non-drift refactor. No new defect; plan stays healthy/unchanged.
