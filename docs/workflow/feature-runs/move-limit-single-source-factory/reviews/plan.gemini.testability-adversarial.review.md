---
reviewer: "gemini"
lens: "testability-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/move-limit-single-source-factory/plan.md"
artifact_sha256: "7238d78add43022a37634164f5033561f73eaafed1cb546a1d03c85098056894"
repo_root: "."
git_head_sha: "f306a0bfb31b5b82cd49ce838231a9d8f47c428d"
git_base_ref: "origin/main"
git_base_sha: "f306a0bfb31b5b82cd49ce838231a9d8f47c428d"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "Round 4 = convergence. #1 (fixture might mask fallback): the new test_connector_loads_with_app_unimportable explicitly blocks 'app' import and asserts _CANONICAL_PROTOCOL is None, proving the fallback branch ran; plus the parity test reads _FALLBACK_* by name (defined unconditionally before the try), so it can't be masked. #2 (DB/other layers): verified Text/unbounded, no other enforcement layer; documented. #3 (_clip edge cases): _clip is exercised end-to-end by the new live-clip tests; a dedicated _clip unit test is a nice-to-have but out of scope - the enforcement guarantee is already covered. No new defect; plan unchanged."
raw_output_path: "docs/workflow/feature-runs/move-limit-single-source-factory/reviews/plan.gemini.testability-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan testability-adversarial

## Findings

1. **Connector fallback parity test is susceptible to false-positive masking.** The plan relies on `test_connector_fallback_matches_server` to compare the connector's internal `_FALLBACK_` constants to the server's source of truth. However, if the `connector` fixture or the test environment automatically triggers an `import app` before the test reaches the parity check, the test might inadvertently load the resolved values rather than the uninitialized fallback values. This could mask a discrepancy between what an operator's machine *actually* uses (the static fallback) and the server's truth.
   [UNVERIFIED] — Requires confirmation that the `connector` fixture doesn't perform implicit imports.

2. **Incomplete enforcement at the `schemas` layer.** While the plan correctly identifies 4 `max_length` sites in `SubmitRequest` and `MessageRequest`, it does not address potential validation at the database-facing layer or downstream consumer models (if any exist beyond the Pydantic schemas). If a new route or internal tool is added that interacts directly with these models, the Pydantic validation could be bypassed, leading to inconsistent enforcement across the system.
   [CODE-CONFIRMED] — `app/models/turn.py` uses `Text` for `message` and `thinking`, confirming no database-level constraint exists to catch over-limit input.

3. **Inadequate regression testing for the `_clip` helper.** The plan includes tests for `_normalize_move` and `_move_request`, but the `_clip` helper function in `scripts/agentludum_connector.py` appears to be a direct target for unit testing that is currently only being tested indirectly. Given that this helper is the primary mechanism for enforcing the limits on the connector side, an adversarial approach suggests it should have a dedicated unit test to ensure it handles edge cases (e.g., zero-length strings, non-string inputs, or multi-byte characters) correctly.
   [CODE-CONFIRMED] — `scripts/agentludum_connector.py` L184 contains `_clip(text, limit)`.

## Residual Risks

1. **"Magic 200" leakage via future code paths.** Despite replacing the literal `200` in the specified locations, the system lacks a declarative mechanism (e.g., a static analysis rule or a custom Pydantic constraint) to prevent developers from hard-coding the same value elsewhere in the future. The dependency on manual grep to catch regressions is a weak human-centric safeguard in an automated pipeline.

2. **Stale fallback guidance text.** The connector's embedded `_PROTOCOL` fallback string is not derived from the new constants. While the plan argues this is acceptable for guidance, it creates a maintenance hazard: if the enforced `_FALLBACK_*` constants are changed in the future, the fallback guidance string (used in standalone mode) might still suggest a "max 200 chars" limit, potentially confusing the LLM/operator even if the code behaves correctly.

3. **Implicit Dependency Fragility.** The strategy of loading the connector as a module using `importlib.util.spec_from_file_location` in tests is powerful but relies heavily on the environment configuration matching the production runtime. Any discrepancy (e.g., missing dependencies, different Python version, or relative path issues) could result in tests that are less indicative of the actual connector's behavior in the operator's environment.

## Token Stats

- total_input=25918
- total_output=850
- total_tokens=50567
- `gemini-3.1-flash-lite`: input=25918, output=850, total=50567

## Resolution
- status: accepted
- note: Round 4 = convergence. #1 (fixture might mask fallback): the new test_connector_loads_with_app_unimportable explicitly blocks 'app' import and asserts _CANONICAL_PROTOCOL is None, proving the fallback branch ran; plus the parity test reads _FALLBACK_* by name (defined unconditionally before the try), so it can't be masked. #2 (DB/other layers): verified Text/unbounded, no other enforcement layer; documented. #3 (_clip edge cases): _clip is exercised end-to-end by the new live-clip tests; a dedicated _clip unit test is a nice-to-have but out of scope - the enforcement guarantee is already covered. No new defect; plan unchanged.
