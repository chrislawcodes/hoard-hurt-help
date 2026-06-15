# Plan

## Review Reconciliation

- review: reviews/spec.codex.feasibility-adversarial.review.md | status: accepted | note: Round 3: no actionable findings — spec converged.
- review: reviews/spec.gemini.requirements-adversarial.review.md | status: accepted | note: Round 3: confirmations only, no new findings — spec converged.
- review: reviews/plan.codex.implementation-adversarial.review.md | status: accepted | note: r3 HIGH: live poll (live_status_fragment) now also only short-circuits on target-provider liveness. r3 MEDIUM1: hint on availability_notes links + all-provider mapping (hermes/openclaw->generic) + create-without-next still routes to connect. r3 MEDIUM2: needs-connecting respects PAUSED status (status-aware coverage). Verifications added.
- review: reviews/plan.gemini.testability-adversarial.review.md | status: accepted | note: r3: no new actionable beyond Codex; readiness/capacity/next verifications retained.

## Architecture decisions

1. **Decouple agent creation from connection.** Remove the
   `enabled_provider_values` gate in `agents_create.py` on BOTH paths: the POST
   handler (no redirect to `/me/connections`) and the GET `new_agent_form` (drop
   the `has_enabled_provider`-driven "connect first" card; always render the
   design form). The model picker (`_build_model_picker_groups`) offers every
   provider as selectable.
2. **Readiness is derived, no new column.** "Needs-connecting" keys off
   **enabled coverage** — `provider_enabled_on_any_connection` /
   `enabled_provider_values` ("have you set this provider up at all"), NOT the
   90-second live window. So the agent list says "needs connecting" only when the
   provider is enabled on no connection; otherwise it says set-up/ready, and the
   *live-right-now* nuance reuses the **existing connection health badge** (the
   same `LIVE_WINDOW_SECONDS` signal the rest of the app uses). This deliberately
   avoids a NEW, possibly-stale "live now" claim on the agent card (Gemini plan
   finding #1). No DB migration. For the list, compute the enabled-provider set in
   ONE batched query, then map per agent — never a query per agent.
   **Add a distinct "needs-connecting" state, don't widen READY (Codex plan
   MEDIUM):** the readiness presenter `agents_health_presenter._is_ready_to_play`
   today returns only READY/LIVE-or-not + `join_blocked`, and
   `agents/_onboarding.html` only renders "Ready to play" / "At capacity" /
   reconnect. We MUST add an explicit "needs connecting" branch (provider enabled
   on no connection) rather than overloading READY — otherwise a stale-but-
   configured agent wrongly shows "Ready" / "At capacity".
   **Respect connection status — paused counts as needs-connecting (Codex plan r3
   MEDIUM):** `enabled_provider_values` / `provider_enabled_on_any_connection`
   ignore `ConnectionStatus.PAUSED` (they look only at enabled rows on non-deleted
   connections). So "needs connecting" MUST be computed against connections that
   are NOT paused and not deleted — a provider enabled only on a paused connection
   is still needs-connecting (resume/reconnect), not "set up". Add a status-aware
   coverage helper (or a `status != PAUSED` filter) rather than reusing the raw
   enabled set for this gate.
3. **Provider-scoped connect handoff.** Add an optional `?provider=<value>` hint
   to `/me/connections` (`list_connections` in `connections_pages.py`) that
   preselects the matching client tab in `_connect_picker.html`
   (Claude→claude-code, Gemini→gemini, OpenAI→codex). The create success branch
   passes it. Absent/unknown hint → generic picker (no regression).
   **Fix the live short-circuit on BOTH the page and the poll (Codex plan HIGH,
   r2+r3):** today `list_connections` AND the 4-second HTMX poll
   `live_status_fragment` both return `next_url`/`HX-Redirect` whenever
   `is_live_now` (ANY connection live). With a `?provider=` hint, BOTH MUST only
   short-circuit when *that target provider* is live (`provider_is_covered`), so a
   user with one live provider can still connect a different one without the page
   OR the poll bouncing them back early.
   **Carry the hint on EVERY connect entry point (Codex plan MEDIUM):** all connect
   links where the provider is known need `?provider=` — `_live_status.html`
   "Create your agent" CTA, `seat_connect.html` reconnect link, AND the per-provider
   `availability_notes` "connect {Provider}" links in `agents/new.html`.
   **Provider→client mapping covers EVERY provider value:** claude→claude-code,
   gemini→gemini, openai→codex; hermes/openclaw and any unknown value → the generic
   picker (no dedicated tab) — never a broken/blank tab.
   **Create reached without `?next`:** still route to connect-that-provider when
   the agent's provider isn't live (not the `/me/agents/{id}` fallback), so the
   strategy-first chain works even when create is not reached via Join.
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
  `/me/connections` + preselect tab; **only short-circuit `is_live_now` when the
  TARGET provider is live**; create success redirects to it; `?next` preserved
  (incl. through a create validation failure); carry the hint on the other connect
  CTAs. Files: `app/routes/agents_create.py`, `app/routes/connections_pages.py`,
  `app/templates/connections/_connect_picker.html`,
  `app/templates/agents/new.html`, `app/templates/connections/_live_status.html`,
  `app/templates/seat_connect.html`. Tests: post-create redirect targets the right
  provider tab; a user with a different provider already live still lands on the
  connect step (no early bounce); connect page renders with/without/unknown hint;
  `?next` survives a bad-name re-render. `[CHECKPOINT]`
- **Slice 3 — Reverse the Join hub.** `_join_setup_redirect` → `/me/agents/new`
  for no-agent users. File: `app/routes/web_player.py`. Tests: no-agent user GET
  Join → `/me/agents/new?next=…` (not `/me/connections`); existing seat-hold /
  #406 tests stay green. `[CHECKPOINT]`
- **Slice 4 — Agent readiness UI.** Add an explicit "needs-connecting" state to
  the readiness presenter and onboarding card (don't widen READY) + provider-
  scoped CTA on the agent list and detail; batch BOTH the coverage lookup AND the
  per-agent match-count query (Codex plan LOW: `list_agents` calls
  `_count_agent_matches` per agent — N+1). Files:
  `app/routes/agents_list.py`, `app/routes/agents_health_presenter.py`
  (`_is_ready_to_play` + a needs-connecting branch), `app/templates/agents/list.html`,
  `app/templates/agents/_onboarding.html`, `app/templates/agents/detail.html`.
  Tests: an agent whose provider is enabled nowhere shows needs-connecting + a
  provider-scoped connect link; an enabled-but-stale provider shows set-up (not a
  false Ready/At-capacity); a covered+live agent shows ready; the list issues a
  bounded, constant number of queries (no per-agent coverage or match-count
  query). `[CHECKPOINT]`

## Reuse decisions

Per `reuse-report.md`: no new module. All four slices are modify/extend of
existing routes, helpers, and templates. Coverage helpers in
`connection_health.py` are reused (FR-003); the connect picker is extended with a
preselect hint, not replaced (NFR-003); no DB migration (NFR-004).

## Residual Risks (each carries a verification action — FF rule)

- **The 4-second live poll bounces the user before they finish (Codex plan r3
  HIGH).** verification: a test that `GET /me/connections/live-status?provider=X`
  does NOT HX-Redirect while provider X is not live, even when a different provider
  is live; pre-merge.
- **A paused-but-enabled connection wrongly reads as "set up" (Codex plan r3
  MEDIUM).** verification: a test that an agent whose provider is enabled only on a
  PAUSED connection shows "needs connecting", and one on an ACTIVE connection does
  not; pre-merge.
- **A provider with no dedicated client tab (Hermes/OpenClaw) breaks the hint.**
  verification: a test that `?provider=hermes` (and an unknown value) renders the
  generic picker without error; pre-merge.
- **Create without `?next` bypasses connect.** verification: a test that creating
  an agent for an un-live provider with no `?next` still routes to
  connect-that-provider, not the agent detail page; pre-merge.
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
- **Readiness UI implies "live now" when a provider is enabled-but-stale (Gemini
  plan finding #1).** verification: a test that an agent whose provider is enabled
  on a connection that is NOT live shows "set up" (not a false "ready to play
  now"); the needs-connecting flag keys on `provider_enabled_on_any_connection`,
  and live-now is shown only via the existing health badge; pre-merge.
