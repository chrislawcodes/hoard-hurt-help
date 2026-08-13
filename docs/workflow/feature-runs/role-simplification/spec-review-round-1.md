# Spec review round 1 — verdicts

Two adversarial lenses ran on `spec.md` v1, foreground and concurrent:
`feasibility-adversarial` (F) and `requirements-adversarial` (R). Both read the
real code and verified their claims. Every finding gets a verdict here; the
"fix now" rows are folded into `spec.md` v2.

## Verdicts

| # | Lens | Finding | Verdict | Reason |
|---|------|---------|---------|--------|
| F1 | feasibility | AC1.1 names `/games/{game}/matches/new` but section 5 opens `/games/{game}/admin/matches/new` — two different routes with different fields and templates | **fix now** | Real contradiction. Resolved by decision D1: one create route survives |
| F2 | feasibility | The 3-match cap lives only in `matches_user.py`; opening rows 3 and 10 makes it bypassable | **fix now** | Blocker. Would silently void contract item 6. Resolved by D1 + D2 |
| F3 | feasibility | AC3.1's grep is unpassable — module names, template dir and 39 test refs all match | **fix now** | Correct. Resolved by D14 (rename the modules and template dir). Note `/api/game-admin` uses a hyphen, so URLs never matched the grep pattern anyway |
| F4 | feasibility | Every game-admin template render hardcodes `is_admin: True`, so a player would see admin nav | **fix now** | Verified at 6 call sites. New AC6.1 |
| F5 | feasibility | Row 5 points at `start_game`, which has none of the player start path's guards | **fix now** | Confirmed privilege escalation. Resolved by D3: admin-page start stays platform admin |
| F6 | feasibility | `create_match` vs `create_match_with_state`: Liar's Dice config is lost SILENTLY because `_load_state(create=True)` fabricates defaults | **fix now** | Verified at `app/games/liars_dice/state.py:100-104`. Resolved by D9; AC pins config round-trip, not row existence |
| F7 | feasibility | The JSON export builder cannot see who owns a seat; needs a viewer argument threaded through two routes | **fix now** | Correct. New AC2.9 names the signature |
| F8 | feasibility | `docs/platform/` asserts the deleted rule in 7 places | **defer** | The scope fence explicitly forbids touching `docs/`. Flagged in the PR with exact line numbers and spawned as a follow-up task. Real, but not mine to fix here |
| F9 | feasibility | AC1.2 cites `create_match`'s limits (1–20); the real limits are the game module's config range plus 3–20 | **fix now** | Verified. AC1.2 rewritten; band explicitly does not change |
| F10 | feasibility | Four test files pin the deleted behaviour; spec named one | **fix now** | Verified. All four listed in Trap 1 |
| F11 | feasibility | Blanket redaction also blanks bot strategy on the owner's own page | **fix now** | Bots are not private. Resolved by D8 |
| F12 | feasibility | The ownership guard cannot be a router-level dependency; use a match-returning one | **accept, plan-level** | No spec change needed; the plan names the dependency |
| R1 | requirements | Same as F2 — cap bypassable through rows 3 and 10 | **fix now** | Duplicate of F2, independently found. Raises confidence |
| R2 | requirements | `CreateGameRequest.mutual_help_mode` has NO enum validation; garbage reaches the column on both admin APIs | **fix now** | Verified at `app/schemas/admin.py:24`. Resolved by D10 |
| R3 | requirements | Narrowing `_is_any_admin` changes admin-only-game visibility at 6+ call sites, not just a nav flag | **fix now** | Verified. This is the correct outcome, but it must be pinned. New AC3.7 |
| R4 | requirements | `build_json_export` has a second consumer, `admin_api.py:71`; a `redact` default could silently strip an admin's export | **fix now** | Verified. AC2.3 now names both URLs |
| R5 | requirements | `per_turn_deadline_seconds` is unbounded on the HTML form; players could set 0 or 86400 | **fix now** | Verified — only the Pydantic path bounds it. Resolved by D11 |
| R6 | requirements | The export includes UNRESOLVED turns, so an opponent could read rivals' in-flight actions before resolve | **fix now** | Verified at `match_export.py:44`. The sharpest finding of the round. Resolved by D5 |
| R7 | requirements | No criterion covers anonymous or disabled-account callers on newly-gated routes | **fix now** | Verified: `get_current_user` does not check `disabled_at`. Resolved by D13 |
| R8 | requirements | "Owner" undefined when `created_by_user_id` is NULL | **fix now** | Verified nullable. Resolved by D6: no owner means platform admin only |
| R9 | requirements | Row 5's note misdescribes the start rule — eligibility is seat-based, not owner-based | **fix now** | Duplicate of F5, independently found. Resolved by D3 |
| R10 | requirements | AC1.2 pins the wrong validator and would loosen the rules | **fix now** | Duplicate of F9 |
| R11 | requirements | The hidden-game rule is pinned on 2 of 13 routes | **fix now** | Verified — none of the game-admin routes calls `require_can_view_game` today. Resolved by D12 |
| R12 | requirements | What a non-admin sees for a bot's strategy is undefined | **fix now** | Duplicate of F11. Resolved by D8 |
| R13 | requirements | "Their own agent" has two plausible definitions (`Player.user_id` vs `Agent.user_id`) | **fix now** | Resolved by D7: `Player.user_id`. Verified non-nullable and set at every seating site |

**Totals:** 26 findings, 24 fix now, 1 deferred with a reason, 1 accepted at
plan level. Nothing dropped.

## Routing check

The `feature-thin` skill says to route to the full Feature Factory if a spec
review surfaces open **design** questions one round cannot settle. It did not.
Every finding was either a place the spec failed to *say* enough, or a place with
one clearly-safest answer that follows from the settled permission model's own
words ("under the rules that already apply"). The model itself was never in
question. **Staying on the Thin path.**

## Design decisions taken in response

| ID | Decision |
|----|----------|
| D1 | ONE create route: `POST /games/{game}/matches/new` gains all seven fields plus per-game config. The game-admin HTML create form and route are deleted |
| D2 | `POST /api/game-admin/{game}/matches` and its cancel twin are deleted — exact duplicates of `/api/admin/*`, and keeping them player-reachable would bypass the cap. Only tests used them |
| D3 | The admin-page start button stays platform admin. Owners start through the existing seat-eligibility route |
| D4 | Cancel stays platform admin on both surfaces — that is the rule that already applies |
| D5 | A non-admin's export contains resolved turns only. Admins keep the in-flight view |
| D6 | A match with `created_by_user_id` NULL has no owner: platform admin only |
| D7 | A seat is "yours" when `Player.user_id == user.id` |
| D8 | Bot seats are not private. Redaction applies only to `AgentVersion.strategy_text` |
| D9 | Converge on `create_match_with_state`, with `state_config` built per game |
| D10 | `CreateGameRequest.mutual_help_mode` gets enum validation — closes the hole on both admin APIs |
| D11 | `per_turn_deadline_seconds` bounded 5–600 on the HTML form, matching the schema |
| D12 | `require_can_view_game` runs on every game-scoped route, before any other check |
| D13 | Every affected route keeps `require_user` semantics: 401 anonymous, disabled account bounced |
| D14 | Rename the four `game_admin_*` modules and the `game_admin/` template dir so the definition-of-done grep can actually pass |
| D15 | `docs/platform/` is not touched — scope fence. Flagged as a follow-up |
| D16 | The legacy `ADMIN_EMAILS` fallback stays. Seven test files depend on it and it may be the live production variable; retiring it risks locking the owner out of their own platform. Reported in the PR |
