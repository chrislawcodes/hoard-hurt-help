---
reviewer: "codex"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/agent-model-selection/spec.md"
artifact_sha256: "237b9035d232d1ec143534d03f801dc8ec92e2a4eedb386d2adaef9c6ff074be"
repo_root: "."
git_head_sha: "5bffc0c511e1f32e5602b31abee940a2df5c8173"
git_base_ref: "origin/main"
git_base_sha: "5bffc0c511e1f32e5602b31abee940a2df5c8173"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "Both findings addressed in spec: new 'Reporting channels' section + FR-005/006/009 require explicit down/up transport (not a reuse of report-pid); FR-003/004 keep server-sent model authoritative so connector default is last-resort only (drift risk closed)."
raw_output_path: "docs/workflow/feature-runs/agent-model-selection/reviews/spec.codex.feasibility-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

- HIGH [CODE-CONFIRMED] The spec’s fail-fast / fail-loud requirement is not feasible with the current reporting path as described. In `scripts/agentludum_connector.py`, the only existing self-report call is `_report_pid()`, and it sends just `pid`, `hostname`, and `detected_providers` to `/api/agent/report-pid` ([scripts/agentludum_connector.py](/Users/chrislaw/hoard-hurt-help--feat-agent-model-selection/scripts/agentludum_connector.py#L716)). When a model call fails, `_handle_turn()` logs the error locally and returns a fallback move, and `_move_request()` only tags that move with the boolean `is_connector_fallback` ([scripts/agentludum_connector.py](/Users/chrislaw/hoard-hurt-help--feat-agent-model-selection/scripts/agentludum_connector.py#L863), [scripts/agentludum_connector.py](/Users/chrislaw/hoard-hurt-help--feat-agent-model-selection/scripts/agentludum_connector.py#L883)). There is no field or endpoint here that can carry the actual failure reason back to the server, so FR-005/FR-009 need a concrete transport/schema change, not just “use the existing self-report channel.”

- MEDIUM [CODE-CONFIRMED] The spec assumes the server’s per-provider default model is the source of truth, but the connector still has hardcoded local defaults and will silently use them if the payload omits `model`. `_resolve()` falls back to `adapter.default_model` ([scripts/agentludum_connector.py](/Users/chrislaw/hoard-hurt-help--feat-agent-model-selection/scripts/agentludum_connector.py#L742)), and those defaults are hardcoded in each adapter (`claude-haiku-4-5`, `gpt-5.4-mini`, `gemini-3-flash-preview`) ([scripts/agentludum_connector.py](/Users/chrislaw/hoard-hurt-help--feat-agent-model-selection/scripts/agentludum_connector.py#L374), [scripts/agentludum_connector.py](/Users/chrislaw/hoard-hurt-help--feat-agent-model-selection/scripts/agentludum_connector.py#L408), [scripts/agentludum_connector.py](/Users/chrislaw/hoard-hurt-help--feat-agent-model-selection/scripts/agentludum_connector.py#L455)). `app/config.py` separately defines `PROVIDER_MODELS`, whose first entries are the intended defaults ([app/config.py](/Users/chrislaw/hoard-hurt-help--feat-agent-model-selection/app/config.py#L156)). If those two lists drift, a “provider default” seat can run a different model than the server intended.

## Residual Risks

- The spec does not pin down the server API shape for verification results, failure reasons, or caching keys, so the implementation could still end up with a weak or inconsistent status model.
- The provided code does not cover the UI surfaces in the spec, so the “checking / verified / failed” states and the read-only effective-model display remain unverified here.
- Model verification will need to avoid contaminating the live chained session state in `scripts/agentludum_connector.py`; that separation is not described in the spec and is easy to get wrong.
- Hermes and OpenClaw are one-shot adapters in the connector code, so any verification story for them will likely need a different path than the session-resume providers.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: Both findings addressed in spec: new 'Reporting channels' section + FR-005/006/009 require explicit down/up transport (not a reuse of report-pid); FR-003/004 keep server-sent model authoritative so connector default is last-resort only (drift risk closed).
