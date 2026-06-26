## Findings

**[minor] C5 reuse-report's "byte-identical" premise is false; the equivalence test must verify query equivalence, not identity.** [CODE-CONFIRMED] `connection_activity.py:80` (`bot_id`, inline `.execute(stmt).first()`) vs `agent_onboarding.py:127` (`agent_id`, assigns `row`) — same join, same `was_defaulted.is_(False)`, same `limit(1)`, so the merge is valid, but not byte-identical. The plan already commits to a `has_moved` boundary test (Slice 6); flagging only that the stated rationale is wrong, so the implementer shouldn't skip the equivalence assertion. The test must assert defaulted-only → False and one real submission → True.

**[minor] C6 rewire of `provider_loop_running` crosses the tz-normalization path; no explicit verification pins that equivalence.** [CODE-CONFIRMED] `provider_loop_running` (`provider_readiness.py:213-222`) normalizes naive timestamps inline (`last_polled.replace(tzinfo=timezone.utc)`) then `<= LOOP_RUNNING_WINDOW_SECONDS`; `within_window` normalizes via `ensure_aware` (`aware_datetime.py:8-10`, behavior-identical). Safe today, but the C6 verification only checks the constant + PAUSED guard. Recommend the C6 test include one naive-timestamp row exactly `LOOP_RUNNING_WINDOW_SECONDS` old to lock the `<=` and tz-normalization equivalence.

**[minor] C3 `turn_drivers._is_bot` omits the `.value` check; the promoted predicate widens it (safe superset) but Slice 2 has no test.** [CODE-CONFIRMED] `turn_drivers._is_bot` checks `agent.kind == AgentKind.BOT` (member only); `is_bot_kind` accepts member or raw string. DB always returns the enum member, so widening is safe. Suggest a one-line assertion: `is_bot_kind(AgentKind.BOT) and is_bot_kind(AgentKind.BOT.value) and not is_bot_kind(<non-bot kind>)`.

Prior-round items — all confirmed resolved:
- Full pytest (not fast lane) for DB-backed char tests + red-then-green: stated explicitly. [CODE-CONFIRMED via plan text]
- C4-watchdog polarity + has_moved boundary: watchdog (`scheduler.py:313-317`) filters `left_at` only → `exclude_reserved=False`; `_active_player_count` filters both → `True`. Correct. [CODE-CONFIRMED]
- C8 per-site "now" + all-7-sites: assignment-scoped regex returns exactly the 7 inline sites + `match_deletion.py`, excluding transition literals/membership tests. [CODE-CONFIRMED]

## Residual Risks

- **C2 disposition is still a judgment call at implementation time.** The divergence is real and structural; `not-a-true-duplicate` is well-supported and the C2-seq/C2-sim tests hold under either disposition. The diff-checkpoint must enforce "reads cleanly with no hidden 4th branch." [CODE-CONFIRMED]
- **Baseline collected-count check is necessary but not sufficient.** It guards dropped tests, not weakened assertions; red-then-green + diff-checkpoint mitigate. [UNVERIFIED — reviewer diligence]
- **C6 tz equivalence rests on `ensure_aware` staying assume-UTC.** Equivalent today; a future change to `ensure_aware` would alter `provider_loop_running` once routed through `within_window`. Low likelihood. [CODE-CONFIRMED current state]
