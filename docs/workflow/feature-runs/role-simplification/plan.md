# Plan — Role Simplification

**Version:** 2 (after plan review round 1 — see `plan-review-round-1.md`)

Implements `spec.md` v2. Read that first; this file says **how**, not **what**.

---

## 1. Consumer enumeration — the mandatory artifact

Every value this change touches, and **every** place that reads it. Verified by
grep against the worktree, then independently re-grepped by the
`implementation-adversarial` review, which confirmed the list complete and added
three rows (§1.5, §1.7).

### 1.1 `require_game_admin` — DELETED (13 consumers)

| File:line | Route | Becomes |
|-----------|-------|---------|
| `game_admin_web.py:52` | `GET /games/{game}/admin/` | `PlatformAdminForGame` |
| `game_admin_web.py:95` | `GET .../matches/new` | route deleted |
| `game_admin_web.py:119` | `POST .../matches/new` | route deleted |
| `game_admin_web.py:209` | `GET .../matches/{id}` | `OwnedOrAdminMatch` |
| `game_admin_web.py:294` | `POST .../matches/{id}/start` | `AdminMatch` |
| `game_admin_web.py:312` | `POST .../matches/{id}/cancel` | `AdminMatch` |
| `game_admin_web.py:330` | `GET .../prompts` | `PlatformAdminForGame` |
| `game_admin_bots_web.py:92` | `GET .../matches/{id}/bots` | `OwnedOrAdminMatch` |
| `game_admin_bots_web.py:104` | `POST .../matches/{id}/bots` | `OwnedOrAdminMatch` |
| `game_admin_api.py:30` | `POST /api/game-admin/{game}/matches` | route deleted |
| `game_admin_api.py:50` | `POST .../{id}/cancel` | route deleted |
| `game_admin_api.py:62` | `GET .../{id}/export.csv` | `ExportableMatch` |
| `game_admin_api.py:73` | `GET .../{id}/export.json` | `ExportableMatch` |
| `app/deps.py:84-97` | the definition | deleted |

### 1.2 Config symbols — DELETED

| Symbol | Defined | Read by |
|--------|---------|---------|
| `game_admin_emails_for()` | `config.py:159` | `deps.py:91`, `web_support.py:183` |
| `all_game_admin_emails_set` | `config.py:179` | `web_support.py:178` |
| `_game_admin_emails_raw` | `config.py:114` | `config.py:166,182`; `tests/test_admin.py:764,774,785`; `tests/test_game_scoped_match_loader.py:210` |
| `_collect_game_admin_emails` | `config.py:117` | pydantic model validator only |
| the `GAME_ADMIN_EMAILS__*` comment block | `config.py:104-105` | docs only |

Deleting these orphans `import os` (`config.py:8`) and `PrivateAttr`,
`model_validator` (`config.py:11`). Ruff F401 catches them.

**Kept:** `platform_admin_emails`, `platform_admin_emails_set`, `admin_emails`.
`platform_admin_emails_set` grants `UserRole.ADMIN` at sign-in
(`auth.py:34,78`) and marks a floor admin (`admin_web.py:277`,
`services/admin_user_actions.py:27`). Untouched.

### 1.3 `_is_game_admin` — DELETED (1 consumer)

`web_support.py:182` → read only at `web_viewer_context.py:304`, which sets the
`is_game_admin` template key. **No template and no JS reads that key.** Both go.

### 1.4 `_is_any_admin` — NARROWED to `user.role == UserRole.ADMIN` (22 consumers)

The body loses its `or email in settings.all_game_admin_emails_set` clause.
**No call site changes**, so every consumer inherits the narrowing — which is
exactly why it must be tested rather than read.

**Real gates — behaviour changes; AC3.7 pins them:**

| File:line | What it gates |
|-----------|---------------|
| `web_support.py:191` (`_can_view_game` → `require_can_view_game`) | Access to admin-only games. Called from `web_play.py:340`, `web_join.py:226`, `web_join.py:488`, `web_lobby.py:102`, `web_lobby.py:252`, `matches_user.py:58` |
| `web_games_catalog.py:50` | Whether Liar's Dice appears in `/games` |
| `web_leaderboard.py:60` | Whether admin-only sections show on the leaderboard |
| `web_front_page.py:36` | **Real, not cosmetic** — lines 45-46 filter the home page's leaderboard band by it |

**Cosmetic — the `is_admin` template flag only** (drives the platform-admin
submenu and the Users link at `base.html:91-99`, which already 403 for a game
admin): `web_seat_connect.py:93`, `web_join.py:253`, `web_analysis.py:56`,
`web_analysis.py:96`, `handle_web.py:67`, `web_guide.py:36`,
`web_my_matches.py:58`, `web_my_matches.py:131`, `web_my_matches.py:178`,
`web_lobby.py:203`, `web_lobby.py:267`, `web_leaderboard.py:67`,
`web_games_catalog.py:114`, `matches_user.py:76`, `matches_user.py:98`,
`web_contact.py:37`, `web_viewer_context.py:303`.

### 1.5 `strategy_text` — the private value

| Site | Scope today | Action |
|------|-------------|--------|
| `match_export.py:104` | **every player** — LEAK | redact for non-admins |
| `game_admin_web.py:268` (match detail) | **every player** — LEAK once opened | redact for non-admins |
| `game_admin_web.py:349` (prompts page) | every player | safe — page becomes platform-admin only |
| `web_viewer_context.py:202`, `web_viewer.py:100` | the viewer's own coach prompt | unchanged |
| `web_my_matches.py:185` | the user's own agents | unchanged |
| `agents_create.py:90,130,146,167,196` | the user's own agent authoring | unchanged |
| `agents_lifecycle.py:40,54,68,80,85,179,186,255,259,266,268` | the user's own agent versions | unchanged |
| `engine/agent_play_next_turn.py:446,570,588` | fed to the agent playing its **own** seat | unchanged |
| `engine/human_player.py:66` | writes `""` | unchanged |
| `mcp_server/mcp_tools.py:133,153,277,283,312` | the calling agent's own strategy via `agent_identity_for` | unchanged — verified not a leak |
| `templates/connection.html:26` | the user's own connection page | unchanged |
| `templates/fragments/coach_panel.html:30` | owner-scoped `viewer_prompt_text` | unchanged |
| `templates/agents/_versions.html:25`, `agents/detail.html:56,61`, `agents/new.html:59` | `/me/agents/*`, owner-scoped | unchanged |
| `templates/game_admin/match_detail.html:35` | renders `p.strategy` | inherits the redaction |

**Two leak sites, both closed.** Everything else is already owner-scoped.

### 1.6 `resolved_only` — the in-flight-turn value (4 `load_match_timeline` consumers)

| Site | Today | After |
|------|-------|-------|
| `match_export.py:44` | `False` — includes the in-flight turn | `False` for platform admin, `True` for everyone else |
| `spectator_api.py:73` | default `True` | unchanged |
| `web_viewer_context.py:261` | default `True` | unchanged |
| `read_models/matches.py:469` | default `True` | unchanged |

### 1.7 Export builders — signature change, two layers

| Layer | Function | Called from |
|-------|----------|-------------|
| read model | `gather_export_rows` | `match_export.py:69,107` (internal) |
| read model | `build_csv_export` | `game_admin_actions.py:128` |
| read model | `build_json_export` | `game_admin_actions.py:133` |
| wrapper | `export_match_csv` | `game_admin_api.py:65` **and** `admin_api.py:60` |
| wrapper | `export_match_json` | `game_admin_api.py:76` **and** `admin_api.py:71` |

All five gain a required keyword-only `viewer`. **No default value** — a
`redact=True` default would silently strip a platform admin's export. All four
outer call sites are in `app/`, which mypy does check, and no test calls the
builders directly (verified), so mypy is a real gate here.

### 1.8 Hardcoded `"is_admin": True` — 6 render sites

`game_admin_web.py:82,106,137,279,356` and `game_admin_bots_web.py:72`. Lines 106
and 137 belong to the deleted create form; the other four switch to the real
value. **`game_admin_bots_web.py:72` matters most** — that page becomes
owner-reachable, so a non-admin owner would otherwise see the admin submenu.

### 1.9 Templates linking to the deleted create route

`admin/dashboard.html:9`, `admin/dashboard.html:15`, `game_admin/dashboard.html:6`,
`game_admin/dashboard.html:56` — all repoint to `/games/{game}/matches/new`.

Also `game_admin/dashboard.html:19` uses `{{ game.game }}` while the context only
supplies `game_slug`, rendering `/games//matches/{id}`. A pre-existing one-word
bug in a template this change already touches; fixed while here.

### 1.10 Module renames (D14) — every importer

| Old | New | Importers |
|-----|-----|-----------|
| `routes/game_admin_web.py` | `routes/match_manage_web.py` | `main.py:41-43,283`, `game_admin_bots_web.py:18`, `tests/test_admin.py:16` |
| `routes/game_admin_bots_web.py` | `routes/match_bots_web.py` | `main.py:42,284` |
| `routes/game_admin_api.py` | `routes/match_export_api.py` | `main.py:41,286` |
| `routes/game_admin_actions.py` | `routes/admin_match_actions.py` | `admin_api.py:12`, `game_admin_api.py:13` |
| `templates/game_admin/` | `templates/match_manage/` | 6 `TemplateResponse` calls; `create_match.html` deleted, not moved |

Verified: `app/routes/__init__.py` is empty, all five templates extend only
`base.html` (no cross-template path includes), and no dynamic or string-based
import references these modules.

`game_admin_bots_web.py:18` imports the `_load_game_match_or_404` alias **from**
`game_admin_web`. That import is **deleted outright**, not repointed — the new
dependency supplies the match.

The `/api/game-admin/{game}` **URL** keeps its hyphen and never matched the
`game_admin\|GAME_ADMIN` grep, so no URL changes and no bookmarks break.

### 1.11 Tests that must be rewritten (not deleted)

| File:line | Pins today | Rewritten to |
|-----------|------------|--------------|
| `test_admin.py:760` | game-admin-only user 403s on `/admin/*` | plain user 403s on `/admin/*` — the only guard behind contract item 13 |
| `test_admin.py:770` | platform admin 403s on the game dashboard | platform admin **succeeds** (the trap, removed) |
| `test_admin.py:782` | game admin for X 403s on Y | plain user 403s on any game's dashboard |
| `test_admin.py:337,916` | `/api/game-admin/.../matches` create | moved to `/api/admin/matches` |
| `test_admin.py:383` | create validation | moved to `/api/admin/matches` |
| `test_admin.py:403` | unknown game type | moved; **assertion changes** from `"Unknown game type"` to `"No game module registered"` — the `known_types()` guard being deleted is what produced the old wording |
| `test_admin.py:581` | `/api/game-admin/.../cancel` | moved to `/api/admin/matches/{id}/cancel` |
| `test_admin.py:538` | admin web form creates a Liar's Dice match | moved to the merged create route |
| `test_admin.py:793` | dashboard survives a null start time | **needs `role=UserRole.ADMIN` added to its `SimpleNamespace`** (`test_admin.py:842-847`), because `is_admin` starts reading `user.role` |
| `test_config_admin.py:44-90` | `game_admin_emails_for`, `all_game_admin_emails_set` | both classes deleted; replaced by a symbol-absence test (§4, row 19) |
| `test_game_scoped_match_loader.py:195-220` | grants game-admin to reach the loader | uses a platform admin instead; its docstring premise inverts |

Factories also change: `make_match` gains `created_by_user_id`, and `seat_player`
gains `strategy_text`. Without those, matrix rows 25 and 13 are vacuous (§4).

---

## 2. Design

### 2.1 New module: `app/routes/match_authz.py`

Holds the match-authorization dependencies. A new module rather than an addition
to `web_match_loaders.py` because of an import cycle: the dependency needs
`require_can_view_game` from `web_support`, and `web_support:49` already imports
from `web_match_loaders`.

Import direction verified acyclic:
`match_authz` → `web_support` → `web_match_loaders` → `deps`. `app/routes/__init__.py`
is empty, so there is no cycle via the package either.

#### The check order IS the security contract

Every dependency runs these in this order. Getting the order wrong leaks the
existence of a hidden game.

1. `require_user` — 401 anonymous, disabled account bounced (D13).
2. `require_can_view_game(user, game, detail=None)` — **404** on an admin-only
   game (D12, AC5.3). Runs before everything else so the game's existence is
   never revealed. `detail=None` gives a bare 404, matching the export routes'
   existing 404 family (pinned at `test_game_scoped_match_loader.py:219`).
3. `load_game_match_or_404(db, game, match_id)` — 404 on missing or wrong slug.
4. The role or ownership test.

**Critical FastAPI mechanic:** `require_platform_admin` must **never** appear as a
signature sub-dependency of these, because FastAPI resolves signature
sub-dependencies *before* the function body — so its 403 would fire ahead of step
2's 404 and leak the hidden game. The role check is written inline in the body,
after step 2. Only `Depends(require_user)` appears in the signature.

| Dependency | Alias | Steps | Used by |
|------------|-------|-------|---------|
| `load_owned_or_admin_match` | `OwnedOrAdminMatch` | 1–3, then: admin → allow; `created_by_user_id is None` → 403 `NOT_MATCH_OWNER` (D6); `!= user.id` → 403 `NOT_MATCH_OWNER` | rows 4, 8, 9 |
| `load_exportable_match` | `ExportableMatch` | 1–3 only | rows 12, 13 |
| `load_admin_match` | `AdminMatch` | 1–3, then `role != ADMIN` → 403 `NOT_PLATFORM_ADMIN` **in the body** | rows 5, 6 |
| `require_platform_admin_for_game` | `PlatformAdminForGame` | 1, 2, then role check — no match load (these routes have no `{match_id}`) | rows 1, 7 |

`require_platform_admin` (`deps.py:72`) stays unchanged and keeps its existing
users outside this router.

### 2.2 Export viewer scope

`app/read_models/match_export.py` gains a frozen value object rather than two
loose booleans, so a call site cannot swap them by accident:

```python
@dataclass(frozen=True)
class ExportViewer:
    """Who is asking, and therefore how much of the export they may see."""
    user_id: int | None
    is_platform_admin: bool
```

- `strategy_prompt` is real when `viewer.is_platform_admin or player.user_id ==
  viewer.user_id`, else `None` (D7, AC2.1, AC2.2).
- `resolved_only = not viewer.is_platform_admin` (D5, AC2.5, AC2.6).
- `viewer` is a **required keyword-only** argument on all five functions in §1.7.
- The docstring's byte-identical claim is replaced with the real rule (AC2.10).

Bot seats need no special case here: bots carry no `agent_version_id`
(`engine/bots/seating.py:151-160`), so `version` is already `None` and
`strategy_prompt` is already `null`.

`admin_api.py:57,68` currently name the auth dependency `_`. Both are renamed to
`user` so an admin `ExportViewer(user_id=user.id, is_platform_admin=True)` can be
built — not `user_id=None`, which would quietly lose AC2.2's own-seat case if the
admin flag were ever reconsidered.

### 2.3 The merged create route

`POST /games/{game}/matches/new` in `matches_user.py` absorbs the deleted admin
form.

**Form defaults come from the game module, not `_CREATE_DEFAULTS`.** A new helper
is needed because Liar's Dice's own defaults are partly out of the route's band:

| Game | `config_defaults()` | Route band |
|------|---------------------|------------|
| hoard-hurt-help | 6–10 players, 5 rounds, 7 turns | fits |
| liars-dice | 3–6 players, **64 rounds, 256 turns** | rounds/turns out of the 3–20 band |

```python
def _form_defaults(module) -> dict[str, int]:
    """Prefill values for the create form: the game's own player range, with
    rounds and turns clamped into the band this route accepts (3-20)."""
```

Player counts come straight from `config_defaults()`; rounds and turns are
clamped to 3–20. Without this, an admin opening the Liar's Dice form gets 6/10
prefilled and the default submission 400s — which is also a pre-existing bug in
today's admin form (`game_admin_web.py:108`), fixed in passing.

Order of operations:

1. `_load_visible_game_module_or_404(game, user)` — unchanged; 404s a non-admin
   on an admin-only game.
2. **Delete** the blanket `is_admin_only(game)` 404 at `matches_user.py:121-125`.
   Its comment says the generic flow "has no way to capture or persist that
   config" — no longer true once this route carries `state_config`. Step 1 still
   hides the game from non-admins, so only an admin gets through (AC5.4).
3. Parse and validate the start time — unchanged.
4. `player_count_error(...)` against `module.config_defaults()` (AC1.2).
5. `3 <= total_rounds <= 20`, `3 <= turns_per_round <= 20` (AC1.2).
6. `5 <= per_turn_deadline_seconds <= 600` (AC1.3 — new).
7. `mutual_help_mode`: ignored entirely unless `user.role == UserRole.ADMIN`
   (AC1.4); a non-empty admin value must parse as `MutualHelpMode` or 400 (AC1.6).
8. The 3-match cap, unchanged, non-admins only (AC4.9, AC4.10).
9. `create_match_with_state(...)` with per-game `state_config` (D9).

`state_config` is built by a shared helper so the HTML route and the JSON API
cannot drift:

```python
def state_config_for(game: str, *, wild_ones: bool, dice_per_player: int) -> dict:
    """Per-game module-owned config. Only Liar's Dice has any today."""
```

Used by **both** `matches_user.py` and `create_game_record`
(`game_admin_actions.py:110-113`), which today stamps Liar's Dice keys onto every
game's `state_config` — the drift D9 exists to remove.

`_CREATE_DEFAULTS` is deleted; `_form_defaults` replaces it.

### 2.4 Match detail redaction and chrome

`game_admin_web.py:263-272` builds `player_views`. The strategy field becomes:

```python
show_strategy = is_platform_admin or p.user_id == user.id
strategy = (
    agent.bot_strategy if is_bot                      # bots are not private (D8)
    else version.strategy_text if (version and show_strategy)
    else ""
)
```

Bot seats keep their strategy and `personality` label for every viewer (AC2.8).

`match_detail.html:19-24` renders a "Start now" form posting to the admin start
route whenever the state is `registering`. That route becomes platform-admin only
(D3), so a non-admin owner would see a button that always 403s. **The form is
wrapped in `{% if is_admin %}`.**

### 2.5 `CreateGameRequest.mutual_help_mode` validation

`app/schemas/admin.py:24` is a bare `str`, so garbage reaches the column on
`/api/admin/matches` today. It gains a `field_validator` parsing the value
through `MutualHelpMode` (AC1.7), closing the hole on the platform-admin API too.

---

## 3. Files changed

| File | Change |
|------|--------|
| `app/deps.py` | delete `require_game_admin` |
| `app/config.py` | delete 4 game-admin symbols + comment block + orphaned imports |
| `app/routes/match_authz.py` | **new** — 4 authorization dependencies |
| `app/routes/match_manage_web.py` | renamed; delete both create routes; re-gate 5; redact strategies; real `is_admin` |
| `app/routes/match_bots_web.py` | renamed; re-gate 2; real `is_admin`; drop the cross-module alias import |
| `app/routes/match_export_api.py` | renamed; delete create + cancel; open 2 exports |
| `app/routes/admin_match_actions.py` | renamed; thread `viewer`; shared `state_config_for`; update docstring |
| `app/routes/admin_api.py` | rename `_` → `user`; pass an admin `ExportViewer` |
| `app/routes/matches_user.py` | merged create route; `_form_defaults`; drop the `is_admin_only` block |
| `app/routes/web_support.py` | narrow `_is_any_admin`; delete `_is_game_admin` |
| `app/routes/web_viewer_context.py` | drop the `is_game_admin` import and context key |
| `app/main.py` | update 3 router imports and includes |
| `app/read_models/match_export.py` | `ExportViewer`; redaction; `resolved_only`; docstring |
| `app/schemas/admin.py` | validate `mutual_help_mode` |
| `app/templates/match_manage/` | renamed; `create_match.html` deleted; links repointed; `game.game` fixed; start form gated |
| `app/templates/matches_user/create_match.html` | all seven fields + per-game config + admin-only mode select |
| `app/templates/admin/dashboard.html` | 2 create links repointed |
| `tests/factories.py` | `make_match(created_by_user_id=…)`, `seat_player(strategy_text=…)` |
| `tests/` | see §1.11 and §4 |

**Not changed:** `app/engine/match_creation.py`, `docs/platform/` (scope fence,
D15), anything under `/guide/`.

---

## 4. Test matrix

`tests/test_role_simplification.py` is the new home for the matrix; the files in
§1.11 are rewritten in place.

**Conventions.** Every `settings` write goes through `monkeypatch.setattr` —
`settings` is an `lru_cache`d process singleton shared across an xdist worker.
Admin users are seeded by setting `User.role` directly (the
`tests/test_matches_user.py:16-26` pattern), not via the `admin_emails` fixture,
which would make the user a game admin for every game under today's rules.

### Create (AC1.x)

| # | Actor | Action | Expect |
|---|-------|--------|--------|
| 1 | plain user | create with min 7, max 9, deadline 120, rounds 4, turns 6 — **every value distinct from every default** | 303; all five stored exactly |
| 2 | plain user | `min_players=2` on HHH | 400 with `"Player counts must be 6 to 10."`; no match row |
| 3 | plain user | `total_rounds=2` — inside the engine's 1–20 band, outside the route's | 400 with `"Total rounds must be 3 to 20."`; no match row |
| 4 | plain user | `turns_per_round=2` | 400 with the turns message; no match row |
| 5 | plain user | `per_turn_deadline_seconds=0` | 400; no match row |
| 6 | plain user | `per_turn_deadline_seconds=99999` | 400; no match row |
| 7 | plain user | `mutual_help_mode=flat_8` | 303; stored match is `decay` |
| 8 | platform admin | `mutual_help_mode=flat_8` | 303; stored match is `flat_8` |
| 9 | platform admin | `mutual_help_mode=nonsense` | 400; no match row |
| 10 | platform admin | `POST /api/admin/matches` with `mutual_help_mode=nonsense` | 422; no match row |
| 11 | plain user | `GET` the create form | no `mutual_help_mode` control in the HTML |
| 12 | platform admin | `GET` the create form for HHH | control present |
| 13 | platform admin | `GET` the create form for liars-dice | **no** mutual-help control (AC1.8 second half) |
| 14 | — | `/games/{game}/admin/matches/new` | **structural**: no such path in `app.routes`. Then signed-in `GET` → 404, `POST` → 405, anonymous `GET` → 401 |
| 15 | — | every template under `app/templates` | no link to `/admin/matches/new` (AC1.9 second half) |

### Export (AC2.x)

| # | Actor | Action | Expect |
|---|-------|--------|--------|
| 16 | plain user with a seat | JSON export of a match seeded with **distinct** per-seat strategy text | own seat's text real; every other human/agent seat `null` |
| 17 | platform admin | JSON export of the **same** match via `/api/game-admin/...` | every seat's text real |
| 18 | platform admin | JSON export of the same match via `/api/admin/...` | every seat's text real (AC2.9 for the export) |
| 19 | plain user | CSV export | 200; header equals `EXPORT_COLUMNS` exactly; no strategy column |
| 20 | plain user | export of a match with an unresolved in-flight turn | that turn's rows absent from **both** CSV and JSON |
| 21 | platform admin | the same match | that turn's rows present in both |
| 22 | — | `match_export.py` module docstring | does not contain "byte-identical" (AC2.10) |

### Roles and gates (AC3.x, AC4.x)

| # | Actor | Action | Expect |
|---|-------|--------|--------|
| 23 | — | `Settings` instance | the four game-admin symbols are absent (`hasattr` is False). Names built from parts so this file does not contain them literally |
| 24 | — | `_is_any_admin` unit test, no DB | True only for `role == UserRole.ADMIN`; False for a non-admin regardless of email (AC3.5) |
| 25 | plain user | `GET /games/hoard-hurt-help/admin/prompts` | 403, `code == "NOT_PLATFORM_ADMIN"` |
| 26 | plain user | `GET /games/hoard-hurt-help/admin/` | 403, `code == "NOT_PLATFORM_ADMIN"` |
| 27 | platform admin **not** in any env list | `GET /games/{game}/admin/` **and** `.../prompts` | 200 each — the trap is gone (AC3.8) |
| 28 | plain user, owns the match | `GET .../matches/{id}` | 200; own seat's strategy text present; another seat's absent from the HTML |
| 29 | plain user, owns the match, seated bots | the same page | each bot's `bot_strategy` **and** preset label present (AC2.8) |
| 30 | platform admin | any match's detail page | every seat's strategy text present (AC2.9) |
| 31 | plain user, owns the match | bots form `GET` + seating `POST` | 200 / 303 |
| 32 | plain user, **another real user owns it** (precondition asserted: `created_by_user_id is not None`) | detail, bots `GET`, bots `POST` | 403, `code == "NOT_MATCH_OWNER"` each |
| 33 | plain user | match with `created_by_user_id` NULL: detail, bots | 403, `code == "NOT_MATCH_OWNER"` |
| 34 | platform admin | the same NULL-owner match | 200 |
| 35 | plain user, owns the match | admin start, admin cancel | 403, `code == "NOT_PLATFORM_ADMIN"` each |
| 36 | plain user, owns the match | `GET .../matches/{id}` | HTML has **no** form posting to `/admin/matches/{id}/start` |
| 37 | plain user, owns the match | `GET .../matches/{id}` and `.../bots` | HTML has no platform-admin nav item (AC6.1, **both** pages) |
| 38 | platform admin | detail, bots, start, cancel on a match they do not own | allowed |
| 39 | anonymous | every route in §1.1 | 401 |
| 40 | disabled account | HTML routes with `Accept: text/html` | 303 to `/disabled` |
| 41 | disabled account | HTML routes with `HX-Request: true` | 200 with an `HX-Redirect` header |
| 42 | disabled account | export routes, default `Accept` | 403, `code == "ACCOUNT_DISABLED"` |
| 43 | plain user, 3 active matches | create a fourth | 409 |
| 44 | platform admin, 3 active matches | create a fourth | 303 |
| 45 | plain user | delete someone else's match | 403 |
| 46 | platform admin | delete someone else's match | 303 |
| 47 | owner holding no confirmed seat | player start route | 409 — `viewer_start_eligibility` unchanged |
| 48 | plain user | Liar's Dice lobby / join / play / create | 404 each |
| 49 | plain user | `/games`, the leaderboard, **and the home-page band** | no Liar's Dice section in any of the three |
| 50 | plain user | **every one of the 11 surviving §1.1 routes**, parametrized, with `{game}=liars-dice` | 404, never 403 — including rows 1 and 7, the two the plan v1 got wrong |

### Creation path (AC5.x)

| # | Actor | Action | Expect |
|---|-------|--------|--------|
| 51 | platform admin | create liars-dice **omitting** `wild_ones` (a checkbox — presence means true), `dice_per_player=3`, explicit in-band rounds/turns | `MatchState.state_json["config"] == {"wild_ones": False, "dice_per_player": 3}` |
| 52 | platform admin | `GET /games/liars-dice/matches/new` | the rendered defaults satisfy `player_count_error` and the 3–20 band |
| 53 | plain user | create an HHH match | succeeds; `state_config` is `{}`, not Liar's Dice keys |
| 54 | platform admin | `POST /api/admin/matches` for HHH | `state_config` is `{}` too — both paths agree (AC5.1) |
| 55 | — | repo scan | no `game_admin` / `GAME_ADMIN` anywhere in `app/` or `tests/` (AC3.1) |

**Row 55 is a filesystem walk, not a shell-out.** Modelled on
`tests/test_arch_doc_paths.py:18,90-99`: `rglob` over `*.py` and `*.html` under
`app/` and `tests/`, skipping `__pycache__` and `Path(__file__)`, with the needle
built from parts (`"game" + "_admin"`) so the test file never contains the string
it forbids. A shelled-out `grep -r` would match stale `__pycache__` binaries and
depend on the xdist worker's CWD.

---

## 5. Build order

One slice. The change is cross-cutting but not ordered: no migration, no data
gate, no step that must land before another compiles. Per the engine guide's
"Keep Diffs Scoped" criteria, slicing is for ordered steps, diffs clearly over
~300 changed lines, or data-critical gates. This has none — the diff is mostly
deletions and re-gating. The whole-diff review fan in stage 5 is the real defense.

Within the slice, the order that keeps `mypy` useful as a progress check:

1. `match_export.py` — `ExportViewer`, redaction, `resolved_only`, docstring.
2. `schemas/admin.py` — mode validation.
3. `match_authz.py` — the four dependencies.
4. `config.py` + `deps.py` — delete the game-admin symbols.
5. `web_support.py`, `web_viewer_context.py` — narrow and delete.
6. Route modules: rename, re-gate, redact, fix `is_admin`.
7. `matches_user.py` + templates — the merged create route.
8. `main.py` wiring.
9. Factories, then tests.

**Correction to a false claim in plan v1:** deleting the config symbols does
*not* make mypy fail at every consumer. CI runs `mypy app/ mcp_server/`
(`.github/workflows/ci.yml:36`) — **`tests/` is not type-checked**, and the
`monkeypatch.setattr(settings, "_game_admin_emails_raw", …)` call sites are
string literals no static tool can see. They fail at runtime under pytest
(`monkeypatch.setattr` raises `AttributeError` when the attribute is absent), so
they are caught — but only by a **full pytest run**, not by mypy. The step-4
checklist is therefore `mypy app/ mcp_server/` **plus** `pytest`.

---

## 6. Rollback

No migration and no data change, so rollback is a revert. The only environment
consequence: any `GAME_ADMIN_EMAILS__*` variable still set in production becomes
inert. It can be removed from Railway before or after the deploy, in either
order. Nothing reads it either way.
