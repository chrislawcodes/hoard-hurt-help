# Reuse Audit — Onboarding / Auth Flow Consolidation

Read-only audit. Goal: the feature must consolidate onto two existing modules
(`connection_health.py`, `nav_context.py`) and add **no** new module. For each
capability the feature needs, the existing code that provides it, and whether to
reuse / extend / justify-new.

## Capability table

| capability | existing module (path:line) | verdict | note |
|---|---|---|---|
| 1. "Is this provider's MCP connection set up?" | `app/engine/connection_health.py:386` `provider_has_current_setup` (MCP-recent for MCP providers, else `provider_enabled_on_any_connection:306`); MCP-recency itself at `provider_has_recent_mcp_connection:340` | **reuse** | This *is* the `NO_MCP_CONNECTION` boundary (`not provider_has_current_setup`). Already #444's canonical "set up" predicate. Do not re-derive. |
| 2a. "Provider connected/seen now?" | `app/engine/connection_health.py:400` `provider_has_live_current_setup` (and `provider_is_covered:277` for non-MCP) | **reuse** | The `SEEN_NOT_POLLING` boundary. |
| 2b. "Is an AI actually polling now?" | `app/engine/connection_health.py:426` `provider_loop_running` (keys off `last_polled_at`, 120s window) | **reuse** | The `LIVE` boundary. `seat_hold.confirm_seat_if_live` and the join gate already key off exactly this. |
| 3. Per-provider readiness enum/state | **none** — readiness is only scattered booleans + the inline `live`/`offline`/`unconfigured` *strings* in `app/routes/web_player.py:268-273`. `ConnectionHealth` enum (`connection_health.py:52`) is a *per-connection machine* badge, not per-provider readiness. `AgentOnboardingState` (`app/engine/agent_onboarding.py:37`) is *in-game progress* (waiting→playing), not provider setup. | **justified-new** (`ProviderReadiness`) | No existing per-provider setup enum. The 4 states are the reconciliation of the three live predicates (2a/2b above + #1). Build it *over* those predicates; do not duplicate their query logic. ⚠ Name it distinctly from `AgentOnboardingState` to avoid collision. |
| 4. Onboarding funnel/ladder (no agent / no connection / connected) | `app/routes/nav_context.py:128` `compute_nav_cta`, over `user_has_agent:114`, `user_has_connected_agent:42`, `user_connection_count:72` | **extend** | Promote `compute_nav_cta` → `resolve_onboarding_state`; keep `compute_nav_cta` as a thin caller. `user_has_agent` reused as the `NEEDS_AGENT` test. ⚠ `user_has_connected_agent` uses `first_connected_at`-ever — the spec intentionally swaps the nav's "ready" bar to `provider_has_current_setup` (MCP-recent), so this helper is *replaced*, not reused, for the Play CTA. |
| 5. "First unmet step + where to redirect" + `?next=` threading | **hand-rolled in every entry point**: `web_player.py:170` `_join_setup_redirect`; `web_player.py:210-215` (handle), `:496-505` (connect-vs-connections); `agents_create.py:232-247` (post-create destination); `auth.py:119-130` (post-login); `web_games_catalog.py:79-89` (`/play`) | **justified-new** (`resolve_onboarding_state` signature) **+ consolidate** | This is the whole point of the feature. No shared resolver exists today; each site re-implements the ladder + URL. The resolver is new behavior on the *existing* `nav_context.py` module (extend the module, new function). Every listed site collapses into a call to it. |
| 6. Handle gate | `app/deps.py:56` `require_user_with_handle` (303 → `/me/handle?next=...`) | **reuse, do not move** | Spec §3 keeps `deps.py` owning the handle gate for `require_user_with_handle` routes. Resolver's `NEEDS_HANDLE` only covers bare `get_current_user` entry points (`/play`, `join_form`, post-login). Do **not** duplicate handle logic for routes already guarded by `deps.py`. |
| 7. Seat-hold confirm via "polling now" | `app/engine/seat_hold.py:42` `confirm_seat_if_live` → `provider_loop_running` | **reuse (keep bit-identical)** | Must resolve `LIVE` from the *same* `provider_loop_running` call the resolver uses (Risk: poller vs resolver drift). Spec wants it kept bit-identical; route both through the shared `LIVE` boundary. |
| 8. safe-internal-next / URL builders | `app/routes/web_support.py:29` `safe_internal_next`; `quote(..., safe="")` pattern repeated at `web_player.py:189,212,502,554,655`, `agents_create.py:246` | **reuse** | The resolver must call `safe_internal_next` for any caller-supplied `next`, and build `?next=` with the same `quote(x, safe="")` idiom. Do **not** add a second next-sanitizer. ⚠ Note `safe_internal_next:43` has a duplicated `raw.startswith("/")` check (`"//"` branch looks dead) — pre-existing, out of scope, flag only. |
| 9. Provider label/display helpers | `app/routes/provider_labels.py:8` `PROVIDER_LABELS`; wrapper `_provider_label` in `connections_setup.py` (used by create/list/detail/connections) | **reuse** | Whatever CTA copy the resolver emits should pull labels from `PROVIDER_LABELS`, not a new map. |
| 10. Test helpers/fixtures (users+connections+agents+providers) | `tests/factories.py` `make_user:16` / `make_connection:32` (auto-creates an enabled `ConnectionProviderRow`) / `make_agent:70` / `seat_player:175`; `tests/conftest.py` parallel `make_*`. Setup patterns: `tests/test_agent_detail_fixes.py:130` `_make_connection(last_seen_at=, first_connected_at=, mcp_connected_at=)`; `tests/test_agent_next_turn_fanout.py:1112` sets `mcp_connected_at`; `tests/test_smart_join_flow.py` (cookie/redirect harness, `_cookies`, `JOIN_URL`); `tests/test_coverage_health_and_join_gate.py` (predicate-level fixtures w/ live windows) | **reuse / extend** | New `provider_readiness` boundary tests should reuse `make_connection` + set `mcp_connected_at` / `last_polled_at` / `last_seen_at` exactly as `test_agent_detail_fixes.py` and `test_coverage_health_and_join_gate.py` already do. Redirect/`Location` + loop-guard tests reuse the `test_smart_join_flow.py` cookie+client harness. ⚠ `factories.make_connection` does not expose `mcp_connected_at`/`last_polled_at` kwargs — set them on the returned object (as existing tests do) or extend the factory; prefer setting on the object to avoid a factory change. |

### Extra rows the plan must reuse

| capability | existing module (path:line) | verdict | note |
|---|---|---|---|
| Join-form provider status strings (`live`/`offline`/`unconfigured`) | `app/routes/web_player.py:252-273` (inline dict) + `status_rank:291` | **extend → map from signal** | These three strings are a hand-rolled 3-state that is *almost* `ProviderReadiness` minus `SEEN_NOT_POLLING`. Derive them from `provider_readiness` (LIVE→live, CONNECTED_NOT_LIVE/SEEN_NOT_POLLING→offline, NO_MCP_CONNECTION→unconfigured), don't keep the inline check. |
| Agent-list readiness badge | `app/routes/agents_list.py:54` `enabled_provider_values_on_nonpaused_connections` + `agents_health_presenter.py:80` `_readiness_state` | **extend** | Set-level helper used as a readiness signal (site 13). Replace its readiness use with per-provider `provider_readiness`; `_readiness_state` presenter stays but reads the new signal. |
| Agent-detail readiness | `app/routes/agents_detail.py:138` `provider_is_covered` | **extend** | Swap the "seen" bar for `provider_readiness` (site 14). |
| Connections-page "live now / playing now" | `app/routes/connections_queries.py:202` `_live_status_context` (keys off `compute_connection_health` + `api_call_count`) + `connections_pages.py:132,137` `provider_has_live_current_setup`/`provider_has_current_setup` | **reuse the provider predicate; leave `_live_status_context` alone** | The page's auto-forward (`connections_pages.py:156-159,219-224`) keys off `provider_has_live_current_setup` — route that through `provider_readiness`'s `SEEN_NOT_POLLING` boundary so it matches the seat pages (closes the #444 loop). `_live_status_context`'s machine-level live/playing banner is a different, per-account concern — out of scope. |
| Post-login agent-count check | `app/routes/auth.py:121-129` (inline `count(Agent kind=AI)`) | **extend** | Same query `user_has_agent` (`nav_context.py:114`) already runs; replace the inline count with the resolver call (spec site 3). |

## Duplication risks

- **Re-deriving readiness in the enum.** `ProviderReadiness`/`provider_readiness`
  must be a thin wrapper over `provider_has_current_setup` (386),
  `provider_has_live_current_setup` (400), `provider_loop_running` (426). If it
  re-queries `connection_providers`/`mcp_connected_at` itself it becomes a 7th
  predicate — the exact failure the feature exists to kill.
- **A second next-sanitizer or URL builder.** `safe_internal_next`
  (`web_support.py:29`) + the `quote(x, safe="")` idiom already exist in 6 places.
  The resolver must call them, not reinvent.
- **A parallel onboarding state type.** `AgentOnboardingState`
  (`agent_onboarding.py:37`, waiting→playing) and `ConnectionHealth`
  (`connection_health.py:52`) already exist. New `ProviderReadiness` /
  `OnboardingStage` must be clearly named and scoped to *provider setup / gate
  ordering* so they don't get conflated with in-game progress or the machine badge.
- **Leaving the join-form `live/offline/unconfigured` strings hand-rolled.** They
  duplicate three of the four readiness states; map them from the signal.
- **Duplicating the handle gate.** `deps.require_user_with_handle` already
  redirects handle-less users; the resolver must *not* add a second handle check
  for routes that already depend on it (spec §3).
- **Drift between `confirm_seat_if_live` and the resolver's `LIVE`.** Both must
  resolve `LIVE` from one `provider_loop_running` result (Risk in spec).

## Justified-new (the genuinely new things)

- **`ProviderReadiness` enum + `provider_readiness()` (`connection_health.py`).**
  No per-provider setup enum exists today — readiness is scattered booleans and
  inline strings (`web_player.py:252-273`). The two adjacent enums
  (`ConnectionHealth`, `AgentOnboardingState`) answer different questions
  (per-connection machine badge; in-game progress). New, but built *over* the six
  existing predicates — not a new data path.
- **`OnboardingStage` (IntEnum) + `OnboardingState` + `resolve_onboarding_state()`
  (`nav_context.py`).** Promotion of the existing `compute_nav_cta` into a shared
  resolver. The *ladder logic* exists per-site (capability 5) but no shared
  function does it; the `require`-threshold + multi-agent-reduction + canonical
  `next_url` signature is genuinely new behavior. Lives on the existing module
  (extend), so still "no new module."

## Bottom line

Nothing here justifies a new module. The two genuinely-new symbols
(`ProviderReadiness`, `resolve_onboarding_state`) both land on the two existing
modules the spec names, and both must be thin wrappers over code that already
exists (the six `connection_health` predicates; `safe_internal_next`;
`PROVIDER_LABELS`; `user_has_agent`; `confirm_seat_if_live`). The main reuse
discipline is: build the enum over the predicates (don't add a 7th), route the
join-form strings and the connections-page auto-forward through the same signal,
and keep the handle gate in `deps.py`.
