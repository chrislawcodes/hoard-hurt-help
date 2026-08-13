# Plan review round 1 — verdicts

Two adversarial lenses ran on `plan.md` v1, foreground and concurrent:
`testability-adversarial` (T) and `implementation-adversarial` (I). Both read the
real code and test suite. Every finding gets a verdict; "fix now" rows are folded
into `plan.md` v2.

**The consumer enumeration (§1) was independently re-grepped and confirmed
complete** for `require_game_admin`, all four config symbols, `_is_game_admin`,
`_is_any_admin`, `load_match_timeline`, `gather_export_rows`, and the module
importers. `scripts/`, `mcp_server/`, `migrations/` and `.github/` are clean.
Nothing outside `tests/` calls the two deleted API routes. Three small omissions
are recorded below (I6, I13, I14).

## Verdicts

| # | Lens | Finding | Verdict | Reason |
|---|------|---------|---------|--------|
| T1 | testability | Row 19 (env var grants nothing) is unwritable — `_game_admin_emails_raw` is filled from `os.environ` at construction and `settings` is an `lru_cache` singleton, so `monkeypatch.setenv` changes nothing | **fix now** | Verified at `config.py:116-125,193`. Replaced with a symbol-absence assertion plus a unit test of `_is_any_admin` — the only assertions that actually fail if the deletion is skipped |
| T2 / I2 | both | Rows 1 and 7 use bare `require_platform_admin`, which never calls `require_can_view_game` → 403 reveals Liar's Dice exists, violating AC5.3 | **fix now** | **Blocker, found independently by both.** New `PlatformAdminForGame` dependency |
| I3 | implementation | `AdminMatch` with `require_platform_admin` as a *signature* dependency fires before the body's `require_can_view_game` → same leak on rows 5 and 6 | **fix now** | **Blocker.** FastAPI resolves signature sub-dependencies first. The role check moves into the body, after visibility |
| T3 / I1 | both | The grep test fails on its own source: a file containing the literal `game_admin` matches its own grep | **fix now** | **Blocker, found independently by both.** Rewritten as an `rglob` walk with the needle built from parts, skipping `__pycache__` and `__file__`, covering `*.py` **and** `*.html` |
| I4 | implementation | `_CREATE_DEFAULTS` (6/10) as the merged form's defaults breaks Liar's Dice (3–6 players), and LD's `config_defaults()` rounds/turns (64/256) are outside the route's 3–20 band | **fix now** | **Blocker.** New `_form_defaults(module)` helper: player counts from the game module, rounds/turns clamped into the route band. Also fixes a pre-existing bug in today's admin form |
| I5 / T11 | both | The match detail page's "Start now" form is unconditional, so a non-admin owner sees a button that always 403s | **fix now** | Verified at `match_detail.html:19-24`. Wrapped in `{% if is_admin %}`, with a test row |
| I12 | implementation | `create_game_record` still stamps Liar's Dice keys onto every game's `state_config`, so the two create paths diverge — the drift D9 exists to remove | **fix now** | Verified at `game_admin_actions.py:110-113`. Per-game rule extended there too |
| I6 | implementation | `web_front_page.py:36` is a **real** gate, listed as cosmetic in plan §1.4 (the spec had it right) | **fix now** | Verified at `web_front_page.py:45-46`. Moved to the real-gates table; row 38 extended |
| I7 | implementation | `test_admin.py:793` will fail — its `SimpleNamespace(email=...)` has no `role`, and `is_admin` starts reading `user.role` | **fix now** | Verified at `test_admin.py:842-847` |
| I8 | implementation | Moving the unknown-game-type test to `/api/admin/matches` loses its `"Unknown game type"` assertion | **fix now** | Verified. The message becomes `"No game module registered"` |
| I9 / T4 | both | Row 12's expected codes are wrong: `GET /matches/new` falls through to the `{match_id}` route (401 anon, 404 signed-in), `POST` gives 405 | **fix now** | The testability lens measured this against a live app. Row restated as a structural route-absence assertion plus per-actor codes |
| I10 | implementation | `game_admin_bots_web.py:18` imports the `_load_game_match_or_404` alias from `game_admin_web`; the rename would break it | **fix now** | The import is dropped entirely — the new dependency supplies the match |
| I11 | implementation | `admin_api.py` names its auth dependency `_`, so there is no `user` to build the admin `ExportViewer` from | **fix now** | Verified at `admin_api.py:57,68`. Renamed to `user` |
| T5 | testability | Row 25 is vacuous — every factory match has `created_by_user_id=None`, so rows 25 and 28 collapse into one test | **fix now** | Verified: `factories.py:175-227` has no such parameter. Added, plus a precondition assertion and distinct error codes |
| T6 | testability | Row 1 is vacuous — posting the game defaults round-trips even if the new fields are ignored | **fix now** | Verified `_CREATE_DEFAULTS` matches HHH's config exactly. Row now posts values distinct from every default |
| T7 | testability | Row 3 is vacuous — `total_rounds=25` is rejected by the engine too, so it passes with the route check absent | **fix now** | Verified at `match_creation.py:93-96`. Uses 2 (inside the engine band, outside the route band) and asserts the exact message |
| T8 | testability | Row 31's "not 403 and not 200" is impossible — the disabled-account response has three shapes depending on headers | **fix now** | Verified at `auth/session.py:29-51`. Split into three explicit expectations |
| T9 | testability | Rows 13/14/15/23 need per-seat distinct strategy text; `seat_player` cannot supply it, so "mine vs theirs" is indistinguishable | **fix now** | Verified at `factories.py:310-348`. `strategy_text=` added to the factory |
| T10 | testability | Status-only rows where the payload is the whole feature (16, 23, 27, and the bare-403 rows) | **fix now** | All now assert content or `detail.error.code` |
| T11 | testability | Nine acceptance criteria have no matrix row: AC2.8, AC2.9, AC2.10, AC1.8b, AC1.9b, AC3.5, AC3.8, AC5.1, AC6.1-bots | **fix now** | The most valuable finding of the round. All nine added |
| T12 | testability | §5's "mypy fails loudly at every consumer" is false — CI type-checks `app/ mcp_server/` only, not `tests/`, and the `monkeypatch.setattr` strings are invisible to any static tool | **fix now** | Verified at `.github/workflows/ci.yml:36`. Wording corrected: mypy plus a full pytest run |
| T13 | testability | Row 41's `wild_ones=false` is not expressible — it is a checkbox, so presence means true | **fix now** | Verified at `game_admin_web.py:127,193-196`. Row omits the field instead |
| T14 | testability | Row 22's premise is unasserted; the existing `admin_emails` fixture makes its user a game admin for every game today | **fix now** | The new test file seeds `User.role` directly and asserts the precondition |
| T15 | testability | State leakage: `settings` writes must go through `monkeypatch.setattr`; the grep row must stay a pure filesystem walk | **accept** | Recorded as a testing convention in §4 |
| I13 | implementation | §1.5 misses three owner-scoped template read sites of `strategy_text` | **fix now** | Added as "unchanged" rows for consistency |
| I14 | implementation | §1.7 skips the `export_match_csv`/`export_match_json` wrapper layer | **fix now** | Wrapper row added. The mypy claim does hold for `app/` |
| I15 | implementation | Deleting the config symbols orphans ~10 imports | **accept** | Ruff F401 catches these during the build. No plan change |
| I16 | implementation | Liar's Dice `config_defaults()` rounds/turns are outside the route band | **fix now** | Same fix as I4 |
| I17 | implementation | `game_admin/dashboard.html:19` uses `{{ game.game }}` but the context supplies `game_slug`, so the watch link renders `/games//matches/{id}` | **fix now** | Pre-existing one-word bug in a template this change already renames. Cheap to fix while here |
| I18 | implementation | `require_can_view_game`'s default 404 body differs from the export routes' bare-404 family | **fix now** | Verified pinned at `test_game_scoped_match_loader.py:219`. Pass `detail=None` there |

**Totals:** 32 findings, 30 fix now, 2 accepted without a plan change. Nothing
dropped.

## Cross-cutting note both lenses raised

Existing green coverage on these routes proves nothing about the new gates.
`test_admin.py`, `test_admin_add_bots.py` and `test_bot_form_validation.py` all
drive the game-admin routes as a `role=ADMIN` user, which is allowed under both
the old and the new model. All of it stays green whether or not the ownership
branch is ever written. **Every negative in this change must come from the new
matrix.**
