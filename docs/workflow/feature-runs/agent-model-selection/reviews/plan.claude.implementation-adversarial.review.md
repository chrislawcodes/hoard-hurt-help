---
reviewer: "claude"
lens: "implementation-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/agent-model-selection/plan.md"
artifact_sha256: "a8277cafd8898cb43c53be4dd4dab86c93fe2cfdeb3fc95bd42923e2f111664c"
repo_root: "."
git_head_sha: "ee8138143e38aef51b57796968d6bf2f5d5e3737"
git_base_ref: "origin/main"
git_base_sha: "ee8138143e38aef51b57796968d6bf2f5d5e3737"
generation_method: "claude-subagent"
resolution_status: "open"
resolution_note: ""
raw_output_path: "docs/workflow/feature-runs/agent-model-selection/reviews/plan.claude.implementation-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan implementation-adversarial

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

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: open
- note: 