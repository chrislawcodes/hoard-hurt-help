---
reviewer: "gemini"
lens: "testability-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/strategy-first-onboarding/plan.md"
artifact_sha256: "a2ba750064689303cbf1fdc349f62d950ef708519b49850e8690c9d8d0f342bf"
repo_root: "."
git_head_sha: "99c9abec482e7d75209b9ecf558e618a38b40474"
git_base_ref: "origin/main"
git_base_sha: "4723b62322a808d5a9c34d77e84e714d681d863e"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "r3: no new actionable beyond Codex; readiness/capacity/next verifications retained."
raw_output_path: "docs/workflow/feature-runs/strategy-first-onboarding/reviews/plan.gemini.testability-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan testability-adversarial

## Findings

1. **Stale/Stalling Race in Readiness Derivation (HIGH)**
   The plan relies on `provider_enabled_on_any_connection` to determine readiness without accounting for the actual connection health (liveness). If a user has enabled a provider, but the only connections enabling that provider are `paused` or have had their runner process die (stalled), the UI will show "set up" or "ready," potentially misleading the user. The plan explicitly defers liveness to the *existing* health badge, but in an onboarding flow, the agent's "readiness" to play should arguably be coupled to an active, reachable connection, not just a configuration toggle.
   [CODE-CONFIRMED] `app/engine/connection_health.py` separates `enabled_provider_values` from live health, but the UI logic in the plan proposes an "explicit needs-connecting branch" that might not distinguish between *configured-but-dead* and *configured-and-live*.

2. **`?next` Param Injection & Validation Bypass (MEDIUM)**
   The plan assumes `?next` survives validation failure via `POST` re-rendering. If the `create_agent` handler does not explicitly pass the query parameter back into the `new_agent_form` template context, the state will be lost on the first validation failure. The plan does not explicitly call for validation of the `next` URL's safety (e.g., ensuring it isn't an open redirect).
   [UNVERIFIED] Need to verify `app/routes/agents_create.py` implementation of `new_agent_form`.

3. **Inconsistent Short-Circuit Logic for `/me/connections` (MEDIUM)**
   The plan proposes changing the `is_live_now` short-circuit in `list_connections` to be provider-specific. This introduces a subtle UX discrepancy: if a user has multiple connections, some of which are live for provider A and others for provider B, the "auto-redirect" behavior will become non-deterministic or confusing based on which specific connection/provider is being targeted by the hint.
   [CODE-CONFIRMED] `app/routes/connections_pages.py` handles the redirection logic; a provider-specific check requires careful handling of the aggregate `is_live_now` state.

4. **N+1 Query Risk in Agent List (LOW)**
   The plan acknowledges an N+1 risk but proposes a "batched coverage query." It is unclear if the proposed batched query handles the `Match` count lookup efficiently or if it will rely on the existing `_count_agent_matches` which may remain N+1 if not refactored into a single group-by query.
   [CODE-CONFIRMED] `app/routes/agents_list.py` needs to move from per-agent call to a single aggregate query to truly mitigate the risk.

## Residual Risks

1. **State Transition Fragility:** The "needs-connecting" state is entirely derived. If `connection_providers` is updated concurrently with a page render, the user might see conflicting "ready" vs "needs-connecting" statuses.
2. **Backfill/Migration Hazards:** While the plan correctly notes no database migration, the reliance on derived state assumes the underlying `connection_providers` table accurately reflects user intent across all edge cases (e.g., user deletes all connections but leaves provider toggles enabled).
3. **Onboarding Loop Deadlock:** The transition from `Join` -> `Agents/New` -> `Connect` -> `Join` is complex. If the connection step is cancelled or interrupted, the `?next` parameter may be lost or stale, leaving the user stranded without a clear path back to the game they originally intended to join.

## Token Stats

- total_input=26130
- total_output=900
- total_tokens=52847
- `gemini-3.1-flash-lite`: input=26130, output=900, total=52847

## Resolution
- status: accepted
- note: r3: no new actionable beyond Codex; readiness/capacity/next verifications retained.
