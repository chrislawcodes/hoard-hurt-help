## Findings

**[minor]** C5 — the reuse-report's "byte-identical" claim for `_has_moved` is inaccurate; the plan's mitigation is sound. [CODE-CONFIRMED] `connection_activity._has_moved` (`bot_id`, pre-built `stmt`, `.first() is not None`) and `agent_onboarding._has_moved` (`agent_id`, inline query, `row = ...; return row is not None`) differ in parameter name, docstring, and statement structure. They are *semantically* equivalent (same join/filter/limit), so the unify verdict holds, but they are not "byte-identical." The plan already neutralizes this via the C5 `has_moved` boundary test. Recommend correcting the inherited "byte-identical" descriptor so the implementer doesn't skip the equivalence test on a false assumption.

**[minor]** C4 — the watchdog conversion replaces an inline left-only query with a call; the plan states the right mode but should flag it as the one inline-replacement site, not a uniform "rewire 4 sites." [CODE-CONFIRMED] `_watchdog` (`scheduler.py:313-317`) inlines `count(...) where match_id == g.id and left_at is None` (no reserved filter). `exclude_reserved=False` reproduces it and the C4-watchdog test guards it; the disposition grep should explicitly confirm the watchdog's former inline query is gone and replaced by `active_player_count(..., exclude_reserved=False)`.

All other prior-round items confirmed resolved:
- **C8 = 7 inline sites** [CODE-CONFIRMED]: `scheduler.py:184,319,400`, `arena.py:188,300,331`, `scheduler_turn_loop.py:214` + `match_deletion.py:41`. The assignment-scoped regex `\.state\s*=\s*GameState\.CANCELLED` returns exactly these and nothing else — tightened check is sound.
- **C8 per-site `now` heterogeneity** [CODE-CONFIRMED]: captured `now` (scheduler ×3, `arena.py:300,331`) vs fresh `datetime.now(timezone.utc)` (`arena.py:188`, `scheduler_turn_loop.py:214`, `match_deletion.py:42`). "Each caller passes its OWN `now`" is correct.
- **C4 `fill_match_with_bots` two calls** [CODE-CONFIRMED]: confirmed (`98-108`) + seated (`109-115`); `used_names`/`bot_count` correctly excluded.
- **C6 `provider_loop_running` non-trivial** [CODE-CONFIRMED]: inlines a per-row loop with `LOOP_RUNNING_WINDOW_SECONDS` (=120), distinct from `LIVE_WINDOW_SECONDS`. Pass the right constant, keep the loop + guards. `provider_readiness` already imports from `connection_health_badge` (cycle-free).
- **C2 defer-expected** [CODE-CONFIRMED]: `_open_turn` is a get-or-create; openers differ structurally.
- **C5 shim/rename** [CODE-CONFIRMED]: `connection_health.py.__all__` re-exports the liveness symbols; `agent_idle.py:82` owns `_UPCOMING_STATES`.

## Residual Risks

- **C6 same-file vs cross-module asymmetry.** `_connection_is_live` is same-file with `within_window` (trivial); `provider_loop_running` is cross-module. Slice 3 covers both; import smoke test retains `provider_readiness`. Low risk, doc-precision only.
- **C5 equivalence test is the only guard against a wrong unify.** The test should cover a defaulted-only submission (expect False) and a mixed real+defaulted set (expect True) so both prior implementations' filter is locked.
- **C4 watchdog inline-to-call must appear in the disposition grep.** Assert the former inline `count(...) where left_at is None` is gone and replaced by `active_player_count(..., exclude_reserved=False)`, not just the two `fill_match_with_bots` keywords.
