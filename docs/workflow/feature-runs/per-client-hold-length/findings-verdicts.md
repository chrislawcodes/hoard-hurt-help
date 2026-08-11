# Spec review findings — per-client hold length

Two adversarial reviewers on `spec.md`: `feasibility-adversarial` (F) and
`requirements-adversarial` (R). Every finding gets a row. No finding dropped
without a recorded reason.

**Outcome: STOP. Do not build this spec as written.** The design collides with
three ceilings that sit *below* the client limits it is built on. Two are our own
code; one was never measured. Per the Thin skill's own routing rule — a review
round that surfaces open design questions is the signal this was not a Thin
feature — this needs a design decision before any implementation.

## The three ceilings that invalidate the design

| Ceiling | Value | Source | Verified |
|---|---|---|---|
| Connection counts as dead for turn routing | **90s** | `LIVE_WINDOW_SECONDS`, `app/engine/connection_health_badge.py:26` → `turn_routing.py:52-60` | Yes, read the code |
| "Play loop is running" (gates seat confirmation) | **120s** | `LOOP_RUNNING_WINDOW_SECONDS`, same file `:31` — its comment says "covers the ~25s long-poll hold" | Yes |
| Production edge / proxy request timeout | **~100s, UNMEASURED** | `agent_idle.py:56` comment: "Kept well under common proxy timeouts (~100s)" | Comment verified; the ceiling itself was never measured |

Proposed holds were 240 / 144 / 96s. All three exceed the 90s wall; two exceed
120s. **The binding constraint is not the AI clients.** Every client limit I
measured (180/300/300) is above all three of these.

## Blockers

| # | Lens | Finding | Verdict | Reason |
|---|---|---|---|---|
| F1 | feasibility | A hold >90s makes the holding connection "dead", so `can_connection_claim_turn` refuses it — it cannot serve its own turn until the hold ends. `last_seen_at` is stamped once before the hold and never refreshed inside it. | **blocker — redesign** | Design-invalidating. Verified. Defeats FR-006 outright. |
| F3 / R5 | both | Seat confirmation gates on `last_polled_at` within 120s; `mark_polled` runs once before the hold. A 240s hold reads as not-running for ~half of each cycle, so held seats stop auto-confirming and the match is less likely to start. | **blocker — redesign** | Verified. Same root cause as F1. |
| R4 | requirements | Every measured number is client-side. The production edge ceiling was never measured; the code's own comment puts it at ~100s. If true, all four per-client numbers are moot. | **blocker — measure first** | The single cheapest measurement that could invalidate the whole scheme. Must run before any build. |
| F2 / R1 | both | Once every lane holds, the post-hold return is a bare `{"status":"waiting"}` — `should_stop` / `no_game` / `idle_seconds` become unreachable. The shipped play prompt's ONLY stop condition is `should_stop=true`, so the loop becomes unstoppable. | **fix now — required in any version** | Verified at `agent_play_next_turn.py:761` vs `:723-725`. |
| F4 / R2 | both | `_NEXT_TURN_HOLD_SECONDS = 25.0` caps every MCP hold; spec never mentions it. Left in place the feature is a silent no-op. | **fix now — required in any version** | Verified `mcp_tools.py:47`. |
| F4 / R3 | both | `max_hold_seconds` is floor-only (`min(hold, cap)`), and `pace_idle` receives no provider or transport. There is no seam to raise a hold per client. | **fix now — name the interface** | Verified. Spec asserted behaviour without specifying the mechanism. |
| R6 | requirements | The connector polls `/api/agent/next-turns` (plural), which never holds, and sleeps `min(next_poll, 60)`. Shrinking `next_poll` to 5 would make an un-updatable always-on client poll **12× more often, forever**. FR-004/SC-004 protect a path it does not use. | **blocker — redesign** | Verified `connector:1812,1839,1931`. A regression on the one client we cannot update. |

## Fix now (carry into whatever design replaces this)

| # | Lens | Finding | Verdict | Reason |
|---|---|---|---|---|
| F7 / R21 | both | The provider is not reliably bound to the calling client: OAuth resolution passes `provider=None`; a DCR-id miss falls through to the user's single live connection whatever its provider; **key sign-in discards the provider entirely** — and Antigravity is the only client that must use key sign-in and the only one with an unraisable wall. | fix now | Verified path-by-path by the reviewer. FR-002/FR-003 rest on this. |
| F8 / R11 | both | `next_poll_after_seconds` and the idle status are computed *before* the hold and returned *after* it — stale by the hold length. Worked example: a 240s hold can land the client 50s past a start whose first turn has a 60s deadline. | fix now | Recompute at hold end. |
| R10 | requirements | Practice Arena's 365-day horizon: under always-hold this becomes a permanent hold re-checking every 5s per seated connection, forever, and `should_stop` can never fire. Spec called it "unchanged" — its *cost* changes ~60×. | fix now | Spec claim was wrong. |
| F5 | feasibility | FR-001+FR-004 break shipped tests that assert instant returns and `hold == 0.0` on the HTTP route, with no override available (`agent_next_turn.py:29` passes no cap). | fix now | Tests must be rewritten, and the route needs an injection point. |
| R12 | requirements | No requirement bounds server-side cost. An idle session goes from ~1 query/300s to ~1/5s. SC-002 measures client polls only and hides the rise. | fix now | Needs a stated ceiling and a check against the pool from #646. |
| R15 | requirements | Nothing logs the resolved provider, hold length, or transport (`turn_poll_idle` is DEBUG; prod runs at INFO), so SC-002/SC-003 have no data source. | fix now | "Did it work in prod?" is currently unanswerable. |
| R7 / R8 / R14 | requirements | FR-005, FR-001 and FR-003 are circular or unjustified as worded ("small", "a period it can bridge", an unexplained 96). | fix now | Replace with numbers; derive 96 from a stated 120 assumption. |
| F6 | feasibility | The spec's lane table says 40s for the MCP live-game hold; the real MCP baseline is 25s. | fix now | Factual correction. |
| F12 | feasibility | The "unknown provider string falls back" edge case is false — `FlexibleEnumType` raises on an unrecognised value when loading the row, before the fallback runs. | fix now | Delete the edge case or the dead branch. |
| R9 | requirements | Undefined whether per-provider holds apply to the live-game lane. US3 reads as "yes", which would change the one phase the evidence says works (56/56 moves). | fix now | Must be decided explicitly. |
| R13 | requirements | The 30s HTTP figure does not follow the spec's own 80% rule (0.8 × 40 = 32), and 40 is restated in three places. | fix now | Derive it or justify it. |
| R22 | requirements | Hermes and OpenClaw are supported clients, never named in the spec, silently in the 96s fallback. | fix now | Name them in the measurement table. |
| R16 | requirements | SC-001..SC-004 are manual-only or unmeasurable as written; SC-002's "≤6" is provider-dependent. | fix now | Split into CI assertion + named manual run. |
| R17 / R18 | requirements | Missing edge cases: a hold outliving the client's real limit (silent, undetected), and a deploy landing mid-hold — the kill window grows from ≤40s to ≤240s on a single-instance app with a documented deploy-freeze history. | fix now | Both need requirements. |
| R19 / R20 | requirements | US2's survival time is undefined (really 10 min, per unchanged `IDLE_STOP_SECONDS`), and both independent tests name no observable. | fix now | Editorial but load-bearing for verification. |
| F15 / R26 | both | Shipped docs and prompt text say the hold is "~25s" in two mirrored places, plus the architecture doc's lane description. A behaviour change contradicting shipped prompt text is in scope. | fix now | Add to scope. |
| F13 | feasibility | During a long hold another connection can steal the seat's pin, because the holder reads as dead after 90s. | fix now | Same root cause as F1; worth its own requirement. |
| F10 / R23 | both | NFR-002 (tunable without a deploy) is already failed by the module as written — all constants hardcoded, unlike #646's settings-backed pool. | fix now | Wire to settings or delete the NFR. |
| F16 | feasibility | `tests/test_mcp.py:245` builds a fake connection with no `provider` attribute; the obvious implementation raises `AttributeError` there. | fix now | Test must be updated. |
| R24 / R25 | requirements | The "no cleanup required" claim is asserted not derived (orphan window grows 6×); the lane table conflates "no game" with "a game with no scheduled_start". | fix now | Editorial precision. |

## Deferred

| # | Lens | Finding | Verdict | Reason |
|---|---|---|---|---|
| F9 | feasibility | FR-004/SC-004 aim at an endpoint the connector does not use, so SC-004 passes vacuously. | **defer into the re-spec** | Superseded by R6, which is the sharper version of the same defect and is a blocker. |
| F11 | feasibility | PR #646 is open, not merged; the pool is still unsized on `main`. | **defer — tracked** | Known and intentional; #646 is the stated dependency. Recorded so the ordering is not lost. |
| F14 | feasibility | The observed 153 polls were not caused by an over-large wait number — the clients ignored the number entirely. The spec's causal claim in "Why this exists" overstates it. | **fix now (wording)** | The *mechanism* claim (a client cannot wait) stands; the *number* claim does not. Correct the narrative rather than the design. |
| F17 | feasibility | SC-001/SC-003 are not CI gates and name no owner. | **defer into the re-spec** | Same ground as R16, which is more specific. |

## Decisions needed before a re-spec

1. **What is the production request ceiling?** Measure a held request through
   Railway and Cloudflare against `agentludum.com` before anything else. If it is
   ~100s, per-client holds collapse to one number under ~80s and most of this
   feature disappears.
2. **Do we raise the 90s / 120s internal windows and re-stamp the heartbeat during
   a hold, or cap holds below 90s?** The first unlocks per-client holds but touches
   turn routing, seat confirmation, the connection badge, lobby onboarding and
   model verification. The second is small and safe and makes per-client tuning
   almost pointless.
3. **What does the connector get?** Its wait number cannot shrink without a 12×
   request-rate rise on a client we cannot update.
