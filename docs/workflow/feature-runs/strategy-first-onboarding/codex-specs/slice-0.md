# Implementation Task

## Context
# Spec — Strategy-first onboarding

## Summary

Today, setting up a competitor is **connect-first**: a player must connect an AI
client over MCP before they can create an agent, because agent creation is gated
on having a provider enabled on a connection (`app/routes/agents_create.py`
redirects to `/me/connections` when the agent's provider isn't connected). That
ordering puts the boring technical chore (connecting) before the creative,
identity-forming part (designing the agent's strategy) — exactly where new
players drop off.

This feature flips the order to **strategy-first**: a player designs their agent
— name, strategy, and which AI it uses — *before* connecting anything. The agent
saves immediately in a clear "ready — needs connecting" state. Then the player is
guided to connect that agent's specific provider, and once that connection is
live the agent can be seated and play.

The load-bearing change is **decoupling agent creation from having a connection**.
Choosing the provider happens at design time (the picker offers every provider,
not only connected ones), which makes the follow-up connect step specific
("Connect Claude Code") instead of generic.

This preserves the three roles bound only by provider — connection (the door),
agent (the player), running loop (the heartbeat) — and one-client-one-provider
(PR #392). It reverses the connect-first routing introduced in PR #400 and keeps
the state-aware, countdown-free held-seat screens from PR #406.

## User Scenarios & Testing

### User Story 1 — Design my agent before connecting anything (Priority: P1)

As a new player who has signed in but connected nothing, I want to create my
agent — give it a name, a strategy, and pick which AI runs it — and have it
saved, so I have invested in something real before doing any technical setup.

**Why this priority**: This is the whole feature. Without it, the chore still
comes before the hook and the dead-end persists.

**Independent Test**: Sign in as a user with zero connections. Go to the
create-agent page. Pick any provider's model, write a strategy, submit. The agent
is created and persists; no redirect to `/me/connections` blocks it.

**Acceptance Scenarios**:

1. **Given** a signed-in user with no connections and no agents, **When** they
   submit the create-agent form with a name, a model (any provider), and a
   strategy, **Then** the agent is created and saved, and they are NOT bounced to
   the connections page as a precondition.
2. **Given** the create-agent page for a user with no connections, **When** the
   model/provider picker renders, **Then** every provider's models are
   selectable (not greyed out for "no machine runs X").

### User Story 2 — Be guided to connect the right AI next (Priority: P1)

As a player who just designed an agent, I want to be sent straight to connecting
*that agent's* AI, so the connect step is specific and obviously the next thing.

**Why this priority**: A saved-but-unconnected agent is useless until the matching
client is connected; the hand-off must be frictionless and specific.

**Independent Test**: Create an agent for provider X with no connection. Confirm
the post-create destination is the connect flow scoped to provider X (carrying a
`?next` back), and that connecting X then lets the agent play.

**Acceptance Scenarios**:

1. **Given** a freshly created agent whose provider is not yet connected, **When**
   creation completes, **Then** the player lands on a step that connects *that
   provider* (e.g. "Connect Claude Code"), with `?next` preserved so the chain
   resumes.
2. **Given** that agent and a now-live connection for its provider, **When** the
   player joins a match with it, **Then** the seat confirms and the agent plays
   (existing seat/live behavior unchanged).

### User Story 3 — Join sends a new player to design an agent first (Priority: P1)

As a brand-new player who clicks Join with no agent yet, I want to be taken to
*design an agent* first (not to connect a client first), so the order matches the
strategy-first flow.

**Why this priority**: This is the reversal of PR #400's connect-first routing;
without it the entry point still leads with the chore.

**Independent Test**: As a user with no agent (and no connection), hit a match's
Join URL. Confirm the redirect target is the create-agent page, not
`/me/connections`.

**Acceptance Scenarios**:

1. **Given** a signed-in, handled user with no AI agent, **When** they open a
   match's Join page, **Then** they are routed to create an agent first
   (`/me/agents/new`), carrying `?next` back to Join.
2. **Given** that user finishes designing an agent, **When** they return through
   `?next`, **Then** they continue toward connecting that agent's provider and
   then Join, with no dead-end.

### User Story 4 — See whether my agent is ready or needs connecting (Priority: P2)

As a player with one or more agents, I want each agent to clearly show whether it
can play now or still needs its AI connected, with a direct action to fix it.

**Why this priority**: Makes the "designed but not connected" state legible and
recoverable; important but the core flow works without polished badges.

**Acceptance Scenarios**:

1. **Given** an agent whose provider has no live/enabled connection, **When** the
   player views their agents, **Then** that agent shows a clear "needs
   connecting" state with a CTA to connect its provider.
2. **Given** an agent whose provider is connected and live, **When** the player
   views their agents, **Then** that agent shows as ready/able to play.

### User Story 5 — My designed agent is captured even if I stop (Priority: P3)

As a player who designs an agent but doesn't finish connecting, I want my agent
and its strategy saved, so I can come back and finish later (and so the product
can re-engage me).

**Why this priority**: A retention win that falls out of decoupling; not required
for the core flow to function.

**Acceptance Scenarios**:

1. **Given** a player who created an agent and then abandoned the connect step,
   **When** they return later, **Then** their agent still exists with its name
   and strategy, showing "needs connecting".

## Edge Cases

- **Provider already connected at design time** → after create, skip straight to
  play/Join rather than a redundant connect step (the connect step only shows
  when the agent's provider isn't live/enabled).
- **User picks a provider, never connects it** → agent sits in "needs connecting"
  indefinitely; it simply can't be seated until connected (held-seat/#406 path
  still applies if they Join it).
- **Model not valid for the chosen provider** → still rejected (existing
  model↔provider validation stays).
- **User has multiple agents across providers** → each shows its own
  ready/needs-connecting state independently (per-provider, per #392).
- **Returning user who already has agents** → Join still shows the agent picker
  (unchanged); no forced re-design.
- **Reconcile #406** → the state-aware held-seat page (no countdown) is unchanged;
  it is reached when a player Joins an agent whose provider isn't live.
- **Empty strategy** → fall back to the game's default strategy (existing
  behavior) so an agent is never strategy-less.
- **Create validation fails (e.g. bad name) mid-chain** → the re-rendered form
  MUST preserve `?next` so the user is never trapped looping between Join and
  Create (Gemini residual risk).
- **Disconnected agent and capacity** → a "needs connecting" agent MUST NOT count
  toward, or break, live-connection capacity math (`active_matches_for_provider` /
  `live_provider_capacity`); it simply cannot be seated until its provider is live
  (Gemini residual risk).

## Requirements

### Functional Requirements

- **FR-001**: The create-agent flow MUST allow creating an agent when the chosen
  provider is not enabled on any connection. This requires changing BOTH paths in
  `app/routes/agents_create.py`: (a) the POST handler's redirect-to-connections
  gate, AND (b) the GET handler `new_agent_form`, which today computes
  `has_enabled_provider` and makes `agents/new.html` render a "Connect an AI client
  first" card (with a `/me/connections` CTA) instead of the real form. The GET
  form MUST show the full design form even with zero connections. Supports US1.
- **FR-002**: The create-agent model/provider picker MUST offer all providers'
  models as selectable regardless of which providers are connected — the picker
  MUST NOT disable provider groups/options for "no machine runs X"
  (`_build_model_picker_groups` + `agents/new.html`). Supports US1.
- **FR-003**: A created agent MUST persist its name, strategy, and provider, and
  MUST be distinguishable as "ready but not connected" when its provider has no
  enabled/live connection. The state SHOULD be derived from connection data
  rather than a new stored column unless the plan shows derivation is infeasible.
  Supports US1, US4, US5.
- **FR-004**: After successful creation, the system MUST route the player to
  connect that agent's specific provider. Because `/me/connections` is currently
  provider-neutral (`list_connections` takes only `next`; one generic client
  picker), this MUST add a provider hint to that page (e.g. `?provider=<value>`)
  that preselects the matching client tab (Claude→Claude Code, Gemini→Gemini,
  OpenAI→Codex), and the create handler MUST pass it, preserving any `?next`. If
  the hint is absent/unknown the page MUST still render the generic picker
  (graceful fallback). "Specific provider" means which MCP CLIENT to connect — one
  client = one provider (PR #392); the multi-provider machine connector is a
  separate path and out of scope. Supports US2.
- **FR-005**: The Join setup routing (`_join_setup_redirect` in
  `app/routes/web_player.py`) MUST send a signed-in, handled user with no AI agent
  to design an agent first (`/me/agents/new`), NOT to `/me/connections`, carrying
  `?next` back to Join. Supports US3.
- **FR-006**: When a player views their agents, each agent MUST show whether it
  can play now or needs connecting, with a direct, provider-scoped CTA to connect
  its provider when needed. This names specific templates: the agent list
  (`app/templates/agents/list.html`, today only a health badge + name + model +
  row link — no connect CTA) MUST surface the needs-connecting state and CTA, and
  the agent detail page (`app/templates/agents/detail.html`) MUST make its connect
  action provider-scoped (carry the `?provider=` hint from FR-004) rather than the
  generic `/me/connections` link. To avoid per-agent query cost, the
  ready-vs-needs-connecting computation for the list MUST be batched (one coverage
  query, not one per agent). Supports US4.
- **FR-007**: Existing seat/live behavior MUST be preserved: an agent whose
  provider is live can be seated and play; an agent whose provider is not live
  follows the PR #406 state-aware held-seat page (no countdown). Supports US2.

### Non-Functional / Constraints

- **NFR-001**: Keep one-client-one-provider (PR #392). Copy MUST NOT imply that
  one connection covers all models.
- **NFR-002**: MUST NOT add auto-join; entering a match stays a deliberate Join
  click.
- **NFR-003**: Reuse the existing `/me/connections` MCP connect flow; do not build
  a new connect screen.
- **NFR-004**: Prefer no database migration — derive "needs connecting" from
  existing connection/provider data if possible (confirm in plan).
- **NFR-005**: Preserve the existing model↔provider validation and agent-name
  screening.

## Success Criteria

- **SC-001**: A signed-in user with zero connections can create an agent end to
  end without hitting a connect-first block.
- **SC-002**: A new user clicking Join with no agent reaches the create-agent page
  (design first), not the connections page, on the first hop.
- **SC-003**: After designing an agent, the very next step offered connects that
  agent's specific provider; once connected and live, the agent can be seated.
- **SC-004**: Every agent in the player's list visibly indicates ready vs needs-
  connecting and offers the matching connect action when needed.
- **SC-005**: An agent created without connecting still exists (name + strategy)
  on the player's next visit.

## Key Entities

- **Agent** — the player's competitor: `name`, `provider`, `strategy` (via its
  current `AgentVersion`'s `model` + `strategy_text`), `status`. After this
  feature it can exist with no live/enabled connection for its provider.
- **Connection** — the MCP door; carries one or more enabled providers
  (`connection_providers`). One MCP client == one provider (PR #392).
- **Readiness (derived)** — for a given agent, whether its provider is
  enabled/live on any of the user's connections (`enabled_provider_values` /
  `provider_is_covered` in `app/engine/connection_health.py`). Drives the
  "ready vs needs-connecting" display.

## Scope

In scope (the run's scope paths):
- `app/routes/agents_create.py` — remove the connect-first gate; post-create routing.
- `app/routes/agents_setup.py` — agent list/readiness display.
- `app/routes/web_player.py` — `_join_setup_redirect` routing reversal.
- `app/engine/connection_health.py` — readiness helpers (reuse; extend only if needed).
- `app/models/agent.py` — only if a stored state proves necessary (prefer not).
- `app/templates/agents/*` — create form (all-providers picker), readiness CTA.
- `app/templates/connections/*` — minimal copy alignment for the post-create connect step.
- `app/templates/seat_connect.html` — keep #406 behavior; verify it still fits.

Out of scope: MCP auth/token mechanics; auto-join; a new connect screen; game rules.

## Assumptions

- "Needs connecting" can be derived from existing connection/provider data
  (no new DB column). To be validated in the plan; FR-003 allows a stored flag
  only if derivation is infeasible.
- The existing `/me/connections` flow can be targeted at a specific provider via
  its current per-client picker and `?next` plumbing; no new connect UI needed.
- Reversing PR #400's routing does not reintroduce the create-agent dead-end,
  because FR-001 removes the gate that made connect-first necessary.


## Plan
# Plan

## Review Reconciliation

- review: reviews/spec.codex.feasibility-adversarial.review.md | status: accepted | note: Round 3: no actionable findings — spec converged.
- review: reviews/spec.gemini.requirements-adversarial.review.md | status: accepted | note: Round 3: confirmations only, no new findings — spec converged.
- review: reviews/plan.codex.implementation-adversarial.review.md | status: accepted | note: Round 4: no actionable findings — plan converged.
- review: reviews/plan.gemini.testability-adversarial.review.md | status: accepted | note: Round 4: no actionable findings — plan converged.
- review: reviews/diff.gemini.regression-adversarial.review.md | status: accepted | note: CP2 diff: no actionable findings.

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


## Tasks to implement (your scope)
- [ ] T9 [app/routes/web_player.py] In `_join_setup_redirect`, send a signed-in,
- [ ] T10 [tests/test_join_seat_hold.py] A no-agent, no-connection user hitting a

## File scope
(no specific scope — implement all tasks)

Implement ONLY the tasks listed above for this slice. Do not implement tasks from other slices and do not work ahead. Commit your changes when done.
DO NOT MODIFY: CLAUDE.md, AGENTS.md, MEMORY.md, the docs/ design/architecture docs, or any file outside this slice's declared scope. The spec/plan above are context only — they describe the whole feature, not your slice; build just the tasks listed.
