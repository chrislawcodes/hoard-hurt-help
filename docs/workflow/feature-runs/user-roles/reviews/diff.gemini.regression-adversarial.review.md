---
reviewer: "gemini"
lens: "regression-adversarial"
stage: "diff"
artifact_path: "docs/workflow/feature-runs/user-roles/reviews/implementation.diff.patch"
artifact_sha256: "435a1205372d6da1d993327c69891a211b83a2c3fbe483466ac77f496edd8a10"
repo_root: "."
git_head_sha: "a69a49ab34b8250d210130ce95f09ef2a54d718a"
git_base_ref: "5a44eea2e004e72e2cfa0b2a8a37f6f16e45a9bf"
git_base_sha: "5a44eea2e004e72e2cfa0b2a8a37f6f16e45a9bf"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "Slice 4 (user routes + read model + cancel). All findings non-actionable. F1(cap race) accept-as-documented: the explicitly-accepted best-effort cap (spec Risks: limit+1 worst case, no data harm; admins exempt). F2('SQL injection'/date) rejected-misframe: SQLAlchemy ORM parameterizes (no injection); scheduled_start IS validated (try/except ValueError -> friendly error, tzinfo + future check) and re-validated inside create_match. F3(cancel TOCTOU) accept: tiny window; cancel_match is idempotent (state->CANCELLED either way), no data harm. Residuals (500 on commit failure / non-JS date) handled: route try/excepts ValueError to a friendly error; an unrecoverable commit error surfacing as 500 is intended fail-loud (CLAUDE.md). Completed the slice: added the missing cancel route + dashboard retarget + cancel test matrix. Preflight green (664 tests). Expansion workaround applied."
raw_output_path: "docs/workflow/feature-runs/user-roles/reviews/diff.gemini.regression-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: diff regression-adversarial

## Findings

1.  **Severity: HIGH. Potential Race Condition/Inconsistent State in `create_match_submit`.**
    The check for `active_count` (match limits) is performed *after* the `scheduled_start` date validation but *before* the call to `create_match`. Because this is not an atomic operation (the check and the create are separate DB calls), a user could bypass `settings.user_active_match_limit` by submitting multiple requests in rapid succession, resulting in a number of active matches exceeding the intended threshold.

2.  **Severity: MEDIUM. [UNVERIFIED] Potential SQL Injection/Type Manipulation.**
    The `scheduled_start` field is passed via `Form()` as a string and converted using `datetime.fromisoformat(scheduled_start.replace("Z", "+00:00"))`. While basic validation is present, relying on client-side input for date parsing without explicit schema validation (e.g., Pydantic) is fragile. If the underlying `app.engine.match_creation.create_match` does not strictly enforce type safety on the `scheduled_start` argument, there is a risk of malformed data persistence.

3.  **Severity: LOW. Inconsistent State Handling in `cancel_match_submit`.**
    The code explicitly rejects already-ended matches (COMPLETED/CANCELLED) with `HTTP 409 Conflict`, but relies on `await cancel_match(db, match)` to perform the action. If `cancel_match` implementation is not idempotent or does not perform its own state check, and another concurrent request modifies the match state between the `_load_match_or_404` check and the `cancel_match` call, the system could enter an undefined state.

## Residual Risks

*   **Reliance on Implicit Dependencies:** The code relies on `app.routes.web_support` and `app.engine` modules. Failure modes within these imported utilities are not handled locally (e.g., if `create_match` raises an exception during a database commit), potentially leading to unhandled 500 errors.
*   **HTML/JS Coupling:** The `create_match` form relies on client-side JavaScript to convert the local time to ISO format before submission. If a user bypasses the browser or runs a client that does not execute JS, the form could submit invalid data formats to the server. The server code should implement stricter validation on the raw form input beyond the current `try/except` block.

## Token Stats

- total_input=18529
- total_output=538
- total_tokens=19067
- `gemini-3.1-flash-lite`: input=18529, output=538, total=19067

## Resolution
- status: accepted
- note: Slice 4 (user routes + read model + cancel). All findings non-actionable. F1(cap race) accept-as-documented: the explicitly-accepted best-effort cap (spec Risks: limit+1 worst case, no data harm; admins exempt). F2('SQL injection'/date) rejected-misframe: SQLAlchemy ORM parameterizes (no injection); scheduled_start IS validated (try/except ValueError -> friendly error, tzinfo + future check) and re-validated inside create_match. F3(cancel TOCTOU) accept: tiny window; cancel_match is idempotent (state->CANCELLED either way), no data harm. Residuals (500 on commit failure / non-JS date) handled: route try/excepts ValueError to a friendly error; an unrecoverable commit error surfacing as 500 is intended fail-loud (CLAUDE.md). Completed the slice: added the missing cancel route + dashboard retarget + cancel test matrix. Preflight green (664 tests). Expansion workaround applied.
