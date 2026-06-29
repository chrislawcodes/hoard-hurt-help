Review this plan artifact using a implementation-adversarial lens.
Stay scoped to that lens.
Approach the artifact adversarially: look for hidden flaws, omitted cases, and weak assumptions before giving credit.
No code context files were provided. Flag any finding that depends on an assumption about the existing codebase as [UNVERIFIED] and limit it to MEDIUM severity or lower.
The full review artifact text is included below in this prompt.
Return markdown using exactly these sections:
## Findings
## Residual Risks
Keep the response concrete and ordered by severity.

Artifact: plan.md
# Plan

## Scope

Builds slices 2–4 of agent-model-selection on top of current main (slice 1 — `Agent.preferred_model` + `resolve_seat_model` — is merged, #572). Covers: connector model **verification** (the fail-fast core), **fail-loud** at play time, and the agent-settings **UI**. Addresses spec FR-005–FR-018.

## Architecture decisions (driven by reuse-report.md)

1. **New `model_verifications` store (justified-new).** A table keyed `(connection_id, provider, model)` with: `status` (`unknown`/`checking`/`verified`/`failed`/`timeout`), `error_text` (≤300 chars, sanitized), `consecutive_timeouts` (int), `checked_at`. The `connection_providers` row is unique per `(connection, provider)` and physically can't hold per-model rows, so this is a new table. New model `app/models/model_verification.py`; migration `0045` (plain `add`/`create_table`, SQLite-safe; batch only for any drop).
2. **Two dedicated endpoints (justified-new), shaped like `report_pid` (extend the pattern).** Both connection-authed (`X-Connection-Key`), in a new `app/routes/agent_model_verification.py`:
   - **GET `/api/agent/model-worklist`** (down): returns the `(provider, model)` set to verify for this connection = union of the connection's enabled providers × the distinct `Agent.preferred_model` values across the user's agents for that provider, plus any cached row older than the 6h refresh (FR-016). Excludes empty-allowlist providers.
   - **POST `/api/agent/model-verification`** (up): body = list of `{provider, model, outcome, error_text}`; upserts the cache (server sanitizes/truncates error_text via the new helper). Also carries the **play-time failure report** `{match_id, provider, model, reason}` (FR-009) so the reason reaches status without riding the submit body.
3. **Server-side worklist + cache logic** in a new engine module `app/engine/model_verification.py` (mirrors `provider_readiness`/`connection_health` shape): `compute_worklist(db, connection)`, `record_results(db, connection, results)`, `model_status_for(db, user, provider, model)` (the union read for the UI + join guard), and `sanitize_error(text)`.
4. **Connector verification side-task (extend the existing clock-gate).** Add a `_VERIFY_INTERVAL = 60` sibling to `_DETECT_REPORT_INTERVAL` in `scripts/agentludum_connector.py`: each tick, GET the worklist, run a cheap isolated test call per `(provider, model)` (`<cli> --model <m> --print "ok"` style, ~30s timeout, run directly via a dedicated helper — NOT the shared `_MAX_CONCURRENCY` turn executor), classify (FR-005 success predicate + FR-009a mapping), POST results. The classification helper is shared with the play-time path.
5. **Per-model status UI (mirror ProviderReadiness pattern, new axis).** `model_status_for` → a small status enum → badge map → an HTMX fragment on the agent-settings page. No change to the provider-readiness enum.
6. **Agent-settings preferred-model picker (surface over shipped column).** Extend `app/routes/agents_*` (the agent settings/detail route + template) with an advanced "Preferred model" `<select>` (options from `PROVIDER_MODELS`, grouped by provider) writing `Agent.preferred_model`, plus the per-model status badge and the read-only effective-model line. Join page untouched (FR-011).
7. **Join-time warning (FR-014)** in the existing join flow: read `model_status_for` over the user's live machine connections; warn (not block) only when verified-failing-everywhere. Reuse the join page's existing error/notice surface.
8. **Error sanitization helper (small justified-new):** `sanitize_error(text)` in the engine module — truncate to 300, strip absolute paths and `sk_…`/bearer-token-shaped substrings.

## Implementation slices (checkpoint-bounded; each ≤ ~300 lines)

- **[CHECKPOINT] Slice 2a — verification store.** `model_verification.py` model + migration 0045 + the engine module skeleton (`sanitize_error`, status enum, `record_results`, `model_status_for`) with unit tests. (~180 lines)
- **[CHECKPOINT] Slice 2b — server channels.** The down `model-worklist` + up `model-verification` endpoints + `compute_worklist` + wiring into the app + tests (worklist union, upsert, sanitize, 6h staleness). (~220 lines)
- **[CHECKPOINT] Slice 2c — connector verification loop.** `_VERIFY_INTERVAL` side-task + isolated test-call helper + FR-005/FR-009a classification + POST results. Connector-only; tested via the classification helper unit tests (the loop itself is integration-light). (~160 lines)
- **[CHECKPOINT] Slice 3 — fail-loud play time.** Route the play-time failure reason on the up-channel from `_handle_turn`/`_decide`; surface the reason + tagged fallback on connection/agent status; flip cached status per FR-009/FR-009a. (~140 lines)
- **[CHECKPOINT] Slice 4a — settings picker.** Agent-settings advanced preferred-model `<select>` + route handler writing `Agent.preferred_model` + tests. (~150 lines)
- **[CHECKPOINT] Slice 4b — status + effective-model display + join warning.** Per-model status badge fragment, read-only effective-model line (FR-010, incl. empty-allowlist "runs your <provider> config's model"), and the FR-014 join warning. (~200 lines)

## Testing

- Engine unit tests: `sanitize_error`, status union (`model_status_for`), worklist computation, FR-009a classification mapping (each stderr/exit class → failed/timeout), 6h staleness.
- Route tests: worklist endpoint (auth, union, empty-allowlist excluded), report endpoint (upsert, sanitize), join warning (warns only on verified-failing-everywhere; silent on unknown/checking).
- Reuse the in-memory SQLite test DB + existing factories; mock CLI calls for the connector classification tests. Preflight (`.venv/bin/ruff` + `mypy` + `pytest`) green at every checkpoint.

## Residual Risks

- **Connector single-thread vs ~60s cadence.** The idle loop sleeps up to 300s, so a naive hook misses SC-001. verification: after slice 2c, run the connector against a local server with a preferred model set and confirm a verification result is POSTed within ~90s (grep the connector log for the verify POST + check the cache row) — if not, the side-task isn't on its own clock.
- **FR-009a stderr classification is brittle across CLI versions.** verification: unit-test the classifier against captured real stderr samples for each CLI's model-unavailable error (claude 404, codex unknown-model, gemini); confirm each maps to `failed` and a `TimeoutExpired`/`FileNotFoundError` maps to `timeout`.
- **Worklist could grow unbounded for an operator who cycles many models.** verification: assert in a test that `compute_worklist` returns only models currently referenced by a live agent's `preferred_model` (plus stale-verified), not every historical value; cap at a sane number and log if truncated.
- **Empty-allowlist machine seat display has no real model name.** verification: render the agent-settings effective-model line for a hermes machine seat in a route test and assert it shows "runs your hermes config's model" (not blank, not a fake model id).
- **Multi-machine last-writer-wins on the cache (Open decision 3).** verification: documented as accepted; add a test that `model_status_for`'s union treats any `verified` row as runnable so a single failing machine doesn't alone trip the join warning.


Return only markdown with exactly these sections:
## Findings
## Residual Risks
Do not include any other sections.