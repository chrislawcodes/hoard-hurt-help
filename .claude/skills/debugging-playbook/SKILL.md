---
name: debugging-playbook
description: Symptom→triage playbook for Hoard Hurt Help production problems — a match is frozen or stuck, an agent only plays HOARD or looks stalled, talk messages go missing, an MCP client shows "waiting" or silently stops playing, scores look doubled, or a deploy broke something live. Load this FIRST for any live/prod misbehavior, before exploring code: it routes each symptom to its most likely cause, the first query to run, and the known trap. Do NOT use it for already-settled history questions (use failure-archaeology), for offline measurement or analysis (diagnostics-and-tooling), or for local dev environment setup (docs/setup-dev.md).
---

# Debugging Playbook

Triage first, explore second. Most prod symptoms here have happened before and
have a known fastest path in. Detailed incident write-ups and the frozen-match
walkthrough live in `docs/operations/debugging-history.md` — that file is the
source of truth; this skill is the router that gets you to the right query in
one step.

Sibling skills: settled past battles → `failure-archaeology`. Measuring instead
of eyeballing → `diagnostics-and-tooling`.

## Ground rules (learned the hard way)

1. **Diagnose before fixing** (`CLAUDE.md`). Find the smallest reproducing
   case. Never change multiple things at once.
2. **Reproduce with the DEPLOYED code.** Check out `origin/main` in a worktree
   before replaying anything against prod. A stale local branch hits phantom
   schema drift and sends you chasing the wrong bug (M_0279 lesson).
3. **The app is single-instance and the scheduler is in-process asyncio.**
   Every deploy interrupts live games; recovery relies on
   `resume_active_games_on_startup`. Bot decisions are deterministic — a loop
   that crashes on a specific move crashes again on every restart. So:
   restart fixes an *interruption* freeze, never a *deterministic-crash*
   freeze.
4. **When you're done, write the incident up** in
   `docs/operations/debugging-history.md` (symptom, diagnosis, root cause,
   fix, prevention — with SHAs, PR numbers, and the query that found it), and
   add an index row to the `failure-archaeology` skill.

## Access

- Prod Postgres: `DATABASE_PUBLIC_URL` from the Railway `Postgres` service
  (see `docs/deploy-railway.md` / `docs/platform`).
- Logs: `ops_event=` lines are greppable markers (e.g.
  `ops_event=turn_loop_crashed`).
- Schema names that matter: `matches`, `turns`, `turn_submissions`,
  `turn_messages`, `players` (`seat_name`, integer `agent_id`), `agents`,
  `connections`, `request_incidents`. A match's id IS its public id
  (`M_0279`); there is no `public_id` column.

## Symptom → triage

| Symptom | Most likely cause | First move |
|---------|-------------------|-----------|
| Match frozen: `matches.state='active'`, latest turn `resolved_at IS NULL` long past `deadline_at` | Turn-loop crash (deterministic → permanent) or mid-deploy interruption | `SELECT * FROM request_incidents WHERE match_id='M_xxxx' ORDER BY created_at;` — `method='TASK'` rows are background-loop crashes. Then the frozen-match walkthrough in `debugging-history.md` |
| A seat submits only fallback HOARD every turn | Wrong model/provider reaching the CLI, or the model keeps emitting an invalid move | Check the connector's per-turn log for the CLI command + failure reason; check the seat's provider/model resolution. Two past causes (stale legacy model; codex resume flag order): see #569 in `failure-archaeology` |
| Agent looks stalled; long gaps between its turns | The client is sleeping on some timestamp instead of polling | Confirm responses carry `next_poll_after_seconds=0` after submit (#541); check the client isn't running its own shell sleep |
| Talk messages missing, agents act without talking | Talk arrived after the window; historically a `STALE_TURN_TOKEN` rejection | Grep for `STALE_TURN_TOKEN` / `talk_window_closed`. The stable-token invariant (#540) makes late talk a graceful 202 — if you see hard staleness inside one turn, the invariant regressed |
| MCP page stuck on "waiting" while the AI is actually playing | Connection identity collapse — clients keyed on a shared id | `SELECT id, provider, oauth_client_id, last_polled_at FROM connections WHERE user_id=<id>;` — two clients sharing one `oauth_client_id` is the #454 signature (fixed by #456; rows self-heal on next initialize) |
| MCP client silently stops playing mid-game | Client-side loop detection tripped by oversized tool output | Check the poll payload size — history must stay a rolling window (`RECENT_HISTORY_TURNS`), never the full transcript (architecture "tensions", `lean-poll-history`) |
| Scores doubled / round awarded twice | Round-boundary resume idempotency broken | Check `matches.rounds_awarded` guard (#46). A restart at a round boundary must not re-run `award_round` |
| Deploy crashes on a migration | Dialect drift — SQLite-tested migration hitting Postgres semantics | Read the failing migration; past example: boolean backfill `boolean = 0` (migration 0030 fix). Test both dialects |

## Discriminating experiments

- **Crash vs interruption freeze:** redeploy (or wait for one). If the match
  resumes, it was an interruption. If it re-freezes at the same turn, it's a
  deterministic crash — find it in `request_incidents`, then replay that
  turn's decision/record path against prod **with deployed code**.
- **Server vs connector fault for a bad move:** the server logs the rejected
  submission (`request_incidents`, HTTP rows); the connector logs what the CLI
  actually returned. Follow whichever side shows the first error.
- **Data vs code:** run the same query/path against a local DB seeded by
  `scripts/new_test_game.py`. Reproduces locally → code. Prod-only → data or
  environment.

## Manual recovery

Last resort only — prefer shipping the fix and letting
`resume_active_games_on_startup` recover the match. The guarded hand-recovery
transaction (compute payoffs, resolve the turn, advance `current_turn`, then
redeploy) is spelled out at the bottom of
`docs/operations/debugging-history.md`. Don't improvise a variant.

## Provenance and maintenance

Written 2026-07-02, distilled from `docs/operations/debugging-history.md`
(incidents M_0279, G_0012, MCP-waiting) and PRs #45/#46, #289/#290, #454/#456,
#540, #541, #569, #586.

Re-verify when suspicious:
- Schema names: `grep -rn "class Match\|class Turn\|class RequestIncident" app/models/`
- The incident doc still exists and starts with the frozen-match walkthrough: `head -60 docs/operations/debugging-history.md`
- `ops_event` markers: `grep -rn "ops_event=" app/ | head`
