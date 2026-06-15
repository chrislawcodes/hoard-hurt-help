# Plan

## Review Reconciliation

- review: reviews/spec.codex.feasibility-adversarial.review.md | status: accepted | note: Round 3: no actionable findings — spec converged.
- review: reviews/spec.gemini.requirements-adversarial.review.md | status: accepted | note: Round 3: confirmations only, no new findings — spec converged.

Earlier rounds (addressed in the spec): the dead-end also lived in the GET
form/template (not just the POST gate) → FR-001/FR-002 now cover both; the
"connect Claude Code" handoff needs a provider hint on the (provider-neutral)
connect page → FR-004; FR-006 now names the agent list/detail templates and a
provider-scoped CTA; edge cases added for `?next` survival and capacity math.

## Architecture decisions

1. **Decouple agent creation from connection.** Remove the
   `enabled_provider_values` gate in `agents_create.py` on BOTH paths: the POST
   handler (no redirect to `/me/connections`) and the GET `new_agent_form` (drop
   the `has_enabled_provider`-driven "connect first" card; always render the
   design form). The model picker (`_build_model_picker_groups`) offers every
   provider as selectable.
2. **Readiness is derived, no new column.** "Ready vs needs-connecting" is
   computed from existing coverage helpers (`connection_health.provider_is_covered`
   / `enabled_provider_values`). No DB migration. For the agent list, compute
   coverage in ONE batched query (provider set enabled across the user's live
   connections), then map per agent — never a query per agent.
3. **Provider-scoped connect handoff.** Add an optional `?provider=<value>` hint
   to `/me/connections` (`list_connections` in `connections_pages.py`) that
   preselects the matching client tab in `_connect_picker.html`
   (Claude→claude-code, Gemini→gemini, OpenAI→codex). The create success branch
   passes it. Absent/unknown hint → generic picker (no regression).
4. **Reverse the Join hub.** `web_player._join_setup_redirect`: a no-agent user
   goes to `/me/agents/new` (design first), carrying `?next` back to Join. The
   create flow already forwards `?next`; verify it survives a validation failure.
5. **Preserve seat/capacity behavior.** `_seat_user_agent` and capacity
   (`active_matches_for_provider` / `live_provider_capacity`) stay keyed on live
   coverage, so a needs-connecting agent can hold a seat (PR #406 path) but never
   bypass or inflate capacity.

## Wave / slice breakdown (each ≤ ~300 lines, `[CHECKPOINT]` per slice)

- **Slice 1 — Decouple create-agent.** Remove the POST gate; unblock the GET
  form; enable all providers in the picker. Files: `app/routes/agents_create.py`,
  `app/templates/agents/new.html`. Tests: no-connection user can POST-create an
  agent; GET form renders (no "connect first" card); picker offers all providers.
  `[CHECKPOINT]`
- **Slice 2 — Provider-scoped connect handoff.** `?provider=` hint on
  `/me/connections` + preselect tab; create success redirects to it; `?next`
  preserved (incl. through a create validation failure). Files:
  `app/routes/agents_create.py`, `app/routes/connections_pages.py`,
  `app/templates/connections/_connect_picker.html`. Tests: post-create redirect
  targets the right provider tab; connect page renders with and without the hint;
  `?next` survives a bad-name re-render. `[CHECKPOINT]`
- **Slice 3 — Reverse the Join hub.** `_join_setup_redirect` → `/me/agents/new`
  for no-agent users. File: `app/routes/web_player.py`. Tests: no-agent user GET
  Join → `/me/agents/new?next=…` (not `/me/connections`); existing seat-hold /
  #406 tests stay green. `[CHECKPOINT]`
- **Slice 4 — Agent readiness UI.** Needs-connecting state + provider-scoped CTA
  on the agent list and detail; batched coverage query. Files:
  `app/routes/agents_setup.py` (list), `app/templates/agents/list.html`,
  `app/templates/agents/detail.html`. Tests: an agent with no live provider shows
  needs-connecting + a provider-scoped connect link; a covered agent shows ready;
  list issues one coverage query, not N. `[CHECKPOINT]`

## Reuse decisions

Per `reuse-report.md`: no new module. All four slices are modify/extend of
existing routes, helpers, and templates. Coverage helpers in
`connection_health.py` are reused (FR-003); the connect picker is extended with a
preselect hint, not replaced (NFR-003); no DB migration (NFR-004).

## Residual Risks (each carries a verification action — FF rule)

- **A disconnected agent leaks into capacity math.** verification: a test that a
  needs-connecting agent is NOT counted by `active_matches_for_provider` /
  `live_provider_capacity` and cannot bypass the seat cap; pre-merge.
- **`?next` is lost when create validation fails, trapping a Join↔Create loop.**
  verification: a test that POSTing the create form with an invalid name
  re-renders WITH `?next` intact; pre-merge.
- **The provider hint breaks the generic connect page.** verification: a test that
  `/me/connections` renders correctly both with a valid `?provider=`, with an
  unknown value, and with none (falls back to the generic picker); pre-merge.
- **Reversed Join routing reintroduces a dead-end.** verification: a test that a
  no-agent, no-connection user GET Join → `/me/agents/new` AND can then create an
  agent successfully (Slice 1 gate removed); pre-merge.
- **Agent-list batched coverage is wrong for mixed providers.** verification: a
  test seeding agents across connected and unconnected providers and asserting
  each row's ready/needs-connecting flag matches per-agent coverage; pre-merge.
- **PR #406 held-seat path no longer reached.** verification: the existing
  `test_join_seat_hold.py` suite stays green (no countdown, state-aware page);
  Preflight Gate green.
