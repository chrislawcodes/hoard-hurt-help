# Tasks — Role Simplification

## Slicing decision: ONE SLICE

The engine guide's "Keep Diffs Scoped" section
(`docs/workflow/operations/codex-skills/feature-factory/SKILL.md`) says to slice
for **ordered steps**, a diff **clearly over ~300 changed lines**, or
**data-critical gates**. This change has none of the three:

- **No ordered steps.** There is no migration, no backfill, no rollout script.
  Every file can change in any order and the app compiles at the end.
- **No data-critical gate.** Nothing writes to production data. The one data
  shape that changes (`state_config` becoming `{}` for non-Liar's-Dice games)
  affects only newly created matches; existing rows are untouched and unread.
- **The diff is large but shallow.** Most of it is deletion, re-gating and a
  rename. A rename split across checkpoints would leave the app un-importable
  mid-slice, which is worse than one coherent commit.

The whole-diff review fan (stage 5) is the real defense here, not slice
boundaries.

---

## Build order

Follows `plan.md` §5. Each step ends with the app still importable.

- [ ] **T1** `app/read_models/match_export.py` — `ExportViewer`; redact
      `strategy_prompt`; `resolved_only = not is_platform_admin`; required
      keyword-only `viewer` on `gather_export_rows`, `build_csv_export`,
      `build_json_export`; rewrite the byte-identical docstring claim.
- [ ] **T2** `app/schemas/admin.py` — `field_validator` parsing
      `mutual_help_mode` through `MutualHelpMode`.
- [ ] **T3** `app/routes/match_authz.py` (new) — `OwnedOrAdminMatch`,
      `ExportableMatch`, `AdminMatch`, `PlatformAdminForGame`. Check order per
      plan §2.1. **No `require_platform_admin` in any signature.**
- [ ] **T4** `app/deps.py` — delete `require_game_admin`.
- [ ] **T5** `app/config.py` — delete `game_admin_emails_for`,
      `all_game_admin_emails_set`, `_game_admin_emails_raw`,
      `_collect_game_admin_emails`, the comment block, and orphaned imports.
- [ ] **T6** `app/routes/web_support.py` — narrow `_is_any_admin`; delete
      `_is_game_admin`. `app/routes/web_viewer_context.py` — drop the import and
      the `is_game_admin` context key.
- [ ] **T7** Rename `game_admin_actions.py` → `admin_match_actions.py`; thread
      `viewer` through both export wrappers; add the shared `state_config_for`
      helper and use it in `create_game_record`.
- [ ] **T8** Rename `game_admin_web.py` → `match_manage_web.py`; delete both
      create routes; re-gate the remaining 5; redact strategies (bots exempt);
      real `is_admin`.
- [ ] **T9** Rename `game_admin_bots_web.py` → `match_bots_web.py`; re-gate 2
      routes; real `is_admin`; **delete** the cross-module
      `_load_game_match_or_404` import.
- [ ] **T10** Rename `game_admin_api.py` → `match_export_api.py`; delete create
      and cancel; open both exports via `ExportableMatch`.
- [ ] **T11** `app/routes/admin_api.py` — rename `_` → `user` on both export
      routes; pass an admin `ExportViewer`.
- [ ] **T12** `app/routes/matches_user.py` — merged create route: `_form_defaults`
      replaces `_CREATE_DEFAULTS`; drop the `is_admin_only` block; all seven
      fields; the six validations; admin-gated mode; `create_match_with_state`
      with `state_config_for`.
- [ ] **T13** Templates — rename `game_admin/` → `match_manage/`; delete its
      `create_match.html`; repoint 4 create links; fix `{{ game.game }}`; gate
      the start form with `{% if is_admin %}`; rebuild
      `matches_user/create_match.html` with every field.
- [ ] **T14** `app/main.py` — update the 3 router imports and includes.
- [ ] **T15** `tests/factories.py` — `make_match(created_by_user_id=…)`,
      `seat_player(strategy_text=…)`.
- [ ] **T16** Rewrite the existing tests listed in plan §1.11 (11 sites across 3
      files). **Rewrite, never delete** — `test_admin.py:760` is the only pinned
      guard behind contract item 13.
- [ ] **T17** `tests/test_role_simplification.py` — the 55-row matrix from plan
      §4. Row 55 is a filesystem walk with the needle built from parts.
- [ ] **T18** Full Preflight Gate: `ruff` + `mypy app/ mcp_server/` + `pytest -q`.

## Definition of done

1. `grep -rn "game_admin\|GAME_ADMIN" app/ tests/` returns nothing (row 55 pins it).
2. One match-creation path, mode field gated.
3. All 55 matrix rows exist and pass.
4. Findings-verdict table complete for the review fan.
5. Full Preflight Gate green.
6. PR with a `Validation` section.
