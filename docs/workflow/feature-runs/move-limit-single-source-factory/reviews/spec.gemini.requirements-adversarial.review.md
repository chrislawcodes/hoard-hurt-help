---
reviewer: "gemini"
lens: "requirements-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/move-limit-single-source-factory/spec.md"
artifact_sha256: "58659e7a23c295d2a5711b026b25a42911a9c0c0f921cd610f6cdf15fc6d819e"
repo_root: "."
git_head_sha: "f306a0bfb31b5b82cd49ce838231a9d8f47c428d"
git_base_ref: "origin/main"
git_base_sha: "f306a0bfb31b5b82cd49ce838231a9d8f47c428d"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "All three findings describe the CURRENT pre-refactor state (the drift problem) and confirm the spec's diagnosis; they raise no flaw in the spec. The plan resolves all three: a single source of truth (MESSAGE_MAX_LENGTH/THINKING_MAX_LENGTH in app/agent_prompt.py), server schemas derive from it, the connector uses a fallback constant test-pinned to it, and a new regression test enforces parity. No spec change required."
raw_output_path: "docs/workflow/feature-runs/move-limit-single-source-factory/reviews/spec.gemini.requirements-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec requirements-adversarial

## Findings

1.  **Hard-Coded Limit Fragmentation (HIGH Severity):** The move limits (200 characters) are inconsistently defined across server schemas, connector logic, and LLM-facing prompt text. This confirms that the current state is prone to drift, as the server and connector maintain separate, non-synchronized definitions of these boundaries.
    *   **Evidence:** `app/schemas/agent.py` uses `max_length=200` in `Field(...)`. `scripts/agentludum_connector.py` uses hard-coded `200` in `_clip()`. `app/agent_prompt.py` and `scripts/agentludum_connector.py` contain literal "max 200 chars" prompt text. [CODE-CONFIRMED]

2.  **Lack of Centralized Invariant (HIGH Severity):** There is no single-source-of-truth constant or validation test that links the connector's clipping logic to the server's input validation logic.
    *   **Evidence:** The codebase lacks a shared `CONSTANTS` module or shared configuration that is importable by both `app/` and the standalone scripts. [CODE-CONFIRMED]

3.  **Standalone Connector Fragility (MEDIUM Severity):** The connector correctly uses a `try/except ImportError` pattern for the protocol, but the limit constants are currently hard-coded literals. This necessitates manual updates across files and precludes automated parity verification between the server-side validator and the connector's clipping mechanism.
    *   **Evidence:** `scripts/agentludum_connector.py` defines `_normalize_move` and `_move_request` with hard-coded `200` literals, independent of any potential `app/` import. [CODE-CONFIRMED]

## Residual Risks

1.  **Deployment Synchronization:** While the proposed architectural refactor (using a centralized constant with an `ImportError` fallback) addresses the "drift" risk for future code edits, it does not solve the fundamental challenge of synchronizing the "live" connector code on operator machines. If an operator is running an outdated version of `agentludum_connector.py` that still uses a hard-coded 200-character limit, but the server is updated to a new limit (e.g., 250), the connector will continue to clip moves incorrectly, resulting in "correct" but potentially truncated or suboptimal interaction until the operator manually refreshes their connector.

2.  **Prompt Drift:** As noted in the artifact, the LLM-facing instructions (`RESPONSE_PROTOCOL`) contain literal "max 200 chars" text. This remains a residual risk even after centralizing the programmatic limits in Python code. If the code limit is updated to 250, but the prompt string is not updated, the LLM will receive contradictory instructions, which may lead to decreased adherence to the new limit.

## Token Stats

- total_input=40216
- total_output=625
- total_tokens=40841
- `gemini-3.1-flash-lite`: input=40216, output=625, total=40841

## Resolution
- status: accepted
- note: All three findings describe the CURRENT pre-refactor state (the drift problem) and confirm the spec's diagnosis; they raise no flaw in the spec. The plan resolves all three: a single source of truth (MESSAGE_MAX_LENGTH/THINKING_MAX_LENGTH in app/agent_prompt.py), server schemas derive from it, the connector uses a fallback constant test-pinned to it, and a new regression test enforces parity. No spec change required.
