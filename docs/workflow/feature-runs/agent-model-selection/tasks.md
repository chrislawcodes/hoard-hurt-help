# Tasks

Executable, checkpoint-bounded slices for agent-model-selection slices 2–4 (slice 1 merged in #572). Each `[CHECKPOINT]` is its own implement→preflight→diff-review boundary, ≤ ~300 changed lines. Order is dependency order. Preflight (`.venv/bin/ruff check .` && `.venv/bin/mypy app/ mcp_server/` && `.venv/bin/pytest -q`) must be green at every checkpoint.

## [CHECKPOINT] Slice 2a — verification store + engine skeleton
- Add `app/models/model_verification.py`: `ModelVerification` keyed `(connection_id, provider, model)` — `status` (enum: unknown/checking/verified/failed/timeout), `error_text` (String ≤300), `consecutive_timeouts` (Integer, default 0), `checked_at` (DateTime). Register in `app/models/__init__.py`.
- Migration `0045_model_verifications`: `create_table` upgrade + `drop_table` downgrade (round-trip test gate).
- Add `app/engine/model_verification.py`: `sanitize_error(text)` (≤300, strip abs paths + `sk_…`/bearer tokens); `record_results(db, connection, results)` (upsert + FR-009a outcome→status mapping + FR-013 `consecutive_timeouts`→failed escalation at 3); `model_status_for(db, user, provider, model)` (union over live machine connections, reusing existing liveness predicates; any `verified` ⇒ runnable; exclude MCP/paused).
- Tests: `sanitize_error` (path/token stripping, truncation); FR-013 escalation boundary (2→timeout, 3→failed); `model_status_for` union (verified+failed ⇒ runnable; MCP/paused excluded).
- Depends on: nothing (current main).

## [CHECKPOINT] Slice 2b — server channels (down worklist + up report)
- `app/routes/agent_model_verification.py`: `APIRouter(prefix="/api/agent")`, connection-authed like `agent_next_turn`. GET `/model-worklist` → `compute_worklist`; POST `/model-verification` → `record_results` (+ play-time reason field accepted, used by slice 3).
- `compute_worklist(db, connection)` in `app/engine/model_verification.py`: for each enabled non-empty-allowlist provider → `default_model_for_provider(provider)` + distinct non-NULL `Agent.preferred_model` for that provider across the user's agents + still-referenced stale (>6h) rows.
- Wire: import + `include_router` in `app/main.py` (no second prefix).
- Tests: worklist includes the default for a NULL-preferred agent (the HIGH); empty-allowlist excluded; deprecated/unreferenced model not re-queued; upsert + server-side sanitize; connection auth required.
- Depends on: 2a.

## [CHECKPOINT] Slice 2c — connector verify loop + shared classifier
- `scripts/agentludum_connector.py`: `_VERIFY_INTERVAL = 60`; pure `_should_verify(now, last_verify)`; cap idle sleep `min(next_poll_after_seconds, _VERIFY_INTERVAL)`; verify tick = GET worklist → per (provider,model) isolated cheap test call (dedicated helper reusing `_run` capture, ~30s, off the turn executor) → classify → best-effort POST.
- Shared classifier `classify_cli_outcome(exit_code, stderr) -> outcome` (one source of truth; also used server-side by `record_results` mapping). FR-005 success predicate + FR-009a mapping.
- Synthetic stderr fixtures (claude 404, codex unknown-model, gemini synthetic).
- Tests: `_should_verify` cadence (pure, no server); classifier parametrized table (asserted by both connector + server).
- Depends on: 2b.

## [CHECKPOINT] Slice 3 — fail-loud at play time
- In `_decide`/`_handle_turn`: distinguish the three branches — deadline-passed (no submit → reason on up-channel only), buffer-fallback (no model called → NOT a model failure, no cache flip), real model-subprocess failure (carries `reason`, flips cache). Best-effort POST of the reason (try/except `httpx.HTTPError`).
- Surface the reason + tagged fallback on connection/agent status.
- Tests: each branch's cache effect; play-time reason flips the right `(conn,provider,model)` row to failed/timeout end-to-end.
- Depends on: 2b (up-channel), 2c (classifier).

## [CHECKPOINT] Slice 4a — agent-settings preferred-model picker
- Agent settings/detail route + template: advanced "Preferred model" `<select>` (options from `PROVIDER_MODELS`, grouped by provider, "Provider default" = none) → writes `Agent.preferred_model`; labeled "machine connections only; ignored by MCP."
- Tests: POST sets/clears `preferred_model`; only `PROVIDER_MODELS` values accepted; join page unchanged.
- Depends on: nothing hard (can run after 2a); UI status in 4b.

## [CHECKPOINT] Slice 4b — status display + effective-model + join warning
- Per-model status badge HTMX fragment (provider derived via `provider_for_model(model)`); read-only effective-model line (FR-010; empty-allowlist machine seat → "runs your <provider> config's model"); FR-014 join warning (warn only when verified-failing on every live machine connection for the provider; silent on unknown/checking).
- Tests: badge for matched + NULL-preferred; effective-model for empty-allowlist seat; join warns only on verified-failing-everywhere.
- Depends on: 2a/2b (status store + read), 4a (picker present).

## Parallelism
- 4a is independent of 2b/2c/3 (it only needs `Agent.preferred_model`, already shipped) and could run in parallel with the 2x slices. The rest are sequential by dependency. Implementation here runs serially for simplicity.
