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

## Requirements

### Functional Requirements

- **FR-001**: The create-agent flow MUST allow creating an agent when the chosen
  provider is not enabled on any connection (remove/replace the redirect-to-
  connections gate in `app/routes/agents_create.py`). Supports US1.
- **FR-002**: The create-agent model/provider picker MUST offer all providers'
  models regardless of which providers are connected. Supports US1.
- **FR-003**: A created agent MUST persist its name, strategy, and provider, and
  MUST be distinguishable as "ready but not connected" when its provider has no
  enabled/live connection. The state SHOULD be derived from connection data
  rather than a new stored column unless the plan shows derivation is infeasible.
  Supports US1, US4, US5.
- **FR-004**: After successful creation, the system MUST route the player to the
  step that connects that agent's specific provider (e.g. the existing
  `/me/connections` flow targeting that provider), preserving any `?next`.
  Supports US2.
- **FR-005**: The Join setup routing (`_join_setup_redirect` in
  `app/routes/web_player.py`) MUST send a signed-in, handled user with no AI agent
  to design an agent first (`/me/agents/new`), NOT to `/me/connections`, carrying
  `?next` back to Join. Supports US3.
- **FR-006**: When a player views their agents, each agent MUST show whether it
  can play now or needs connecting, with a direct CTA to connect its provider when
  it needs connecting. Supports US4.
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
