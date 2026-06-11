# Debugging History

A running log of real production incidents we've debugged: the symptom, how we
found the cause, the actual root cause, the fix, and what prevents a repeat.

**This is shared external memory for debugging.** When something is broken or
frozen in production, read this first — a past entry may be the same class of
bug, and the "How to diagnose" notes below are the fastest path in. Add a new
entry every time you debug a non-trivial production issue. Keep entries concrete:
exact table/column names, commit SHAs, PR numbers, and the query or command that
found the problem.

This file is the source of truth for debugging history. Project memory should
only point here, not duplicate the detail.

---

## How to diagnose a frozen / stuck match

Prod Postgres is reachable via `DATABASE_PUBLIC_URL` (read it from the Railway
`Postgres` service; see `docs/platform` + the deployment notes). The schema was
renamed in feature 009, so use the current names:

- Tables: `matches`, `turns` (`turns.match_id`, `turns.round`, `turns.turn`,
  `turns.phase`, `turns.resolved_at`, `turns.deadline_at`,
  `turns.talk_resolved_at`), `turn_submissions`, `turn_messages`, `players`
  (`players.seat_name`, integer `players.agent_id`), `agents`, `request_incidents`.
- There is **no** `public_id` — a match's id *is* its public id (e.g. `M_0279`).

A frozen match looks like: `matches.state = 'active'`, but the latest `turns`
row has `resolved_at IS NULL` long past `deadline_at`, and no newer turn exists.
Check whether everyone already submitted for that `turn_id` in
`turn_submissions`.

**First query on any freeze** — background turn-loop crashes are now persisted
(see the M_0279 entry):

```sql
SELECT * FROM request_incidents WHERE match_id = 'M_xxxx' ORDER BY created_at;
```

`method = 'TASK'` rows are background-loop crashes (the loop runs as a
fire-and-forget asyncio task with no HTTP request). `method`-other rows are HTTP
request failures.

**Reproducing a suspected loop crash:** run the real decision/record path against
the prod DB **with the deployed code** — check out `origin/main` in a worktree
first. A stale local branch will hit phantom schema drift (e.g. a column the
deployed schema doesn't have) and send you chasing the wrong thing.

**Why a freeze is permanent:** the per-turn deadline can't self-heal a frozen
turn — only a *live* loop resolves a turn, and there is no separate overdue-turn
sweeper. Bot decisions are deterministic, so if the loop crashes on a specific
move it will crash again on every restart/resume. A process restart (every
deploy — the app is single-instance) re-runs `resume_active_games_on_startup`,
which restarts the loop from `matches.current_round/current_turn`; that recovers
a freeze caused by an *interruption*, but not one caused by a *deterministic
crash*.

---

## Incidents

### 2026-06-11 — M_0279 frozen at Round 1 / Turn 2 (bot HELP/HURT crashed the turn loop)

**Symptom.** A practice-arena match sat frozen at R1T2 for hours. Turn 1
resolved; turn 2 opened, the talk phase resolved, then nothing — zero `act`
submissions, `resolved_at` stayed NULL, and the match never advanced. It stayed
frozen across multiple redeploys.

**Diagnosis.** `request_incidents` had nothing for the match (at the time it only
captured HTTP errors — see the fix below). Reading the rows directly showed turn
1 was all `HOARD` (no target) and turn 2 had 0 committed submissions. Replaying
turn 2's bot decisions against the live DB with the deployed (`origin/main`) code
showed the decisions themselves were fine — one bot chose `HURT Cleopatra`. The
crash was in the *write*, confirmed by running the exact target lookup against
prod.

**Root cause.** The bot auto-submit path (`app/engine/sims/service.py`) handed
`record_submission` a move whose `target_id` was the target's **public seat
name** (e.g. `"Cleopatra"`). `record_submission`
(`app/games/hoard_hurt_help/game.py`) resolves the target via
`Player.agent_id == target_id`, but `agent_id` is an **integer FK**. Postgres
raised `operator does not exist: integer = character varying`, which aborted the
whole `auto_submit_bot_phase` transaction — so *none* of the bot moves committed
and the turn never resolved. Because bot decisions are deterministic, every
resume re-ran the identical `HURT` and re-crashed → permanent freeze. The match
survived turn 1 only because every move was `HOARD` (no target).

The real-agent API path (`app/routes/agent_api.py`) already translated seat name
→ `agent_id` before recording; the bot path skipped that step. Bug introduced in
`fb6acde` (#38, the game-framework refactor).

**Fix.** [#289](https://github.com/chrislawcodes/hoard-hurt-help/pull/289)
(`8807e34`) — the bot path now builds a `seat_name → agent_id` map and translates
the target before `record_submission`, mirroring the API path. Regression test in
`tests/test_sims_scheduler.py` asserts a bot HELP/HURT records the correct integer
`target_player_id`. Once deployed, `resume_active_games_on_startup` picked the
match back up and it completed on its own.

**Prevention / follow-up.** The crash left no DB trace because `request_incidents`
was HTTP-only, which made this slow to find.
[#290](https://github.com/chrislawcodes/hoard-hurt-help/pull/290) (`ed68756`)
added `record_background_incident(...)` and wired `_run_game_guarded` to persist a
`RequestIncident` on any turn-loop crash — `method='TASK'`,
`path='scheduler:_run_game'`, `stage='turn_loop'`, round/turn in `context_json`,
plus a greppable `ops_event=turn_loop_crashed` log line.

**Lesson.** Bots reason in seat names; the DB stores integer agent ids. Translate
at *every* record boundary, not just the HTTP one.

### 2026-05-31 — G_0012 frozen by a mid-deploy restart (silent loop crash)

**Symptom.** A deploy landed mid-turn and froze G_0012 for ~8.5h at R2T5, with no
log of why.

**Root cause.** On resume the loop restarted from `current_round/current_turn`,
but `_open_turn` did a blind `INSERT` of the in-flight turn row → unique-constraint
violation on `uq_turns_game_id_round_turn` → the loop task raised. The task was
fire-and-forget with no done-callback, so the exception was never surfaced — a
silent freeze.

**Fix.** PR #45 (`229cb4b`) made `_open_turn` get-or-create (reuse the existing
turn on resume) and added a done-callback that logs loop-task crashes. PR #46
(`511b305`) closed a companion window where a restart at a round boundary could
re-run `award_round` and double-count scores (added `matches.rounds_awarded`,
migration 0008, and guarded `award_round_winners`).

**Lesson.** Every deploy interrupts live games (single instance, in-memory
scheduler). Resume must be idempotent at turn *and* round boundaries, and a
fire-and-forget task must never be able to die silently — which is why the
M_0279 follow-up now persists loop crashes to `request_incidents`.

---

## Manual recovery (last resort)

If a match is frozen and you can't ship a code fix to self-heal it, recover by
hand in one guarded transaction: compute the open turn's payoffs from its
submissions (rules in `app/engine/`: HOARD +2 self, HELP +4 to target, HURT −4,
mutual-help bonus), write each submission's `points_delta`/`round_score_after`,
bump each player's `current_round_score`, set the turn's `resolved_at`, advance
`matches.current_turn` to the *next* turn (so resume opens a fresh turn rather
than the existing row), then redeploy to restart the loop. Prefer shipping the
fix and letting `resume_active_games_on_startup` recover the match — that's how
M_0279 was unstuck — and only hand-edit prod data when there's no other path.
