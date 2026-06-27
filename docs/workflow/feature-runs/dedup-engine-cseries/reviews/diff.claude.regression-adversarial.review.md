---
reviewer: "claude"
lens: "regression-adversarial"
stage: "diff"
artifact_path: "docs/workflow/feature-runs/dedup-engine-cseries/reviews/implementation.diff.patch"
artifact_sha256: "31ecdcc9e9ebc2580f0c7944ff1b8e3fe89ff0c6f24388042981854ecea0f623"
repo_root: "."
git_head_sha: "2e415669af9ef0a423647580d7a21465292fbdf2"
git_base_ref: "origin/main"
git_base_sha: "9d36fdc28273b44ec7b04fbdaf747b1b9f18c221"
generation_method: "claude-subagent"
resolution_status: "accepted"
resolution_note: "Whole-branch regression review: no behavior change found across all clusters (C4 exclude_reserved per site, C8 all 8 sites field-only with own now/commit + registry.stop kept, C6 PAUSED+LOOP constant preserved, C1/C5/C7 equivalent, C2 distinct). Two minors noted: Preflight lint/type run green on every slice; mark_cancelled commit-adjacency is advisory."
raw_output_path: "docs/workflow/feature-runs/dedup-engine-cseries/reviews/diff.claude.regression-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: diff regression-adversarial

## Findings

None. Every focus area was verified against the live source in `app/engine/`:

- **C4 count sites** — all four map correctly. Start-floor `_active_player_count` → `exclude_reserved=True` (`scheduler.py`); watchdog → `exclude_reserved=False` (includes held seats); arena confirmed → `True`, seated → `False` (`arena.py`). `active_player_count` (`player_counts.py`) appends the `seat_reserved_until IS NULL` filter only when `True`, matching both originals.
- **C8 cancel sites** — all 8 call `mark_cancelled`: 7 inline (`scheduler.py:175,307,387`; `arena.py:175,286,316`; `scheduler_turn_loop.py:212`) plus `cancel_match` (`match_deletion.py:42`). Each preserves its own `now` (fresh vs captured) and its own `await db.commit()`. `cancel_match` keeps `registry.stop`. `mark_cancelled` is field-only. Grep found no surviving raw `.state = GameState.CANCELLED` writes outside `mark_cancelled`.
- **C6** — `_connection_is_live` keeps the PAUSED early-return before the window check. `provider_loop_running` still passes `LOOP_RUNNING_WINDOW_SECONDS`, not `LIVE_WINDOW_SECONDS`. `within_window` is semantically identical to both inlined originals (`None → False`; `ensure_aware` applies the same naive→UTC fallback; same `<=` comparison).
- **C1** — `now_utc()`/`SUBMIT_POLL_SECONDS` are exact replacements; deadlines and poll cadence unchanged in both drivers; constant value (0.25) and `datetime.now(timezone.utc)` semantics identical.
- **C5** — `has_moved` is equivalent to both removed `_has_moved` bodies (`.limit(1)`, `was_defaulted.is_(False)` join). `mark_first_move` is untouched (distinct `.limit(2)` + exactly-one logic). The two onboarding state machines remain distinct.
- **C2** — `_open_turn` (get-or-create, writes both pointers) and `_open_actor_turn` (blind INSERT, `current_turn` only) remain distinct, documented as not-a-true-duplicate.
- **Public surface / cycles** — `_within_window` → `within_window` rename consistently updated across `connection_health.py` (`__all__`), `connection_activity.py`, `provider_readiness.py`. All four new leaf modules import only models + stdlib/sqlalchemy. `user_match_start` (new home of `is_bot_kind`) imports only `app.models.*`, so `turn_drivers`/`arena` importing it introduces no cycle. All touched modules import cleanly; an AST pass found zero unused imports.
- **C7** — `_public_standings` delegates to `_scoreboard_order`, whose sort key `(-current_round_score, seat_name)` is byte-for-byte the original lambda.

No swallowed-error/silent-fallback regressions: the only `except Exception` in the touched files (`scheduler.py`) predates this diff and is labelled `# fail-open: advisory only`.

## Residual Risks

- **[minor]** Ruff/mypy were not run in the read-only review environment; runtime import checks + an AST unused-import scan were substituted (both clean). The Preflight Gate's full lint/type pass is the authoritative gate — and was run green on every slice (ruff + mypy 183 files + pytest 1331).
- **[minor]** `mark_cancelled` makes it marginally easier for a future caller to forget the accompanying `await db.commit()`, since the commit is no longer adjacent to the field writes. Not a regression in this diff — all 8 current callers retain their commit — but the field-only design relies on each caller remembering it.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: Whole-branch regression review: no behavior change found across all clusters (C4 exclude_reserved per site, C8 all 8 sites field-only with own now/commit + registry.stop kept, C6 PAUSED+LOOP constant preserved, C1/C5/C7 equivalent, C2 distinct). Two minors noted: Preflight lint/type run green on every slice; mark_cancelled commit-adjacency is advisory.
