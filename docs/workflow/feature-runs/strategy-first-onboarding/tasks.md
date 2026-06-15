# Tasks — Strategy-first onboarding

Four checkpoint-bounded slices (each ≤ ~300 lines). Each slice ends `[CHECKPOINT]`;
the diff checkpoint runs a Gemini regression review for slices ≥ 50 changed lines.
Slices are independent except CP4 (readiness UI) reuses the status-aware helper it
adds. No DB migration. Order CP1 → CP2 → CP3 → CP4.

## CP1 — Decouple create-agent (Slice 1)

- [ ] T1 [app/routes/agents_create.py] Remove the POST gate in
  `create_agent_or_connection` that redirects to `/me/connections` when the
  provider isn't enabled. In `new_agent_form` (GET), stop letting
  `has_enabled_provider` swap the form for the "connect first" card — always
  build the full design form. Make `_build_model_picker_groups` mark every
  provider's options selectable (drop the "no machine runs X" disabling).
- [ ] T2 [app/templates/agents/new.html] Always render the design form (name /
  model picker / strategy); remove the "Connect an AI client first" blocking card
  as a precondition; picker shows all providers as choosable.
- [ ] T3 [tests/test_strategy_first_onboarding.py] A signed-in user with zero
  connections can POST-create an agent (no redirect-to-connections block); the GET
  form renders the real form (no connect-first card); the picker offers every
  provider's models.
- CP1 complete [CHECKPOINT]

## CP2 — Provider-scoped connect handoff (Slice 2)

- [ ] T4 [app/routes/connections_pages.py] Accept an optional `?provider=` on
  `list_connections` AND the `live_status_fragment` poll. Only short-circuit
  (`next_url` / `HX-Redirect`) when *that target provider* is live
  (`provider_is_covered`), not on global `is_live_now`. Thread the provider into
  the template context.
- [ ] T5 [app/templates/connections/_connect_picker.html] Preselect the tab
  matching `?provider=` (claude→claude-code, gemini→gemini, openai→codex);
  hermes / openclaw / unknown → no preselect (generic picker, no broken tab).
- [ ] T6 [app/routes/agents_create.py] On create success, when the agent's
  provider isn't live, redirect to `/me/connections?provider=<value>` carrying
  `?next`; with NO `?next`, still route to connect-that-provider (not the
  `/me/agents/{id}` fallback). Preserve `?next` when re-rendering the form after a
  validation failure.
- [ ] T7 [app/templates/agents/new.html, app/templates/connections/_live_status.html, app/templates/seat_connect.html]
  Carry `?provider=` on the per-provider `availability_notes` "connect {Provider}"
  links, the "Create your agent" CTA, and the seat-hold reconnect link.
- [ ] T8 [tests/test_strategy_first_onboarding.py] Post-create redirect targets the
  right provider tab; a user with a *different* provider already live still lands
  on the connect step (no early bounce on the page OR the poll fragment);
  `?provider=hermes` and an unknown value render the generic picker without error;
  `?next` survives a bad-name re-render; create-without-`?next` routes to connect.
- CP2 complete [CHECKPOINT]

## CP3 — Reverse the Join hub (Slice 3)

- [ ] T9 [app/routes/web_player.py] In `_join_setup_redirect`, send a signed-in,
  handled user with no AI agent to `/me/agents/new?next=<join_url>` (design first),
  not `/me/connections`.
- [ ] T10 [tests/test_join_seat_hold.py] A no-agent, no-connection user hitting a
  match's Join page is routed to `/me/agents/new` (not `/me/connections`); existing
  seat-hold / PR #406 state-aware tests stay green.
- CP3 complete [CHECKPOINT]

## CP4 — Agent readiness UI (Slice 4)

- [ ] T11 [app/engine/connection_health.py] Add a status-aware coverage helper: a
  provider counts as "set up" only when enabled on a NOT-paused, not-deleted
  connection. Add a batched "set-up providers for this user" query (one query, not
  per agent).
- [ ] T12 [app/routes/agents_health_presenter.py] Add an explicit "needs
  connecting" state to `_is_ready_to_play` / the presenter (provider set up
  nowhere), distinct from READY / At-capacity — so a stale-but-configured or
  paused-only agent never shows a false "Ready".
- [ ] T13 [app/routes/agents_list.py] Use the batched set-up-providers query and
  batch the per-agent match count (remove the `_count_agent_matches`-in-loop N+1).
  Pass per-agent readiness + a provider-scoped connect link.
- [ ] T14 [app/templates/agents/list.html, app/templates/agents/_onboarding.html, app/templates/agents/detail.html]
  Render the needs-connecting state + a provider-scoped connect CTA (`?provider=`).
- [ ] T15 [tests/test_strategy_first_onboarding.py] Agent whose provider is enabled
  nowhere → needs-connecting + provider-scoped link; enabled only on a PAUSED
  connection → needs-connecting; enabled on an ACTIVE-but-stale connection → set-up
  (not a false Ready/At-capacity); covered+live → ready; the list issues a bounded,
  constant number of queries (no per-agent coverage or match-count query).
- CP4 complete [CHECKPOINT]
