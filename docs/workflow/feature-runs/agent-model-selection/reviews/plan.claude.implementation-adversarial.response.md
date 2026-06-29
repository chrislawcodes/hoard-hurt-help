## Findings

**1. [HIGH] [CODE-CONFIRMED] The down-channel worklist omitted the provider-default model — the model most seats actually run — so verification would be empty for nearly every operator.** `Agent.preferred_model` is nullable and NULL by default (just shipped in slice 1), so a worklist built only from non-NULL preferred values is empty in the common case, yet `resolve_seat_model` sends the provider default (`PROVIDER_MODELS[provider][0]`) for those seats. Contradicts FR-004, the arch doc, and SC-001. Fix: `compute_worklist` MUST include `default_model_for_provider(provider)` for every enabled non-empty-allowlist provider, plus the distinct non-NULL preferred values.

**2. [MEDIUM] [CODE-CONFIRMED] Connector idle cadence: a sibling tick at the top of the loop can't fire on ~60s because the idle branch sleeps up to ~300s below it.** Fix: cap the idle sleep at `min(next_poll_after_seconds, _VERIFY_INTERVAL)` and gate the tick with a pure predicate.

**3. [MEDIUM] [CODE-CONFIRMED] Slice 3 conflates three `_decide` branches.** Only the real model-subprocess failure carries a reason and should flip the cache; deadline-passed (no submit) and buffer-fallback (no model called) must not be reported as model failures. The reason POST must be best-effort (try/except `httpx.HTTPError`) like `_report_pid`.

**4. [MEDIUM] [CODE-CONFIRMED] Per-model status badge needs a provider, but `Agent` has none.** Derive it from the model via `provider_for_model(model)`; define behavior for NULL preferred (default case).

**5. [LOW] [CODE-CONFIRMED] New route must be `include_router`'d in `main.py` with the prefix baked into its own `APIRouter`, not double-prefixed.**

**6. [LOW] Plan↔reuse-report module-placement divergence (separate `agent_model_verification.py` vs sibling in `agent_next_turn.py`) — defensible (arch doc backs it); keep `require_connection` reuse verbatim.**

## Residual Risks

- Worklist 6h-staleness must union with currently-referenced models only (deprecated models not re-queued).
- Verify call must run off the `_MAX_CONCURRENCY` turn pool (FR-005); reuse `_run`'s capture but a dedicated timeout.
- `model_status_for` union must reuse existing liveness predicates, not hand-roll.
- `sanitize_error` must run server-side on store so a forgetful connector can't persist a raw path/token.
