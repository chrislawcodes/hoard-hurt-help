# Running a real-LLM match

How to put N agents on real models into a live Hoard-Hurt-Help match and get a
result you can trust. Recovered from the v7 measurement runs (2026-08-18/19) and
written down because the working scripts were living in a temp directory.

**This is the MCP path, and it needs no API key.** Each agent is a `claude
--print` (or `codex exec`) session driving the `mcp__agentludum__*` tools over
the OAuth connection you already signed in with. The `sk_conn_` connector
(`scripts/agentludum_connector.py`) is a different, always-on route — you do not
need it for a one-off measurement run.

## The order is load-bearing — get it wrong and the match dies silently

**START THE PLAY LOOPS BEFORE YOU JOIN.** This is not a preference; joining in
the wrong order destroys the match without an error message anywhere.

Joining with an AI that is not already running creates a **held** seat
(`players.seat_reserved_until` set). A held seat is not a real player: it does
not count toward the start floor, and `release_held_seats` **DELETES** every
still-held seat the moment the match starts. So a match whose eight seats are all
held starts, loses all eight players, and is then cancelled for having fewer than
`MIN_PLAYERS_TO_START` (3). The lobby shows eight agents right up until you press
start, so nothing looks wrong until it is over. This cost a match on 2026-08-19.

`sweep_held_seats` runs every ~2s and confirms a held seat as soon as its chosen
provider reads LIVE, so once the loops are polling the seats fix themselves
within seconds. The working order:

1. **Create the agents** — one per seat, each picking the preset you are testing.
   Note each agent's numeric id; the runner keys off it. **Do this AFTER the
   change has deployed** — a preset's text is copied into the agent at creation,
   so an agent made too early silently measures the old strategy.
2. **Create the match**, scheduled far enough out that the auto-start poller does
   not fire before you are ready (it cancels a due match with too few confirmed
   players).
3. **Start the play loops.** They will poll, get `no_game`, exit, and be
   relaunched by their supervisor — that is fine and expected. What matters is
   that the provider now reads LIVE. Confirm on `/me/connections`: it should say
   **"Your AI is playing"**.
4. **Join every agent.** Seats confirm within a couple of seconds.
5. **Start the match**, and check `get_game_state` immediately — the agents list
   should still hold every seat, with `model_self_report` filled in. If the list
   came back empty, the seats were held and you are already dead.

Steps 1, 2, 4 and 5 need a signed-in browser: there is no `create_agent` or
`create_match` MCP tool, and joining is deliberately a per-match action
(auto-join was proposed and rejected).

## What the scripts do

```bash
RUN=/tmp/hhh-run-$(date +%H%M%S)          # NOT inside the repo — see below
mkdir -p "$RUN"
cp scripts/match_runner/{play_agent.sh,start_loops.sh,prompt_template.txt} "$RUN/"

# one "agent_id<TAB>name<TAB>provider" per line; provider is claude or codex
cat > "$RUN/roster.txt" <<'ROSTER'
601	Tit-for-Tat	claude
602	Loyal Partner	claude
ROSTER

"$RUN/start_loops.sh" "$RUN"              # one supervised loop per agent
touch "$RUN/STOP"                         # stop them all
```

- `start_loops.sh` reads `roster.txt` and starts one supervisor per agent.
- `play_agent.sh` is that supervisor. It relaunches the CLI until `STOP` exists,
  because `claude --print` and `codex exec` both EXIT when the model decides it
  has finished — a single invocation dies mid-match. It backs off 300s on a
  provider quota wall rather than burning relaunches against it.
- `prompt_template.txt` is the per-agent brief. `__AGENT_ID__` / `__AGENT_NAME__`
  are substituted per seat.

## The traps, each of which cost a run

- **Run from an isolated directory, never the repo.** The repo's `CLAUDE.md` /
  `AGENTS.md` is a Python engineering contract, and a model that reads it decides
  it was handed a coding task and refuses to play ([[agent-play-loops-load-repo-claude-md]]).
- **Every `get_next_turn` call must pin `agent_id`.** An unpinned call returns
  some OTHER agent's turn and steals it. The prompt says so three times for a
  reason.
- **A leftover `STOP` kills the next run instantly.** `start_loops.sh` removes
  it first; if you hand-roll a launcher, do the same.
- **Never `codex exec --sandbox read-only`** — it blocks outbound network, so
  every HTTP MCP call fails, reported only as "user cancelled MCP tool call".
- **One match at a time.** Throughput is fixed at about one match per 23 minutes
  however you arrange it; concurrency buys nothing and pushes the default rate
  from under 1% to 9-11%. A defaulted move IS a HOARD, so defaults contaminate
  exactly the hoard-rate number a payoff change is trying to measure.
- **An ACTIVE match cannot be stopped.** There is no cancel path once it starts;
  the only lever is an admin delete that destroys every turn and message. Plan
  the run so you never need to.

## Before believing the result

Check all three (the fuller version is in `.claude/skills/diagnostics-and-tooling/`):

1. **Move and talk validity** — a match where agents could not answer still
   completes and looks normal, with the missing turns silently scored as HOARD.
2. **Field an identical-twin pair.** The measured noise floor between two
   byte-identical agents is about 2.0 round wins and 72 points, which is larger
   than any preset difference yet observed. A single match cannot rank
   strategies.
3. **Compare like stages.** Hoard rate by round runs 24/24/27/31/24/17/16%, so a
   young match is not comparable with a finished one.
