---
reviewer: "codex"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/move-limit-single-source-factory/spec.md"
artifact_sha256: "58659e7a23c295d2a5711b026b25a42911a9c0c0f921cd610f6cdf15fc6d819e"
repo_root: "."
git_head_sha: "f306a0bfb31b5b82cd49ce838231a9d8f47c428d"
git_base_ref: "origin/main"
git_base_sha: "f306a0bfb31b5b82cd49ce838231a9d8f47c428d"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "Acknowledged but deliberately scoped out (FR5). The prompt strings ('max 200 chars') are LLM/operator GUIDANCE text, not an enforced cap: if they drift, the model just gets slightly stale advice — the server still clips/validates at the real cap and no move is silently dropped. The drift path this feature kills is the ENFORCED one (schema max_length vs connector clip), which both become derived/test-pinned. Templating the prose into the constants is recorded as a known-remaining literal in FR5 and an explicit non-goal for this slice to keep the diff minimal and the invariant focused; it can be a cheap follow-up. No spec change required — FR5 already states this decision and rationale."
raw_output_path: "docs/workflow/feature-runs/move-limit-single-source-factory/reviews/spec.codex.feasibility-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

1. Medium severity [CODE-CONFIRMED]: The spec does not fully make the move-limit contract single-sourced because it explicitly exempts the LLM-facing prompt copies from the invariant. The code still hard-codes `max 200 chars` in `[app/agent_prompt.py](/private/tmp/wt-move-limit-single-source-factory/app/agent_prompt.py#L7)` and in the standalone fallback inside `[scripts/agentludum_connector.py](/private/tmp/wt-move-limit-single-source-factory/scripts/agentludum_connector.py#L107)`. So a future cap change can still leave the prompt stale even if the schema constants and regression test pass, which recreates the drift path this feature is supposed to remove.

## Residual Risks

1. The spec only guards the known enforcement sites. If another cap consumer is added later outside the listed files, this design does not automatically catch it.
2. The standalone connector is shipped verbatim from `scripts/` and served by the web layer, so a live operator machine can still run an old copy until it is refreshed. The spec does not define any rollout or auto-update mechanism.
3. The prompt guidance strings stay literal by design, so they will still be wrong if the cap ever changes in a later feature.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: Acknowledged but deliberately scoped out (FR5). The prompt strings ('max 200 chars') are LLM/operator GUIDANCE text, not an enforced cap: if they drift, the model just gets slightly stale advice — the server still clips/validates at the real cap and no move is silently dropped. The drift path this feature kills is the ENFORCED one (schema max_length vs connector clip), which both become derived/test-pinned. Templating the prose into the constants is recorded as a known-remaining literal in FR5 and an explicit non-goal for this slice to keep the diff minimal and the invariant focused; it can be a cheap follow-up. No spec change required — FR5 already states this decision and rationale.
