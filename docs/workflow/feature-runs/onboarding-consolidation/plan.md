# Plan — Onboarding / Auth Flow Consolidation

## Review Reconciliation

- review: reviews/spec.codex.feasibility-adversarial.review.md | status: accepted | note: Manual sub-agent feasibility pass (codex CLI unavailable); all findings folded into spec v2.
- review: reviews/spec.gemini.requirements-adversarial.review.md | status: accepted | note: Manual sub-agent requirements pass (gemini CLI unavailable); all findings folded into spec v2.
- review: reviews/plan.codex.implementation-adversarial.review.md | status: accepted | note: Manual sub-agent implementation pass; findings folded into plan (cascade order, query bound, HTMX endpoints).
- review: reviews/plan.gemini.testability-adversarial.review.md | status: accepted | note: Manual sub-agent testability pass; findings folded into plan test lists + verifications.

## Architecture decisions

### AD-1 — Two new symbols, both thin wrappers on existing modules

| New symbol | Lives in (existing module) | Wraps / reuses |
|---|---|---|
| `ProviderReadiness` (enum) + `provider_readiness()` | `app/engine/connection_health.py` | `provider_has_current_setup:386`, `provider_has_live_current_setup:400`, `provider_loop_running:426` — **calls these three, adds no new SQL** |
| `PlaySetupStage` (IntEnum) + `PlaySetupState` (dataclass) + `resolve_play_setup_state()` | `app/routes/nav_context.py` | `user_has_agent:114`, `provider_readiness`, `safe_internal_next` (`web_support.py:29`), `PROVIDER_LABELS` (`provider_labels.py:8`) |

### AD-2 — Naming (avoid collision with existing onboarding type)

`app/engine/agent_onboarding.py:37` already defines `AgentOnboardingState`
(in-game progress: waiting→playing) and `connection_health.py:52` defines
`ConnectionHealth` (per-connection machine badge). To avoid conflation, this
feature uses a distinct **play-setup** vocabulary, **not** "onboarding":

- Provider setup state: **`ProviderReadiness`** — `NO_MCP_CONNECTION` /
  `CONNECTED_NOT_LIVE` / `SEEN_NOT_POLLING` / `LIVE`.
- The gate ladder: **`PlaySetupStage`** (IntEnum) — `NOT_SIGNED_IN` /
  `NEEDS_HANDLE` / `NEEDS_AGENT` / `NEEDS_MCP_CONNECTION` / `NEEDS_LIVE` /
  `READY`.
- Result: **`PlaySetupState(stage, next_url)`**; resolver
  **`resolve_play_setup_state(...)`**. `compute_nav_cta` becomes a thin caller.

> Spec v2 uses `OnboardingStage`/`resolve_onboarding_state` in prose; the plan
> renames to `PlaySetupStage`/`resolve_play_setup_state`. The behavior is
> identical — this is the implementation name of record.

### AD-3 — `provider_readiness` resolves as a **top-down cascade, first match wins**

Evaluate highest-readiness first so `LIVE` always wins — required for
correctness with **non-MCP providers** (hermes/openclaw), where the predicates
fall back to liveness-free / `last_seen_at`-based checks and can otherwise
disagree (a non-MCP connection with a fresh `last_polled_at` but stale
`last_seen_at` is genuinely `LIVE` even though `provider_has_live_current_setup`
→ `provider_is_covered` is False):

```
if   provider_loop_running(...):           return LIVE
elif provider_has_live_current_setup(...): return SEEN_NOT_POLLING
elif provider_has_current_setup(...):      return CONNECTED_NOT_LIVE
else:                                      return NO_MCP_CONNECTION
```

Non-MCP fallbacks (from `connection_health.py`): `provider_has_current_setup` →
`provider_enabled_on_any_connection` (liveness-free, :397);
`provider_has_live_current_setup` → `provider_is_covered` (:405);
`provider_loop_running` keys on `last_polled_at` only (no `mcp_connected_at`
guard for non-MCP, :453). The cascade order makes the ladder hold for **both**
MCP and non-MCP providers. Slice 1 must test each boundary with a **non-MCP**
provider too — especially the stale-seen-but-polling case.

PAUSED-only naturally lands in `CONNECTED_NOT_LIVE` (settled decision 3): the
function adds no PAUSED special-case. The predicates already encode it
(`has_current_setup` ignores PAUSED; `live`/`loop` exclude it).

### AD-4 — `require` threshold + multi-agent reduction

`resolve_play_setup_state(db, user, *, target_match=None, target_agent=None,
require: PlaySetupStage = NEEDS_MCP_CONNECTION)`:

- `require` is the minimum stage the caller treats as "done." Nav uses
  `NEEDS_MCP_CONNECTION` (a set-up agent shows "Play"); join-confirm uses `READY`.
- **`NEEDS_LIVE` is non-blocking at join (spec decision 6).** The two client
  actions are distinct: Part 1 = connect (the `NEEDS_MCP_CONNECTION` gate, a real
  redirect); Part 2 = paste the play-prompt to start polling (the
  `SEEN_NOT_POLLING → LIVE` jump). Part 2 is surfaced **only after a match is
  joined**, on the held-seat page, and never hard-redirects: the seat is held and
  auto-confirms the instant `provider_readiness` reads `LIVE` — because the user's
  poll loop may already be running (one loop serves all joined matches). The
  play-prompt is an optional nudge, not a gate. This is already how
  `_seat_user_agent` behaves (a `provider_loop_running` agent confirms immediately
  and skips the prompt) — keep it.
- **Global intent** (no `target_agent`): reduce over the user's AI agents to the
  **most-ready** one (settled decision 2). Excludes `kind=bot`,
  `archived_at IS NOT NULL`, and `provider IS NULL` agents.
- Owns the canonical `next_url` (incl. `?next=` via `safe_internal_next`).

**Query-cost bound (the reduction must NOT be naive).** `provider_readiness`
issues up to 3 predicate queries; a naive per-agent loop is up to 3·K queries on
a hot path (`compute_nav_cta` runs on every full page load — though it is skipped
for HX requests, `nav_context.py:152`). The reduction therefore must:
1. **Dedupe agents → distinct providers** first (agents share providers).
2. **Early-exit on the caller's `require` bar:** evaluate the cheap-to-clear
   predicate first and stop at the first provider that reaches `require`. For nav
   (`require=NEEDS_MCP_CONNECTION`) that means: as soon as one provider clears
   `provider_has_current_setup`, return — no need to evaluate `LIVE`/`SEEN` or any
   other provider.
This keeps the **common single-provider ready user at ~1 query** (today's cost),
rising to ~1 query per distinct *unready* provider in the worst case — not 3·K.
Slice 2 states the resulting per-page query bound and a test asserts it.

### AD-5 — Handle gate stays in `deps.py`

`require_user_with_handle` (`deps.py:56`) keeps owning the handle bounce. The
resolver's `NEEDS_HANDLE` only fires for the bare-`get_current_user` entry points
(`/play`, `join_form`, post-login). No second handle check is added.

## Reuse decisions (from `reuse-report.md`)

- **Reuse, don't re-derive:** the three `connection_health` predicates (AD-3),
  `safe_internal_next`, `PROVIDER_LABELS`, `user_has_agent`,
  `confirm_seat_if_live`.
- **Extend → map from signal:** the join-form `live`/`offline`/`unconfigured`
  strings (`web_player.py:252-273`), the agent-list badge (`agents_list.py:54` +
  `agents_health_presenter._readiness_state`), agent-detail readiness
  (`agents_detail.py:138`), the connections-page auto-forward
  (`connections_pages.py:132,219`) — all derive from `provider_readiness`.
- **Replace:** the nav's `user_has_connected_agent` ("connected once") bar for the
  Play CTA → `provider_readiness ≥ CONNECTED_NOT_LIVE` (intentional ⚠).
- **Out of scope:** `_live_status_context` machine banner; the pre-existing dead
  `//` branch in `safe_internal_next:43` (flag only).

## Wave / slice breakdown

Each slice is ≤ ~300 changed lines and ends at a stable interface boundary
(types before callers), so a `[CHECKPOINT]` diff review covers one coherent unit.

### Slice 1 — `ProviderReadiness` signal (foundation, no behavior change) `[CHECKPOINT]`
- Add `ProviderReadiness` + `provider_readiness()` to `connection_health.py` as a
  top-down cascade over the three predicates (AD-3). No callers change yet.
- Tests: the four boundaries incl. PAUSED-only → `CONNECTED_NOT_LIVE`, **plus the
  same four boundaries via a non-MCP provider (hermes/openclaw)** — especially the
  stale-seen-but-polling case that stresses the cascade order (Finding 2). Reuse
  `make_connection` + `mcp_connected_at`/`last_polled_at`/`last_seen_at` patterns
  from `test_coverage_health_and_join_gate.py` / `test_agent_detail_fixes.py`.
- **Query-count test:** attach a `before_cursor_execute` listener, call
  `provider_readiness` once, assert it issues **≤3** queries (locks "no 7th
  predicate" — replaces the earlier grep idea, which proves nothing since the
  queries live in the callees).
- Est: ~140 lines. Pure addition; zero redirect risk.

### Slice 2 — `resolve_play_setup_state` resolver (**ships the nav ⚠ change**) `[CHECKPOINT]`
- Add `PlaySetupStage`/`PlaySetupState`/`resolve_play_setup_state()` to
  `nav_context.py` (AD-2/AD-4), with provider-dedup + early-exit reduction. Reimplement
  `compute_nav_cta` as a thin caller (only consumer this slice).
- **This slice is behavior-changing**, not pure foundation: it lands the nav's
  "ready" bar swap (`first_connected_at`-ever → `provider_has_current_setup`,
  spec §4 ⚠ row 1). The `[CHECKPOINT]` review treats it as such.
- Tests: each stage transition; `require` threshold; multi-agent most-ready
  reduction (incl. a **mixed MCP + non-MCP** provider set); `provider IS NULL` /
  `kind=bot` / `archived_at` agents excluded; the **per-page query bound** from
  AD-4 (single-provider ready user ≤ ~1 readiness query).
- Est: ~190 lines.

### Slice 3 — Adopt at the redirect entry points `[CHECKPOINT]`
- `auth.py` post-login, `agents_create` destination, `web_games_catalog` `/play`,
  `web_player._join_setup_redirect` → call the resolver.
- Tests: per-entry-point redirect `Location` — **including a named `/play`
  redirect test** for the ⚠ lobby-drop → next-gate change (spec §4); the
  `/play ⇄ /me/connections` loop-guard with a seen-but-not-polling fixture (reuse
  `test_smart_join_flow.py` cookie/redirect harness); a "READY user is never
  redirected to setup" invariant.
- Est: ~160 lines.

### Slice 4 — Adopt the readiness signal at the per-provider display/seat sites `[CHECKPOINT]`
- `web_player` join-form strings (`:252-273`) derive from `provider_readiness`;
  seat confirm/hold/connect **and the `seat_connect_status` poll (`:649`)** read
  via the signal; `connections_pages` auto-forward via `SEEN_NOT_POLLING` on
  **both** the page-load path (`:156-159`) **and** the `live_status_fragment` HTMX
  poll path (`:216-227`) — leaving either on the old predicate re-creates the
  split (Finding 3); `agents_list` + `agents_detail` readiness via the signal;
  `seat_hold.confirm_seat_if_live` shares the `LIVE` boundary.
- Tests: connections-page **load-path and poll-path** bar parity with the seat
  pages; named before/after `agents_list` badge and `agents_detail` readiness
  tests (the ⚠ swaps, spec §4); a `confirm_seat_if_live` ↔ resolver `LIVE` parity
  test asserting agreement across **all four** states (incl. the non-MCP
  stale-seen-but-polling case), not just one happy path.
- Est: ~210 lines.

Slices 1→2 are a hard dependency (resolver uses the signal). 3 and 4 both depend
on 1+2.

## Parallelization

- Slices 1, 2 are serial (2 depends on 1).
- Slices 3 and 4 share `web_player.py`, so they are **not** safe to parallelize
  (overlapping write set). Run them serially. No `[P:]` annotations.

## Test strategy

Reuse `tests/factories.py` (`make_user`, `make_connection`, `make_agent`,
`seat_player`). Set `mcp_connected_at`/`last_polled_at`/`last_seen_at` on the
returned objects (the factory doesn't expose them as kwargs — match existing
tests, don't change the factory). Redirect/loop tests reuse the
`test_smart_join_flow.py` client+cookie harness. Preflight Gate (`ruff` + `mypy` +
`pytest`) green at each `[CHECKPOINT]`.

## Residual Risks

- **#444 redirect loop may already be live** (seat pages on `has_current_setup`
  vs connections page on `has_live_current_setup`). *verification:* Slice 3's
  `/play ⇄ /me/connections` loop-guard test with `mcp_connected_at` recent +
  `last_polled_at` stale must show no redirect cycle; run it against `origin/main`
  first to confirm it reproduces the loop pre-fix, then green post-fix.
- **`provider_readiness` silently becomes a 7th predicate.** *verification:* a
  `before_cursor_execute` SQL-counter test asserts one `provider_readiness` call
  issues **≤3** queries (a grep over its body proves nothing — the queries are in
  the callees). Slice 1.
- **Nav query-cost regression on a hot path** (the most-ready reduction over many
  agents). *verification:* Slice 2 query-bound test — a single-provider ready user
  resolves with ≤ ~1 readiness query (no 3·K blow-up); dedup + early-exit per AD-4.
- **Poller vs resolver drift on `LIVE`.** *verification:* Slice 4 parity test:
  for the same fixtures across **all four** readiness states (incl. non-MCP
  stale-seen-but-polling), `confirm_seat_if_live` and `resolve_play_setup_state(...,
  require=READY)` agree on whether the seat is `LIVE`.
- **Multi-agent reduction regresses the nav** (a user with one ready + one
  unready agent should still see "Play now"). *verification:* Slice 2 test asserts
  most-ready wins for that exact mix.
- **Paused-only CTA stays "start your AI"** (accepted limitation, decision 3).
  *verification:* Slice 1 unit test asserts a paused-only connection resolves to
  `CONNECTED_NOT_LIVE` (documents it so it can't regress to a worse state).
- **Behavior changes at the ⚠ sites are unintended.** *verification:* each ⚠ row
  in spec §4 has a before/after redirect test in Slice 3/4; the diff review
  confirms no ⚠ outside the enumerated set.
