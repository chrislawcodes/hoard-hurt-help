# Spec — Onboarding / Auth Flow Consolidation

**Slug:** `onboarding-consolidation`
**Path:** Feature Factory (full) · platform feature
**Scope dirs:** `app/engine/`, `app/routes/`

## Summary

We keep hitting bugs in "how a user gets an AI agent into a game" because **five
different entry points each re-derive the same onboarding ladder, using a
different provider-readiness predicate.** They drift, and a user ends up at the
wrong next step (wrong CTA label, skipped connect step, redirect loop).

This feature centralizes that logic onto **two modules we already have** — no new
module:

1. **`app/engine/connection_health.py`** gains **one provider-capability
   tri-state** — `NO_MCP_CONNECTION` / `CONNECTED_NOT_LIVE` / `LIVE` — and the ~6
   overlapping predicates are re-expressed over it (or kept as documented thin
   wrappers).
2. **`app/routes/nav_context.py`** promotes `compute_nav_cta` into a shared
   **`resolve_onboarding_state(...)`** that returns the first unmet gate plus the
   URL to send the user to. All five entry points call it instead of rolling
   their own ladder.

The user-facing pipeline this expresses (the "holistic" model): **sign in →
handle → create agent (picks the provider) → connect that provider's MCP
connection → provider goes live / starts pulling → join the requested game.**

No DB migration. Storage stays one-connection-per-user + per-provider
`connection_providers` toggles; "per-provider MCP connection" is a *conceptual*
unit derived over those toggles.

## Problem

There is no single object that represents "how far along is this user toward
playing." Each page invents its own slice, so:

- The **same question** ("is this provider ready?") is answered by **four
  different predicates** that disagree at the edges.
- Fixes have been **piecemeal** — patch one redirect, the next page still has the
  old logic.
- The "signed in but no provider" path is spread across `auth.py`, `deps.py`,
  `agents_create.py`, `connections_pages.py`, and `web_player.py`.

## Goal

One intent-driven onboarding pipeline whose "next step" decision is computed in
**one** place and whose "is this provider ready?" question is answered by **one**
tri-state — both living in modules that already exist.

## Non-goals

- **No new module or subsystem.** Consolidate onto `connection_health.py` +
  `nav_context.py` only. (An optional rename of `nav_context.py` →
  `onboarding_state.py` is cosmetic and deferred to the plan; not required.)
- **No DB schema or migration change.** Storage and the MCP OAuth bridge are
  untouched.
- **No change to the per-user MCP connection creation / OAuth mechanics.**
- **Do not revisit strategy-first ordering** — agent-before-connection stays (an
  agent must exist first so we know *which* provider's connection to check).
- **Do not redesign the seat-hold / connect-countdown UX.** Only its "what is
  missing?" decision routes through the resolver; the countdown page itself is
  unchanged.
- **Seat-capacity math is out of scope** (`active_matches_for_provider`,
  `live_provider_capacity`, `is_join_blocked`) — that answers "how many matches
  can my machine serve," not "what's my next onboarding step."

---

## Current-state audit (the disagreement table)

How each entry point decides the user's next step **today**, and the exact
predicate it keys off. This is the artifact that motivates the unification — and
the sites marked **⚠ changes** will behave differently after consolidation, on
purpose.

| # | Entry point | File:line | What it decides | "Provider ready?" predicate it uses today | Notes |
|---|---|---|---|---|---|
| 1 | **Nav "Play" CTA** | `app/routes/nav_context.py:128` (`compute_nav_cta`) | Button label + href | **"connected once"** — `user_has_connected_agent` = provider enabled on a connection whose `first_connected_at` is set (line 42) | Deliberately *not* live-now so the label doesn't flap. Ladder: connected→agent→neither. |
| 2 | **`/play` hub** | `app/routes/web_games_catalog.py` (`operator_join_page`) | Where the "play" funnel lands | **none** — checks sign-in + handle only, then redirects to the lobby | Provider-less / agent-less users are dropped at the lobby, not routed to setup. |
| 3 | **Post-login redirect** | `app/routes/auth.py:119` (`google_callback`) | Where you land right after OAuth | **agent count only** — counts non-archived `kind=ai` agents; 0 → `/me/agents` | Only fires when `next == "/"`. Ignores handle and provider state. Sends to the agent *list*, not *new*. |
| 4 | **Agent-create destination** | `app/routes/agents_create.py:238` | After creating an agent: connect, or done | **"enabled on a non-paused connection"** — `enabled_provider_values_on_nonpaused_connections` (line 502) | Looser than "has current MCP setup": a provider enabled on a connection with no recent MCP login still counts as set up here. |
| 5 | **Join setup gate** | `app/routes/web_player.py:170` (`_join_setup_redirect`) | First missing step before the join form | **agent existence only** — `has_ai_agent`; none → `/me/agents/new` | The provider check happens *later*, per picked agent, in `_seat_user_agent` / `join_submit`. |
| 5b | **Seat confirm vs hold** | `app/routes/web_player.py:370` (`_seat_user_agent`) | Confirm the seat now, or hold it | **"loop running now"** — `provider_loop_running` (line 426; keys off `last_polled_at`) | Strictest signal: an AI is actually polling for turns. A mere sign-in handshake does *not* count. |
| 5c | **Seat connect redirect / escalation** | `app/routes/web_player.py:496,548,649` | Send to connect vs play-prompt; escalate to "reconnect" | **"has current setup"** — `provider_has_current_setup` (line 386; MCP-recent for Claude/OpenAI/Gemini, else enabled) | Yet another definition, distinct from #4's "enabled on non-paused." |

**The core finding:** "is this provider ready?" is answered by **four different
predicates** across these sites — `first_connected_at` set (1),
`enabled_provider_values_on_nonpaused_connections` (4), `provider_loop_running`
(5b), and `provider_has_current_setup` (5c) — plus two sites that don't check at
all (2, 3). They are each *locally* defensible but *collectively* inconsistent.

### The ~6 predicates in `connection_health.py` being consolidated

| Predicate | Line | Answers |
|---|---|---|
| `provider_is_covered` | 277 | A *live* connection (seen within `LIVE_WINDOW_SECONDS`) has the provider enabled. |
| `provider_enabled_on_any_connection` | 306 | Provider enabled on any non-deleted connection (liveness not required). |
| `provider_has_recent_mcp_connection` | 340 | MCP connection used within `MCP_CONNECTION_VALID_DAYS` (90d). |
| `provider_has_current_setup` | 386 | The setup we currently support exists (MCP-recent for MCP providers, else enabled). |
| `provider_has_live_current_setup` | 400 | That current setup is connected *right now* (seen now). |
| `provider_loop_running` | 426 | An AI is actually *polling/looping* right now (`last_polled_at`). |
| `enabled_provider_values*` | 475 / 502 | Set-level: which providers to offer / mark ready (multi-provider list). |

---

## Target design

### 1. The provider-capability tri-state (`connection_health.py`)

One function is the single answer to "where is this provider in setup?":

```python
class ProviderReadiness(enum.Enum):
    NO_MCP_CONNECTION = "no_mcp_connection"   # gate 4 unmet: no usable setup
    CONNECTED_NOT_LIVE = "connected_not_live" # set up, but no AI is looping yet
    LIVE = "live"                             # an AI is polling for turns right now

async def provider_readiness(db, user_id, provider) -> ProviderReadiness: ...
```

Boundary definitions (the agreed unified meaning):

| State | Defined as |
|---|---|
| `NO_MCP_CONNECTION` | `not provider_has_current_setup(...)` (MCP-recent for MCP providers; enabled for hermes/openclaw). |
| `CONNECTED_NOT_LIVE` | `provider_has_current_setup(...)` is true **and** `provider_loop_running(...)` is false. |
| `LIVE` | `provider_loop_running(...)` is true (an AI is actually pulling turns). |

The existing predicates are **kept as the building blocks** of these boundaries
(re-expressed, not duplicated): `provider_has_current_setup` defines the
`NO_MCP_CONNECTION` edge; `provider_loop_running` defines the `LIVE` edge;
`provider_is_covered` / `provider_enabled_on_any_connection` /
`provider_has_recent_mcp_connection` stay as internal helpers behind
`provider_has_current_setup`. The set-level `enabled_provider_values*` stay for
the create-form/agent-list "which providers" lists, but their use as a
*readiness* signal (site #4) is replaced by the tri-state.

**Note on `provider_has_live_current_setup` ("seen now"):** kept, but the plan
must decide whether the seat-connect auto-redirect should switch from "seen now"
to `LIVE` ("looping"). Recommendation: use `LIVE`, because "seen" includes a
sign-in handshake that isn't actually playing — but this is a plan-stage call.

### 2. The onboarding-state resolver (`nav_context.py`)

`compute_nav_cta` is already the ladder; promote it into a general resolver every
site calls:

```python
class OnboardingStage(enum.Enum):
    NOT_SIGNED_IN, NEEDS_HANDLE, NEEDS_AGENT, NEEDS_MCP_CONNECTION, NEEDS_LIVE, READY

@dataclass(frozen=True)
class OnboardingState:
    stage: OnboardingStage
    next_url: str

async def resolve_onboarding_state(
    db, user, *,
    target_match=None,        # match-scoped intent (join) vs global (nav, /play)
    target_agent=None,        # when a specific agent's provider is the subject
    require_live: bool = False,  # nav label tolerates CONNECTED_NOT_LIVE; join needs LIVE
) -> OnboardingState: ...
```

- The resolver owns the **ordering** (the gate ladder) and the **canonical
  next-step URL**, including `?next=` threading back to the intent.
- It reads provider state **only** through `provider_readiness`.
- `require_live` is how nav (label may stay on `CONNECTED_NOT_LIVE`) and join
  (needs `LIVE` to seat) share one ladder instead of two — the "label vs gate"
  distinction, as a parameter.
- Pages keep only their **local** concern (rendering the form, the seat-hold
  countdown). The chain logic is centralized.

### 3. Per-site adoption (before → after; ⚠ = intentional behavior change)

| Site | Before | After |
|---|---|---|
| **Nav CTA** | "Play now" on `first_connected_at`-ever | Calls resolver (`require_live=False`); label maps from stage. ⚠ "Play now" now means **has current MCP setup** (recent), not "ever connected" — a 90-day-stale connection stops showing "Play now". |
| **`/play`** | sign-in + handle, then lobby | Calls resolver; routes a provider-less/agent-less user to their first unmet gate instead of dropping them at the lobby. ⚠ behavior change — **flag as a decision** (some may want `/play` to stay lobby-first). |
| **Post-login** | `next=="/"` & 0 agents → `/me/agents` | `next=="/"` → resolver decides the gate. ⚠ destination becomes `/me/agents/new` (create) rather than the list, and also catches the missing-handle gate. |
| **Agent-create** | `enabled_provider_values_on_nonpaused_connections` | Resolver with `target_agent`. ⚠ "set up" now means `provider_has_current_setup` (MCP-recent), so an enabled-but-not-recently-MCP-connected provider is now sent to connect instead of skipping it. |
| **Join gate** | `has_ai_agent` only | Resolver with `target_match` for the pre-pick gates (handle/agent); post-pick seat confirm/hold reads the **same tri-state** (`LIVE` boundary). Net logic ≈ same, now shared. |

Every ⚠ row is enumerated here on purpose; the plan must keep a before/after note
for each and the reviews must confirm none is accidental.

---

## Scope boundaries (files)

**Edited:**
- `app/engine/connection_health.py` — add `ProviderReadiness` + `provider_readiness`; re-express predicates over it.
- `app/routes/nav_context.py` — promote `compute_nav_cta` → `resolve_onboarding_state` (+ `OnboardingStage`/`OnboardingState`); keep `compute_nav_cta` as a thin caller for the label.
- `app/routes/auth.py` — post-login redirect calls the resolver.
- `app/routes/agents_create.py` — post-create destination calls the resolver.
- `app/routes/web_player.py` — `_join_setup_redirect` (and the seat confirm/hold + connect-redirect reads) go through `provider_readiness` / the resolver.
- `app/routes/web_games_catalog.py` — `/play` calls the resolver (pending the `/play` decision below).

**Tests:** unit tests for `provider_readiness` (each boundary) and
`resolve_onboarding_state` (each stage transition + `require_live`), plus
per-entry-point redirect tests.

**Not touched:** models, migrations, MCP bridge, `deps.py` guards (`require_user`
/ disable enforcement stay as-is), seat-hold UX templates, capacity math.

---

## Acceptance criteria

1. `spec.md` contains the disagreement table naming the exact predicate each of
   the five entry points uses today (✅ above) and the agreed unified gate
   definitions.
2. `connection_health.py` exposes one `provider_readiness(...) ->
   ProviderReadiness` tri-state; the ~6 existing predicates are re-expressed over
   it or documented as retained wrappers.
3. `nav_context.py` exposes `resolve_onboarding_state(...) -> OnboardingState`;
   all adopting entry points call it instead of re-deriving the ladder.
4. Every ⚠ behavior change is enumerated with a before/after note, and confirmed
   intentional at the spec + plan reviews.
5. Tests cover the tri-state boundaries and the resolver ladder; Preflight Gate
   (`ruff` + `mypy` + `pytest`) is green.
6. No DB migration; no new module.

## Assumptions carried in (correct any at review)

1. **"Set up your agent to pull"** = the step where the user pastes the
   play-prompt into their MCP client so the agent starts polling for turns
   (today's seat-connect / `provider_loop_running` state). This maps to the
   `CONNECTED_NOT_LIVE → LIVE` transition.
2. The nav label may stay on `CONNECTED_NOT_LIVE` while join requires `LIVE` —
   modeled as `require_live`, not two ladders.
3. "Per-provider MCP connection" is conceptual, derived over
   `connection_providers` toggles; physical storage is unchanged.

## Open questions / risks

- **`/play` routing (decision needed):** route `/play` through the resolver
  (sends setup-incomplete users to their next gate) or keep it lobby-first?
  Recommendation: route through the resolver, since `/play` is the "I want to
  play" funnel. Flagged for Chris.
- **Redirect-loop risk:** five sites changing redirect logic at once is the main
  regression surface. Mitigation: per-entry-point redirect tests asserting the
  exact `Location` for each stage; a "READY user never gets redirected to setup"
  invariant test.
- **"Seen now" vs "looping" for seat-connect auto-redirect:** plan-stage call
  (recommend `LIVE`).
