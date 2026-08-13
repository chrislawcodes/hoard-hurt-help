# Spec — Role Simplification: three roles become two

**Version:** 2 (after spec review round 1 — see `spec-review-round-1.md`)
**Status:** settled. The permission model was decided by the repo owner before
this run started. This spec RECORDS those decisions and works out how to
implement them. It does not re-open whether players may create matches, or where
the rules dropdown belongs.

**Delivery path:** Thin (`feature-thin` skill).
**Run folder:** `docs/workflow/feature-runs/role-simplification/`

---

## 1. Summary

The platform has three effective roles stored two different ways:

| Role | Where it lives | How you grant it |
|------|----------------|------------------|
| Platform admin | `users.role == UserRole.ADMIN` — a real database column | Promote in the admin UI |
| Game admin | `GAME_ADMIN_EMAILS__<GAME>` environment variables | Edit env vars and redeploy |
| User | The default | Sign in |

Game admin is the odd one out. It needs a deploy to grant, the database has no
record of it, and it mostly gates things that are not really privileges. Players
already create, start, cancel and delete their own matches through
`app/routes/matches_user.py` — they just get fixed settings, while a game admin
gets every knob.

This change collapses the three roles to two: **user** and **platform admin**.
The game-admin role is deleted entirely, along with `require_game_admin` and
every `GAME_ADMIN_EMAILS__*` environment variable.

---

## 2. Target permission model — the contract

This is what gets built. Exactly this.

### Any signed-in player can

1. **Create a match** choosing name, scheduled start, min players, max players,
   per-turn deadline, total rounds and turns per round — within the limits the
   create route validates today (see AC1.2; the allowed band does **not** change).
2. **Seat bots** in a match they own.
3. **Start** their own match through the existing player start route, and
   **delete** their own match, under the rules that already apply to each.
   Cancel stays a platform-admin power — that is the rule that already applies
   (`matches_user.py:243-261`).
4. **Export any match as CSV** and **as JSON**, with other players'
   `strategy_prompt` omitted and with **resolved turns only**.
5. **Open the match detail page** for a match they own, with other players'
   strategy text hidden.
6. Hold at most 3 active matches (`settings.user_active_match_limit`). The cap
   stays, and it must hold on **every** route that can create a match.

### Platform admin can do all of that, plus

7. Choose `mutual_help_mode` when creating a match. Player-created matches
   always get the default, `decay`.
8. Reach the strategy prompts page.
9. Reach the per-game dashboard listing every match in one game.
10. Cancel anyone's match, delete anyone's match, and force-start any match
    through the admin start route.
11. Open **anyone's** match detail page, with every player's strategy text
    visible.
12. Get the **unredacted** JSON export, including every player's
    `strategy_prompt`, and an export that includes the **unresolved** in-flight
    turn.
13. Everything already under `/admin/*`: users, handles, disable and enable,
    promote and demote, incidents, the turn-timing report.
14. Be exempt from the 3-match cap. This is existing behaviour and it stays.

### Authentication is unchanged and comes first

Every route named in this spec keeps `require_user` semantics. An anonymous
caller gets 401 `NOT_SIGNED_IN`. A disabled account gets the existing
`/disabled` treatment. Both happen **before** any ownership or role test.

This matters because `get_current_user` does **not** check `disabled_at`
(`app/deps.py:32-34` → `app/auth/session.py:13-18`). Swapping
`Depends(require_game_admin)` for `Depends(get_current_user)` plus an inline
ownership test would silently readmit disabled accounts. Do not do that.

### Deleted entirely

- The game-admin role as a concept.
- `require_game_admin` in `app/deps.py`.
- Every `GAME_ADMIN_EMAILS__*` environment variable, and the config code that
  reads them.

---

## 3. Decisions

### Made with the repo owner before the build

**Decision A — the per-game match detail page.**
`GET /games/{game}/admin/matches/{match_id}` renders every seated player's
strategy text (`app/routes/game_admin_web.py:268`) and holds the only
"+ Add Bots" button, which players need for matches they own.

**Owner or platform admin, with strategies redacted for non-admins.**

- The match owner and any platform admin may open the page.
- A non-admin sees their **own** seat's strategy text and blanks for every other
  human/agent seat.
- **Bot seats are not private.** A bot's preset name and strategy stay visible to
  every viewer. Bots have no `AgentVersion`; they carry `Agent.bot_strategy`, and
  their seats belong to a shared bots account
  (`app/engine/bots/seating.py:113-122`). Redaction applies **only** to
  `AgentVersion.strategy_text`. Without this carve-out an owner could not see
  what the bots they just seated are set to — while the adjacent "Type" column
  already prints the same information.
- A platform admin sees every seat's strategy text.

**Decision B — the per-game dashboard.**
`GET /games/{game}/admin/` lists every match in one game. **Platform admin
only.** It is an organizer's view; `/admin/matches` already covers all games for
admins and `/me/matches` covers a player's own.

### Made during spec review round 1

Full reasoning and the finding that forced each one is in
`spec-review-round-1.md`.

| ID | Decision |
|----|----------|
| **D1** | **ONE create route.** `POST /games/{game}/matches/new` gains all seven fields plus per-game config. The game-admin HTML create form and route (`GET`/`POST /games/{game}/admin/matches/new`) are **deleted**; links repoint |
| **D2** | `POST /api/game-admin/{game}/matches` and `POST /api/game-admin/{game}/matches/{id}/cancel` are **deleted** — exact duplicates of `/api/admin/*`, reachable only by tests, and keeping them player-reachable would bypass the 3-match cap |
| **D3** | The admin start route stays **platform admin**. `start_game` has no seat check, no player floor and no bot fill (`app/engine/scheduler.py`), unlike the player path's `viewer_start_eligibility`. Opening it to owners would let a creator who holds no seat force-start a table of other people's agents — a new power, not an extension |
| **D4** | Cancel stays **platform admin** on both surfaces |
| **D5** | A non-admin's export contains **resolved turns only**. Admins keep the in-flight view |
| **D6** | A match with `created_by_user_id` NULL has **no owner**: platform admin only |
| **D7** | A seat is "yours" when `Player.user_id == user.id` (non-nullable, set at every seating site) |
| **D8** | Bot seats are not private — see Decision A |
| **D9** | Converge on `create_match_with_state`, with `state_config` built per game |
| **D10** | `CreateGameRequest.mutual_help_mode` gets enum validation |
| **D11** | `per_turn_deadline_seconds` bounded 5–600 on the HTML form, matching the schema |
| **D12** | `require_can_view_game` runs on every game-scoped route, before any other check |
| **D13** | `require_user` semantics preserved everywhere (see section 2) |
| **D14** | Rename the four `game_admin_*` modules and the `game_admin/` template dir |
| **D15** | `docs/platform/` is not touched — scope fence. Flagged as a follow-up |
| **D16** | The legacy `ADMIN_EMAILS` fallback stays — see section 9 |

---

## 4. Four known traps

### Trap 1 — platform admin does not currently imply game admin

`require_game_admin` checks only the environment list; it never reads
`user.role` (`app/deps.py:84-97`). So today a platform admin who is not in
`GAME_ADMIN_EMAILS__HOARD_HURT_HELP` gets a 403 creating a match.

**Remove the trap.** After this change a platform admin reaches everything.

**Four test files pin the old three-role behaviour. All four get rewritten to the
new model — none is deleted.** Deleting them would quietly remove the only
pinned guard behind contract item 13.

| File | What it pins today |
|------|--------------------|
| `tests/test_admin.py:760` | A game-admin-only user cannot reach `/admin/*` — the **only** negative pinning contract item 13 |
| `tests/test_admin.py:770` | A platform admin gets 403 on the game-admin dashboard (the trap itself) |
| `tests/test_admin.py:782` | A game admin for game X cannot reach game Y |
| `tests/test_config_admin.py:44-90` | Two whole classes testing `game_admin_emails_for` and `all_game_admin_emails_set` |
| `tests/test_game_scoped_match_loader.py:195-220` | Its docstring's premise ("a non-admin would 403 before the slug check") inverts after this change |

`_game_admin_emails_raw` is a `PrivateAttr`, so `monkeypatch.setattr` on it fails
loudly once deleted. These are hard failures, not silent skips.

### Trap 2 — the JSON export leaks every opponent's strategy prompt, and the in-flight turn

Two separate leaks in one file.

1. `app/read_models/match_export.py:104` writes
   `"strategy_prompt": version.strategy_text` for every player. Strategy text is
   private everywhere else in the app.
2. `app/read_models/match_export.py:44` calls
   `load_match_timeline(..., resolved_only=False)`, which drops the
   `Turn.resolved_at IS NOT NULL` filter. The export therefore includes the turn
   still in flight. The public spectator API deliberately uses `resolved_only=True`
   (`app/routes/spectator_api.py:75`). **Opening this export to any signed-in
   user without that filter would let an opponent in a live match read every
   rival's chosen action, target and message before the turn resolves.**

`build_json_export` has **two** callers with different auth —
`game_admin_api.py:76` and the platform-admin `admin_api.py:71`, both via
`export_match_json`. A viewer argument must reach both, and the platform-admin
route must stay unredacted. A `redact=True` default would silently strip an
admin's export with no test noticing.

That module's docstring promises the two routes' output stays byte-identical.
Once the payload depends on who is asking, that promise is false. **The docstring
must be updated.** Never leave a comment asserting a rule the code no longer
keeps.

### Trap 3 — two match-creation paths, and a silent config loss

Players go through `create_match(...)` with `_CREATE_DEFAULTS`; admins go through
`create_match_with_state(...)`. Merging them is the point.

`create_match_with_state` also seeds the module-owned `MatchState` row and is the
only carrier of `state_config` (`wild_ones`, `dice_per_player`).
`create_match` writes no such row.

**The silent-failure trap:** only Liar's Dice reads `MatchState`, and
`app/games/liars_dice/state.py:100-104` **lazily fabricates a row from defaults**
when one is missing. So converging on `create_match` would replace an admin's
chosen Liar's Dice config with defaults, and a test asserting "the row exists"
would still pass. The acceptance criterion therefore pins the **config values
round-tripping**, not the row's existence.

### Trap 4 — narrowing `_is_any_admin` changes real gates, not just a nav flag

`_is_any_admin` (`app/routes/web_support.py:173-179`) returns true for game
admins today and is read in 22 places. Nineteen are the `is_admin` template flag,
which drives the platform-admin submenu in `app/templates/base.html:91`. Those
links 403 for a game admin today, so hiding them is harmless.

**Three are real gates**, and narrowing `_is_any_admin` narrows all of them:

| Site | What it gates |
|------|---------------|
| `web_support.py:186-191` `_can_view_game` → `require_can_view_game` | Access to admin-only games (Liar's Dice), used by `web_play.py:340`, `web_join.py:226` and `:488`, `web_lobby.py:102` and `:252`, `matches_user.py:58` |
| `web_games_catalog.py:50` | Whether Liar's Dice appears in `/games` at all |
| `web_leaderboard.py:60`, `web_front_page.py:36` | Whether admin-only game sections show on the leaderboard and home page |

This narrowing is the **correct** outcome — the game-admin role is gone, so only
platform admins should see an under-construction game. But it must be pinned by a
test, because someone unblocking a broken test by widening `_can_view_game` back
out would leak the hidden game to everyone.

---

## 5. Every route `require_game_admin` guards today

Verified: exactly 13 `Depends(require_game_admin)` sites — 7 in
`game_admin_web.py` (52, 95, 119, 209, 294, 312, 330), 2 in
`game_admin_bots_web.py` (92, 104), 4 in `game_admin_api.py` (30, 50, 62, 73).
No router-level dependency adds a hidden one (`app/main.py:283-286` adds only
`populate_nav_cta`).

| # | Route | Today | After |
|---|-------|-------|-------|
| 1 | `GET /games/{game}/admin/` | game admin | **platform admin** |
| 2 | `GET /games/{game}/admin/matches/new` | game admin | **deleted** (D1) |
| 3 | `POST /games/{game}/admin/matches/new` | game admin | **deleted** (D1) |
| 4 | `GET /games/{game}/admin/matches/{id}` | game admin | **owner or platform admin**; strategies redacted for non-admins |
| 5 | `POST /games/{game}/admin/matches/{id}/start` | game admin | **platform admin** (D3) |
| 6 | `POST /games/{game}/admin/matches/{id}/cancel` | game admin | **platform admin** (D4) |
| 7 | `GET /games/{game}/admin/prompts` | game admin | **platform admin** |
| 8 | `GET /games/{game}/admin/matches/{id}/bots` | game admin | **owner or platform admin** |
| 9 | `POST /games/{game}/admin/matches/{id}/bots` | game admin | **owner or platform admin** |
| 10 | `POST /api/game-admin/{game}/matches` | game admin | **deleted** (D2) |
| 11 | `POST /api/game-admin/{game}/matches/{id}/cancel` | game admin | **deleted** (D2) |
| 12 | `GET /api/game-admin/{game}/matches/{id}/export.csv` | game admin | **any signed-in user**; resolved turns only unless admin |
| 13 | `GET /api/game-admin/{game}/matches/{id}/export.json` | game admin | **any signed-in user**; redacted + resolved-only unless admin |

Plus the surviving player create route, which absorbs rows 2 and 3:

| Route | Today | After |
|-------|-------|-------|
| `GET`/`POST /games/{game}/matches/new` | any signed-in user, fixed settings | any signed-in user, **all seven fields**; mode field admin-gated |

---

## 6. User stories and acceptance criteria

### P1 — A player creates a match with the settings they want

- **AC1.1** A signed-in non-admin can `POST /games/hoard-hurt-help/matches/new`
  with `name`, `scheduled_start`, `min_players`, `max_players`,
  `per_turn_deadline_seconds`, `total_rounds` and `turns_per_round`, and the
  created match stores every one of those values.
- **AC1.2** The allowed band **does not change**. A value is rejected with a form
  error, creating no match row, when it falls outside: the game module's
  `config_defaults()` player range (via the existing `player_count_error` — 6 to
  10 for Hoard Hurt Help), 3 to 20 total rounds, or 3 to 20 turns per round.
  `player_count_error` stays in use; it is not folded away as duplicated logic.
- **AC1.3** `per_turn_deadline_seconds` outside 5 to 600 is rejected with a form
  error, matching `CreateGameRequest`'s existing bound. (Today the HTML form
  bounds it nowhere.)
- **AC1.4** A non-admin who submits `mutual_help_mode` gets a match with
  `mutual_help_mode == "decay"`. The submitted value is ignored, not stored.
- **AC1.5** A platform admin who submits `mutual_help_mode=flat_8` gets a match
  with `mutual_help_mode == "flat_8"`.
- **AC1.6** An unknown `mutual_help_mode` from a platform admin returns a 400
  form error naming the bad value, and creates no match row.
- **AC1.7** `CreateGameRequest` rejects an unknown `mutual_help_mode` at the
  schema level, so `POST /api/admin/matches` with `{"mutual_help_mode":
  "garbage"}` returns 422 and creates no match. (Today the field is a bare `str`
  and garbage reaches the column.)
- **AC1.8** The create form shows the mutual-help control only to a platform
  admin, and only for `hoard-hurt-help`.
- **AC1.9** `GET`/`POST /games/{game}/admin/matches/new` no longer exist (404),
  and no template links to them.

### P1 — Strategy text stays private

- **AC2.1** A non-admin's JSON export contains `"strategy_prompt": null` for
  every human/agent seat that is not their own.
- **AC2.2** That same export contains the real strategy text for the requesting
  user's own seat — the seat where `Player.user_id == user.id` — when they have
  one in the match.
- **AC2.3** A platform admin's JSON export contains real strategy text for every
  seat that has one, on **both**
  `GET /api/game-admin/{game}/matches/{id}/export.json` **and**
  `GET /api/admin/matches/{id}/export.json`.
- **AC2.4** The CSV export is unchanged from today for a platform admin, and
  never contains strategy text for anyone. (Verified: `EXPORT_COLUMNS` has no
  strategy field.)
- **AC2.5** For a **non-admin** caller, both exports contain only resolved turns.
  Given a live match with an unresolved in-flight turn, a non-admin's CSV and
  JSON omit that turn's submissions entirely.
- **AC2.6** For a **platform admin**, both exports still include the unresolved
  in-flight turn — today's behaviour.
- **AC2.7** A non-admin opening the match detail page for a match they own sees
  blank strategy text for every human/agent seat except their own.
- **AC2.8** Bot seats on that page still show their strategy and preset name to
  every viewer, admin or not.
- **AC2.9** A platform admin opening any match detail page sees strategy text for
  every seat.
- **AC2.10** `app/read_models/match_export.py`'s docstring no longer claims the
  two routes' output is byte-identical.

### P1 — The game-admin role is gone

- **AC3.1** `grep -rn "game_admin\|GAME_ADMIN" app/ tests/` returns nothing.
  (The `/api/game-admin/{game}` URL prefix uses a **hyphen**, so it never matched
  this pattern; the module and template-directory names, which use underscores,
  are renamed under D14.)
- **AC3.2** `require_game_admin` no longer exists in `app/deps.py`.
- **AC3.3** `settings.game_admin_emails_for`, `settings.all_game_admin_emails_set`,
  `settings._game_admin_emails_raw` and the `_collect_game_admin_emails`
  validator no longer exist in `app/config.py`.
- **AC3.4** Setting `GAME_ADMIN_EMAILS__HOARD_HURT_HELP` grants that address
  nothing. The app starts and behaves as if the variable were unset.
- **AC3.5** `_is_any_admin(user)` returns true only when
  `user.role == UserRole.ADMIN`.
- **AC3.6** `_is_game_admin` and the `is_game_admin` viewer-context key no longer
  exist. (Verified: no template or JS reads that key today.)
- **AC3.7** A user listed only in a `GAME_ADMIN_EMAILS__*` variable, with
  `user.role != ADMIN`, sees Liar's Dice nowhere: 404 on its lobby, join, play
  and create routes; absent from `/games`, the leaderboard and the home-page
  band; and the platform-admin nav items hidden.
- **AC3.8** A platform admin can reach every route in section 5 marked platform
  admin, without appearing in any environment list.

### P1 — Ownership decides who may act on a match

- **AC4.1** A non-admin who does not own a match gets 403 on that match's detail
  and bots routes (rows 4, 8, 9).
- **AC4.2** A non-admin who owns a match can open its detail page, open its bot
  form and seat bots.
- **AC4.3** A platform admin can do all of that on a match they do not own.
- **AC4.4** A non-admin gets 403 on `GET /games/{game}/admin/prompts`.
- **AC4.5** A non-admin gets 403 on `GET /games/{game}/admin/`.
- **AC4.6** A non-admin gets 403 on the admin start route (row 5) and the admin
  cancel route (row 6) — **even for a match they own**.
- **AC4.7** A match whose `created_by_user_id` is NULL has no owner: a non-admin
  gets 403 on its detail and bots routes. A platform admin does not.
- **AC4.8** An anonymous caller gets 401 on every route in section 5. A signed-in
  but disabled account gets the existing `/disabled` treatment, not a 403 and not
  a 200.
- **AC4.9** A non-admin who already holds 3 active matches gets 409 on
  `POST /games/{game}/matches/new` — the only surviving player-reachable create
  route.
- **AC4.10** A platform admin holding 3 or more active matches can still create
  another.
- **AC4.11** Deleting someone else's match still returns 403 for a non-admin and
  succeeds for a platform admin. (Existing behaviour, pinned so the refactor
  cannot quietly change it.)
- **AC4.12** The player start route `POST /games/{game}/matches/{id}/start` still
  obeys `viewer_start_eligibility` exactly as today: an owner who holds no
  confirmed seat, or whose match has another user's seat, still gets 409.

### P1 — One match-creation path

- **AC5.1** Both the surviving HTML create route and the platform-admin JSON
  create API reach the database through the same creation function.
- **AC5.2** A Liar's Dice match created with `wild_ones=false` and
  `dice_per_player=3` **reads back that exact config**. (Pinning the config, not
  the row: `_load_state(create=True)` fabricates a default row, so a
  row-existence assertion would pass while the config was silently lost.)
- **AC5.3** **Every** route in section 5 returns 404 — not 403 — to a non-admin
  when `{game}` is admin-only, before any other check. A non-admin
  `GET /api/game-admin/liars-dice/matches/{id}/export.csv` must not reveal that
  the game exists.
- **AC5.4** A platform admin can still create a Liar's Dice match through the
  merged create route, with its `wild_ones` and `dice_per_player` fields.

### P2 — No accidental privilege through page chrome

- **AC6.1** Every reopened template renders `is_admin` from
  `user.role == UserRole.ADMIN`, never a hardcoded `True`. A non-admin owner
  opening their match detail page sees no platform-admin nav items.
  (Today six render sites pass the literal `True`:
  `game_admin_web.py:82,106,137,279,356` and `game_admin_bots_web.py:72`.)

---

## 7. Out of scope — do not build

- The funnel or marketing dashboard. A separate planned change.
- Any alpha gate or invite system. Sign-up stays open on purpose.
- Any change to game rules, payoffs, or the `MutualHelpMode` values. This change
  only decides **who may choose** the mode.
- `/guide/*` rendering. (`docs/platform/` was originally out of scope too; Chris lifted that mid-run — see section 8.)
- Removing the 3-active-match cap.

---

## 8. Known follow-up, deliberately not done here

`docs/platform/AGENT_LUDUM_ARCHITECTURE.md` lines 108, 114, 115, 117, 399 and
643, and `docs/platform/AGENT_LUDUM_DESIGN.md:336`, describe
`require_game_admin`, `_is_game_admin` and `GAME_ADMIN_EMAILS__*` as live. After
this change those lines are false, and `CLAUDE.md` tells every agent to read that
architecture doc first.

**Fixed after all.** The scope fence originally excluded `docs/`, so this was
written up as a follow-up. Chris lifted the fence during the run ("update docs
as well"), so `docs/platform/` was brought in line in the same PR. The historical
run folders under `docs/workflow/feature-runs/` are deliberately left alone —
they record what was true when each feature shipped, and rewriting them would
falsify that history.

## 9. The legacy `ADMIN_EMAILS` fallback — kept

`app/config.py:111` carries `admin_emails`, marked "Remove this field once all
prod env vars are updated." Retiring the game-admin role was the natural moment
to finish it.

**Decision: keep it.** Two reasons, both checked:

1. Seven test files depend on it (`test_admin.py`, `test_handle_safety.py`,
   `test_admin_add_bots.py`, `test_request_logging.py`,
   `test_bot_form_validation.py`, `test_migrations.py`, `test_config_admin.py`).
2. It is the fallback that feeds `platform_admin_emails_set`, which is what grants
   `UserRole.ADMIN` at sign-in (`app/routes/auth.py:34,78`). If `ADMIN_EMAILS` is
   still the live production variable, retiring it locks the owner out of their
   own platform.

The spec's own guidance was "optional, only if it stays clean." It is not clean.
Reported in the PR.

---

## 10. Risks

| Risk | Why it matters | How this spec handles it |
|------|----------------|--------------------------|
| A route left ungated | An auth change can pass every test and still leave a door open in production | Section 5 lists all 13 routes with a verified count; the plan enumerates every consumer; the `silent-failure` review lens hunts for exactly this |
| A gate that checks the wrong role | Looks correct, behaves wrong | AC4.1–AC4.12 pin both the allow and the deny for every role on every route |
| Strategy text leaking through an unlisted path | It already leaks in one place | The plan enumerates every consumer of `strategy_text`, including templates, the spectator API and `mcp_server/` |
| In-flight turn data leaking to an opponent | Reading a rival's action before resolve breaks the game itself | AC2.5 and AC2.6 pin resolved-only for non-admins |
| Liar's Dice config lost silently | The lazy default-row path makes a row-existence test pass while the config is gone | AC5.2 pins the config values round-tripping |
| Losing admin access in production | Retiring `ADMIN_EMAILS` could lock the owner out | Section 9: kept, with the reason checked and recorded |
| A broken test "fixed" by widening a gate | Would leak the under-construction game to everyone | AC3.7 pins the hidden game's invisibility |
