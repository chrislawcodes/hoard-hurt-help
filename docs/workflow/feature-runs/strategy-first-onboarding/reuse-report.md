# Reuse audit — strategy-first-onboarding

Adversarial check: prefer reuse/extend; justify any new module. This feature
re-orders existing onboarding screens and loosens one gate — there is **no new
module**. It is all modifications + extensions of existing routes, helpers, and
templates. No database migration (readiness is derived).

| Capability | Existing module (path) | Verdict | Note |
|---|---|---|---|
| Agent-create gate (POST) | `app/routes/agents_create.py` (`create_agent_or_connection`) | **modify** | Remove the `enabled_provider_values` gate that redirects to `/me/connections`; let creation succeed with no connection (FR-001). |
| Agent-create form (GET) | `app/routes/agents_create.py` (`new_agent_form`) + `app/templates/agents/new.html` | **modify** | Stop blocking the form on `has_enabled_provider` / the "connect first" card; always render the design form (FR-001). |
| Model/provider picker | `app/routes/agents_create.py` (`_build_model_picker_groups`) + `new.html` | **modify** | Offer all providers as selectable; do not disable groups for "no machine runs X" (FR-002). |
| Post-create routing | `app/routes/agents_create.py` (`create_agent_or_connection` success branch) | **extend** | After save, redirect to connect-that-provider, passing a `?provider=` hint + `?next` (FR-004). |
| Join setup routing | `app/routes/web_player.py` (`_join_setup_redirect`) | **modify** | No-agent users → `/me/agents/new` (design first), not `/me/connections` (FR-005). |
| Provider-coverage helpers | `app/engine/connection_health.py` (`enabled_provider_values`, `provider_is_covered`, `provider_enabled_on_any_connection`) | **reuse** | Drive "ready vs needs-connecting" — already derived, no new DB column (FR-003). |
| Batch readiness for the agent list | `app/engine/connection_health.py` + `app/routes/agents_setup.py` | **extend** | Add ONE batched coverage query for the list (not per-agent) to avoid N queries (FR-006 residual risk). |
| Connect picker / target a provider | `app/routes/connections_pages.py` (`list_connections`) + `app/routes/connections_connect_guide.py` (`_connect_options`) + `app/templates/connections/_connect_picker.html` | **extend** | Accept a `?provider=` hint to preselect the matching client tab; generic fallback when absent (FR-004). |
| Agent list UI | `app/templates/agents/list.html` + `app/routes/agents_setup.py` | **extend** | Surface needs-connecting state + a provider-scoped connect CTA (FR-006). |
| Agent detail recovery CTA | `app/templates/agents/detail.html` | **modify** | Make the existing recovery action provider-scoped (carry `?provider=`) instead of the generic `/me/connections` link (FR-006). |
| Held-seat / connect screens | `app/templates/seat_connect.html` + PR #406 state-aware logic | **reuse** | No change; verify the design-first path still reaches it correctly (FR-007). |
| Agent model/state | `app/models/agent.py` | **reuse** | No new column — "needs connecting" is derived from connection coverage (NFR-004). |
| Capacity gate | `app/routes/web_player.py` (`_seat_user_agent`, `active_matches_for_provider`, `live_provider_capacity`) | **reuse** | Must stay keyed on live coverage so disconnected agents never bypass/inflate capacity (edge case). |

**Duplication risk:** none found. No new module is justified — the wrong move
would be a new "agent readiness" abstraction; rejected, because
`connection_health.py` already derives coverage and just needs a batched call for
the list. No new DB column (readiness derived). The connect-picker already has
per-client tabs; we add a preselect hint rather than a new connect screen.
