# Spec — Onboarding / Auth Flow Consolidation

**Slug:** `onboarding-consolidation`
**Path:** Feature Factory (full) · platform feature
**Scope dirs:** `app/engine/`, `app/routes/`

> **Revision history**
> - **v2 (this doc)** — refreshed after PR **#444** ("Use MCP connection flow for AI setup") merged to main, and after an independent adversarial review (manual sub-agent pass; the FF codex/gemini lenses can't run in this container). Changes: predicate inventory is now 6 (not ~3) because #444 added three; 4 more call sites brought into scope; `require_live` boolean replaced with a min-stage threshold; multi-agent reduction rule, PAUSED placement, handle-gate ownership, and the #444 redirect-loop risk added.
> - v1 — initial spec.

## Summary

We keep hitting bugs in "how a user gets an AI agent into a game" because the
question **"is this provider ready?"** is answered by **six different,
drifting predicates** across **~9 call sites**, and several pages re-derive the
onboarding ladder themselves. They disagree at the edges, so users land on the
wrong page, see the wrong CTA, or get bounced between pages.

PR #444 (just merged) made this **worse, not better**: chasing a real product
need (MCP providers need a recent OAuth token) it *doubled* the readiness
predicates (3 → 6) and spread a stricter gate across more pages as ad-hoc
redirects — without a single source of truth. It also introduced a concrete
two-page redirect-loop risk (see Risks). #444 is not wrong to revert; its rule is
correct. The fix is to **unify** what it scattered.

This feature centralizes the logic onto **two modules we already have** — no new
module:

1. **`app/engine/connection_health.py`** gains **one provider-readiness signal**
   — a small state enum — and the 6 overlapping predicates are re-expressed over
   it (or kept as documented thin wrappers).
2. **`app/routes/nav_context.py`** promotes `compute_nav_cta` into a shared
   **`resolve_onboarding_state(...)`** that returns the first unmet gate plus the
   URL to send the user to. Every entry point calls it instead of rolling its
   own ladder.

The user-facing pipeline this expresses: **sign in → handle → create agent
(picks the provider) → connect that provider's MCP connection → provider goes
live / starts pulling → join the requested game.**

No **new** DB migration. Storage stays one-connection-per-user + per-provider
`connection_providers` toggles. (#444 already landed migration `0038`, a pure
rename `connections.mode_a_at` → `connections.mcp_connected_at`; we build on that
column, we don't add one.)

## Problem

There is no single object that represents "how far along is this user toward
playing," and no single answer to "is this provider ready." Each page invents
its own slice, so:

- The **same question** is answered by **six predicates** that disagree at the
  edges (stale vs recent MCP, seen-now vs polling-now, paused handling).
- Fixes have been **piecemeal** — #444 is the latest example: it re-pointed some
  sites to a stricter predicate but left others behind and added new redirects.
- The "signed in but no provider / not currently polling" path is spread across
  `auth.py`, `deps.py`, `agents_create.py`, `agents_list.py`, `agents_detail.py`,
  `connections_pages.py`, and `web_player.py`.

## Goal

One intent-driven onboarding pipeline whose "next step" decision is computed in
**one** place, and whose "is this provider ready?" question is answered by **one**
readiness signal — both living in modules that already exist.

## Non-goals

- **No new module or subsystem.** Consolidate onto `connection_health.py` +
  `nav_context.py` only. (An optional rename of `nav_context.py` →
  `onboarding_state.py` is cosmetic; deferred to the plan.)
- **No new DB migration.** Storage and the MCP OAuth bridge are untouched; we
  reuse the `mcp_connected_at` column #444 renamed.
- **Do not revert #444's product rule** — MCP providers genuinely need a recent
  (90-day) MCP connection. We keep that meaning; we just stop duplicating it.
- **Do not revisit strategy-first ordering** — agent-before-connection stays.
- **Do not redesign the seat-hold / connect-countdown UX.** Only its "what is
  missing?" decision routes through the shared logic.
- **Seat-capacity math is out of scope** (`active_matches_for_provider`,
  `live_provider_capacity`, `is_join_blocked`).

---

## Current-state audit (the disagreement table — post-#444)

How each call site answers "is this provider ready?" / "what's the next step?"
**today**, with verified file:line and predicate. Sites marked **⚠** will behave
differently after consolidation, on purpose.

### A. Entry points that pick the next onboarding step

| # | Entry point | File:line | Predicate today | Note |
|---|---|---|---|---|
| 1 | **Nav "Play" CTA** | `nav_context.py:128` (`compute_nav_cta`) | **"connected once"** — `user_has_connected_agent` (`:42`), provider enabled on a connection whose `first_connected_at` is set (`:66`) | Deliberately not live-now so the label doesn't flap. |
| 2 | **`/play` hub** | `web_games_catalog.py:68` (`operator_join_page`) | **none** — sign-in + handle only, then redirect to the lobby | Provider-less / agent-less users are dropped at the lobby, not routed to setup. |
| 3 | **Post-login redirect** | `auth.py:119` (`google_callback`) | **agent count only** — 0 non-archived `kind=ai` agents → `/me/agents` (the list) | Only fires when `next == "/"`. Ignores handle + provider. |
| 4 | **Agent-create destination** | `agents_create.py:238` | **"enabled on a non-paused connection"** — `enabled_provider_values_on_nonpaused_connections` (`connection_health.py:502`) | Looser than `provider_has_current_setup`: an enabled-but-not-recently-MCP-connected provider counts as set up here. |
| 5 | **Join setup gate** | `web_player.py:170` (`_join_setup_redirect`) | **agent existence only** — inline `any(agent.kind == AgentKind.AI ...)` (`:186`), none → `/me/agents/new` | Not a named predicate; an inline check. |

### B. Per-provider readiness checks during join/seat/connect (the sprawl)

| # | Site | File:line | Predicate today | Meaning |
|---|---|---|---|---|
| 6 | Join-form status badge | `web_player.py:268,270` | `provider_loop_running` then `provider_has_current_setup` | live / offline / unconfigured |
| 7 | Seat confirm vs hold | `web_player.py:370` (`_seat_user_agent`) | `provider_loop_running` (`connection_health.py:426`) | "an AI is polling right now" |
| 8 | Held-seat connect redirect | `web_player.py:496` (`join_submit`) | `provider_has_current_setup` (`:386`) | redirect to `/me/connections` if not set up |
| 9 | Seat-connect page | `web_player.py:548` (`seat_connect`) | `provider_has_current_setup` | same |
| 10 | Seat-connect poll / escalation | `web_player.py:649` (`seat_connect_status`) | `provider_has_current_setup` | escalate to "reconnect" CTA |
| 11 | Seat-hold poller confirm | `seat_hold.py:56` (`confirm_seat_if_live`) | `provider_loop_running` | background seat confirm |
| 12 | **Connections page** target state | `connections_pages.py:132,137,219` | **`provider_has_live_current_setup`** (`:400`) + `provider_has_current_setup` | "seen right now" — **a different bar than the seat pages** |
| 13 | Agent list readiness badge | `agents_list.py:54` | `enabled_provider_values_on_nonpaused_connections` | ready vs needs-connecting |
| 14 | Agent detail readiness | `agents_detail.py:139` | `provider_is_covered` (`:277`) | "live now" (seen) |

**The core finding:** "is this provider ready?" is answered by **six predicates**
spread over **14 sites**, and at least three *different* readiness bars are in
active use at once:

- **"has current setup"** (`provider_has_current_setup`, MCP-recent) — sites 4*, 6, 8, 9, 10.
- **"seen now"** (`provider_has_live_current_setup` / `provider_is_covered`) — sites 12, 14.
- **"polling now"** (`provider_loop_running`) — sites 6, 7, 11.

Plus "connected once" (`first_connected_at`) at the nav (1) and "enabled on
non-paused" at agent-create/list (4, 13). #444 is what pushed sites 8–12 onto the
stricter `*_current_setup` / `*_live_current_setup` predicates while leaving 1,
4, 13, 14 on their old bars — creating the seat-page (`has_current_setup`) vs
connections-page (`has_live_current_setup`) split that is the new loop risk.

### The 6 predicates in `connection_health.py` being consolidated

| Predicate | Line | Answers | Added by #444? |
|---|---|---|---|
| `provider_is_covered` | 277 | a *live* (seen) connection has the provider enabled | no |
| `provider_enabled_on_any_connection` | 306 | provider enabled on any non-deleted connection | no |
| `provider_has_recent_mcp_connection` | 340 | MCP connection used within `MCP_CONNECTION_VALID_DAYS` (90d) | **yes** |
| `provider_has_current_setup` | 386 | MCP-recent for MCP providers, else enabled | **yes** |
| `provider_has_live_current_setup` | 400 | that current setup is connected *right now* (seen) | **yes** |
| `provider_loop_running` | 426 | an AI is actually *polling* right now (`last_polled_at`) | no (but #444 added `mcp_connected_at` guard) |

Set-level helpers `enabled_provider_values` (475) / `_on_nonpaused_connections`
(502) stay for "which providers to offer / list," but their use as a *readiness*
signal (sites 4, 13) is replaced by the unified signal.

---

## Target design

### 1. The provider-readiness signal (`connection_health.py`)

One function is the single answer to "where is this provider in setup?" The
review showed the live code uses **three** meaningful bars, not two, so the
signal must distinguish them rather than collapse to a boolean:

```python
class ProviderReadiness(enum.Enum):
    NO_MCP_CONNECTION = "no_mcp_connection"   # no usable setup (gate: connect)
    CONNECTED_NOT_LIVE = "connected_not_live" # set up, MCP client not signed in now
    SEEN_NOT_POLLING = "seen_not_polling"     # MCP client online, but no AI polling yet
    LIVE = "live"                             # an AI is polling for turns right now

async def provider_readiness(db, user_id, provider) -> ProviderReadiness: ...
```

Boundary definitions (built over the predicates #444 already wrote — we wrap,
not rewrite):

| State | Defined as |
|---|---|
| `NO_MCP_CONNECTION` | `not provider_has_current_setup(...)` |
| `CONNECTED_NOT_LIVE` | `provider_has_current_setup(...)` and not `provider_has_live_current_setup(...)` |
| `SEEN_NOT_POLLING` | `provider_has_live_current_setup(...)` and not `provider_loop_running(...)` |
| `LIVE` | `provider_loop_running(...)` |

This four-state signal is the reconciliation of the three bars: the nav can treat
anything ≥ `CONNECTED_NOT_LIVE` as "ready to show Play"; the seat pages can require
`LIVE`; the connections page's "you're connected, go back" auto-forward keys off
`SEEN_NOT_POLLING` (matching its current `provider_has_live_current_setup`
behavior) **without** a separate predicate. The six predicates remain as the
internal building blocks of these four boundaries.

**PAUSED handling (decided — fold into `CONNECTED_NOT_LIVE`):** a paused-only
connection naturally lands in `CONNECTED_NOT_LIVE` — `provider_has_current_setup`
is true (it ignores PAUSED) while `provider_has_live_current_setup` and
`provider_loop_running` exclude it. We **keep** that and add **no** special PAUSED
state, so the signal stays at four states (simpler). Accepted limitation: the CTA
for a paused-only provider then reads "start your AI" rather than "resume your
connection" (see Residual limitation).

### 2. The onboarding-state resolver (`nav_context.py`)

Promote `compute_nav_cta` into a general resolver every site calls:

```python
class OnboardingStage(enum.IntEnum):
    NOT_SIGNED_IN = 0
    NEEDS_HANDLE = 1
    NEEDS_AGENT = 2
    NEEDS_MCP_CONNECTION = 3   # NO_MCP_CONNECTION
    NEEDS_LIVE = 4             # CONNECTED_NOT_LIVE / SEEN_NOT_POLLING
    READY = 5                  # LIVE

@dataclass(frozen=True)
class OnboardingState:
    stage: OnboardingStage
    next_url: str

async def resolve_onboarding_state(
    db, user, *,
    target_match=None,        # match-scoped (join) vs global (nav, /play, post-login)
    target_agent=None,        # when a specific agent's provider is the subject
    require: OnboardingStage = OnboardingStage.NEEDS_MCP_CONNECTION,  # min bar this caller demands
) -> OnboardingState: ...
```

- `require` replaces v1's `require_live` boolean. It is the **minimum stage this
  caller treats as "done."** Nav uses `NEEDS_MCP_CONNECTION` (a set-up agent shows
  "Play"); join-confirm uses `READY` (must be polling). An `IntEnum` makes the
  threshold comparison explicit and supports the three real bars.
- **Multi-agent reduction rule (decision — see Open Decisions):** for global
  intent (no `target_agent`), the resolver reports the **most-ready** agent's
  stage. This preserves today's nav semantics ("any connected agent ⇒ Play").
  This rule is an acceptance criterion and gets a test.
- The resolver excludes `kind=bot` agents, `archived_at IS NOT NULL` agents, and
  agents with `provider IS NULL`.
- It owns the gate **ordering** and the canonical **next-step URL** (incl. `?next=`
  threading). Pages keep only local concerns (rendering a form, the countdown).

### 3. Handle-gate ownership

`deps.py:56` `require_user_with_handle` already 303-redirects handle-less users.
To avoid two sources of truth: **`deps.py` keeps owning the handle gate** for
routes that depend on it; the resolver's `NEEDS_HANDLE` stage only covers the
entry points that use bare `get_current_user` (`/play`, `join_form`, post-login).
The spec does not move handle logic into the resolver for `require_user_with_handle`
routes.

### 4. Per-site adoption (before → after; ⚠ = intentional behavior change)

| Site | Before | After |
|---|---|---|
| **Nav CTA** (1) | "Play now" on `first_connected_at`-ever | Resolver, `require=NEEDS_MCP_CONNECTION`. ⚠ "Play now" now means **has current MCP setup** (recent), not "ever connected" — a 90-day-stale connection stops showing "Play now". |
| **`/play`** (2) | sign-in + handle, then lobby | Resolver routes to the first unmet gate. ⚠ behavior change — **decided: route to next gate** (see Resolved decisions). |
| **Post-login** (3) | `next=="/"` & 0 agents → `/me/agents` | Resolver decides the gate. ⚠ destination becomes `/me/agents/new`; also catches missing handle at login. |
| **Agent-create** (4) | `enabled_provider_values_on_nonpaused_connections` | Resolver with `target_agent`. ⚠ "set up" now means `provider_has_current_setup` (MCP-recent) — aligns it with the join flow #444 already tightened. |
| **Join gate + seat** (5–11) | mix of `loop_running` / `has_current_setup` | Pre-pick gates via resolver; per-agent seat reads via `provider_readiness`. Logic ≈ same, now shared. |
| **Connections page** (12) | `provider_has_live_current_setup` | `provider_readiness` `SEEN_NOT_POLLING` boundary — **same bar, shared definition**, closing the seat-page/connections-page split #444 created. |
| **Agent list** (13) | `enabled_provider_values_on_nonpaused_connections` | `provider_readiness` per provider. ⚠ list "ready" badge now matches the create flow + join flow definition. |
| **Agent detail** (14) | `provider_is_covered` ("seen") | `provider_readiness`. ⚠ detail readiness aligns to the shared bars. |

Every ⚠ row is enumerated on purpose; the plan keeps a before/after note for each
and the reviews confirm none is accidental.

---

## Scope boundaries (files)

**Edited:**
- `app/engine/connection_health.py` — add `ProviderReadiness` + `provider_readiness`; re-express the 6 predicates over it.
- `app/routes/nav_context.py` — promote `compute_nav_cta` → `resolve_onboarding_state` (+ stage enum); keep `compute_nav_cta` as a thin caller.
- `app/routes/auth.py` — post-login redirect → resolver.
- `app/routes/agents_create.py` — post-create destination → resolver.
- `app/routes/agents_list.py` — readiness badge → `provider_readiness`.
- `app/routes/agents_detail.py` — readiness → `provider_readiness`.
- `app/routes/web_player.py` — `_join_setup_redirect` + seat confirm/hold/connect reads → resolver / `provider_readiness`.
- `app/routes/connections_pages.py` — target-state + auto-forward → `provider_readiness`.
- `app/routes/web_games_catalog.py` — `/play` → resolver (routes to next gate).
- `app/engine/seat_hold.py` — `confirm_seat_if_live` reads the `LIVE` boundary via the shared signal (kept bit-identical).

**Tests:** unit tests for `provider_readiness` (each of the 4 boundaries, incl.
PAUSED), `resolve_onboarding_state` (each stage + `require` threshold + multi-agent
reduction), per-entry-point redirect `Location` tests, and an explicit
**`/play ⇄ /me/connections` loop-guard test** using a "seen-but-not-polling"
fixture (the #444 risk).

**Not touched:** models, new migrations, MCP bridge, `deps.py` handle/disable
guards, seat-hold UX templates, capacity math.

---

## Acceptance criteria

1. `spec.md` tables how each call site answers readiness today (✅ above,
   post-#444) and the agreed unified boundaries.
2. `connection_health.py` exposes one `provider_readiness(...) ->
   ProviderReadiness` signal; the 6 predicates are re-expressed over it or kept as
   documented wrappers. PAUSED placement is explicit.
3. `nav_context.py` exposes `resolve_onboarding_state(...)`; all 14 sites read
   through the resolver or `provider_readiness` — none keeps its own predicate.
4. The multi-agent global reduction rule (most-ready) is implemented and tested.
5. Every ⚠ behavior change is enumerated with a before/after note and confirmed
   intentional at review.
6. A loop-guard test covers the `/play ⇄ /me/connections` pair for a
   seen-but-not-polling user.
7. No new DB migration; no new module. Preflight Gate green.

## Assumptions carried in (correct any at review)

1. **"Set up your agent to pull"** = the user pastes the play-prompt into their
   MCP client so the agent polls for turns (the `SEEN_NOT_POLLING → LIVE`
   transition).
2. "Per-provider MCP connection" is conceptual, derived over `connection_providers`
   toggles + `mcp_connected_at`; physical storage is unchanged.
3. #444's product rule (90-day MCP recency for Claude/OpenAI/Gemini) is correct
   and stays.

## Resolved decisions (2026-06-16)

All five open questions answered by Chris:

1. **`/play` routing** — **Route to next gate.** A setup-incomplete user is sent
   through the resolver to their first unmet step, then back to play. `/play` is a
   true "get me playing" funnel, not a lobby drop.
2. **Multi-agent reduction** — **Most-ready wins.** For nav / `/play` /
   post-login, the resolver reports the furthest-along agent's stage (one ready
   agent ⇒ "Play now"). Preserves today's nav behavior.
3. **PAUSED placement** — **Fold into `CONNECTED_NOT_LIVE`.** No special state;
   the signal stays at four states. Accepted limitation below.
4. **Connect-page auto-forward bar** — **Seen now (`SEEN_NOT_POLLING`).** The page
   advances the instant the MCP client signs in, before the first turn poll
   (today's snappy behavior).
5. **Post-login destination** — **`/me/agents/new`.** A returning user with zero
   agents lands directly on the create form (strategy-first), not the empty list.

### Residual limitation

- **Paused-only provider shows "start your AI," not "resume."** Folding PAUSED
  into `CONNECTED_NOT_LIVE` (decision 3) keeps the misleading CTA for a user whose
  only connection is paused. Accepted for simplicity.
  *verification:* a unit test asserts a paused-only connection resolves to
  `CONNECTED_NOT_LIVE` (documents the behavior so it can't regress silently); if
  this becomes a real support issue, a distinct `PAUSED` state is a cheap
  follow-up. Severity: low — a user who paused their own connection has context
  for why it isn't running.

## Risks

- **#444 redirect-loop (possibly already live):** seat pages redirect on
  `provider_has_current_setup` while `connections_pages.py` auto-forwards on
  `provider_has_live_current_setup` — different bars pointing at each other. A
  seen-but-stale user could ping-pong **today**. Mitigation: the shared signal
  gives both pages one definition; the loop-guard test (criterion 6) locks it.
  *Verification:* trace `/games/{g}/matches/{m}/connect/{p}` ⇄ `/me/connections`
  with a fixture where `mcp_connected_at` is recent but `last_polled_at` is stale;
  assert no redirect cycle before merge.
- **Cross-cutting redirect changes across 10 files** — main regression surface.
  *Verification:* per-entry-point `Location` tests + a "READY user is never
  redirected to setup" invariant test.
- **`seat_hold.confirm_seat_if_live` must stay bit-identical to the `LIVE`
  boundary** or the background poller confirms seats under different rules than
  the resolver routes them. *Verification:* a shared-constant test asserting both
  resolve `LIVE` from the same `provider_loop_running` result.
