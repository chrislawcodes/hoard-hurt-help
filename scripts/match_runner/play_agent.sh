#!/bin/bash
# Supervise ONE agent's play loop for the whole match.
#
# claude -p and codex exec both exit when the model decides it is finished, so a
# single invocation dies mid-match. This relaunches until the run directory's
# STOP sentinel appears, backing off hard on provider quota errors.
#
# usage: play_agent.sh <agent_id> <agent_name> <provider> <run_dir>

set -uo pipefail

AGENT_ID="$1"; AGENT_NAME="$2"; PROVIDER="$3"; RUN_DIR="$4"
# Which Claude model plays. Haiku by default because it is the cheap one these
# runs are built around, but a match is a different experiment on a different
# model — so it is settable, and the export now records what actually played
# rather than leaving it to a shell history nobody kept.
CLAUDE_MODEL="${CLAUDE_MODEL:-claude-haiku-4-5}"
LOG="$RUN_DIR/agent_${AGENT_ID}.log"
# Its own working directory, created by setup_mcp_key.sh. Claude Code files MCP
# logs by working directory, so this is what lets the monitor tell one agent's
# turns from another's. Logs and prompts still live in the run dir.
WORK_DIR="$RUN_DIR/a/$AGENT_ID"
mkdir -p "$WORK_DIR"
PROMPT="$RUN_DIR/prompt_${AGENT_ID}.txt"

sed -e "s/__AGENT_ID__/$AGENT_ID/g" -e "s/__AGENT_NAME__/$AGENT_NAME/g" \
    "$RUN_DIR/prompt_template.txt" > "$PROMPT"

# THE RUN DIR IS SIGNED IN WITH A CONNECTION KEY, NOT A GOOGLE SIGN-IN.
#
# Nothing is set here: setup_mcp_key.sh registers the key against THIS run
# directory's path in the normal Claude config, and the sessions below already
# `cd "$RUN_DIR"`, so they pick it up and the repo checkout keeps its own
# Google sign-in untouched.
#
# Why a key at all: the Google sign-in renews with a single-use ticket, so eight
# agents sharing one eventually renew at the same moment and the second is left
# holding a ticket the server has already deleted — signed out for good. M_7360
# died that way nine minutes in, 2,584 sessions burned, six turns defaulted, and
# a defaulted move scores as a HOARD. A key never renews, so sharing one across
# eight agents is safe.
#
# Do NOT reach for CLAUDE_CONFIG_DIR to isolate the agents. It was tried: it
# separates which servers are configured, but on macOS credentials live in the
# system keychain (so per-agent logins overwrite one entry) AND a fresh config
# dir has no Claude account login at all, so every session dies on
# "Not logged in · Please run /login".

TOOLS="ToolSearch,mcp__agentludum__get_next_turn,mcp__agentludum__submit_talk,mcp__agentludum__submit_action,mcp__agentludum__get_game_state,mcp__agentludum__get_instructions,mcp__agentludum__get_chat"

# A session finishing faster than this did not play a turn, whatever it says
# and whatever it exits with. A real turn takes ~100s.
FAST_FAIL_SECONDS=15
# Ceiling on the wait between retries. The subscription wall can last hours, so
# this is generous — a stuck supervisor costs nothing, a hot loop costs quota.
MAX_BACKOFF_SECONDS=900

# TWO BUDGETS THAT NEED NOTHING ELSE TO BE WORKING.
#
# Everything else — the stall check, the storm alarm, the self-stop when a match
# ends — lives in ONE watcher subshell inside run_all.sh, and only runs when
# MATCH_ID was set and the match reads "active". So all of it is absent before a
# match starts (exactly when agents churn on "no_game"), absent if MATCH_ID was
# forgotten, and absent if that one subshell dies. A storm in any of those
# windows is unbounded, which is how 3,060 sessions happened.
#
# These two live in the supervisor itself. No watcher, no match state, no
# diagnosis of what went wrong — they just refuse to keep spending.
#
# RUN_LAUNCH_BUDGET: every launch by every agent, capped for the whole run. A
# healthy 35-turn match uses well under 250. At a storm's measured 4.5 launches
# a minute per agent this caps the damage at roughly ten minutes.
RUN_LAUNCH_BUDGET="${RUN_LAUNCH_BUDGET:-400}"
# AGENT_LAUNCH_BUDGET: launches by THIS agent since it last actually played a
# turn. Generous because a legitimate pre-match wait plays nothing at all: the
# loops must be live before anyone joins, and that window is minutes of honest
# churn. The watcher catches an in-match storm in 3 minutes; this only has to
# catch the case where the watcher is not there at all.
AGENT_LAUNCH_BUDGET="${AGENT_LAUNCH_BUDGET:-60}"

# Turns this agent has actually played, from Claude Code's own MCP log for its
# working directory. The same signal the watcher uses, read locally: a
# successful submit_action is a turn played, and nothing else is.
plays_so_far() {
  local real mcpdir
  real=$(cd "$WORK_DIR" 2>/dev/null && pwd -P) || { echo 0; return; }
  mcpdir="$HOME/Library/Caches/claude-cli-nodejs/$(printf '%s' "$real" | tr '/' '-')/mcp-logs-agentludum"
  # No `|| echo 0` — grep -c prints its count AND exits 1 at zero, so a fallback
  # would append a SECOND zero and every comparison against it would fail.
  grep -rh 'submit_action' "$mcpdir" 2>/dev/null | grep -c 'completed successfully'
}

stop_run() {  # halt every agent, not just this one
  echo "!!! $1" >> "$LOG"
  echo "!!! $1" >&2
  touch "$RUN_DIR/STOP"
}

launch=0
fast_fails=0
launches_since_play=0
last_played=$(plays_so_far)
while [ ! -f "$RUN_DIR/STOP" ]; do
  # Run-wide budget. One line appended per launch; short appends from separate
  # processes do not interleave, so counting lines is a safe shared total.
  echo "$AGENT_ID" >> "$RUN_DIR/.launch_ledger"
  spent=$(wc -l < "$RUN_DIR/.launch_ledger" | tr -d ' ')
  if [ "$spent" -gt "$RUN_LAUNCH_BUDGET" ]; then
    stop_run "run launch budget spent: $spent launches across all agents (cap $RUN_LAUNCH_BUDGET). Something is wrong; stopping before it costs more quota."
    break
  fi

  # This agent's own budget: launches since it last actually played a turn.
  played=$(plays_so_far)
  if [ "$played" != "$last_played" ]; then
    last_played="$played"
    launches_since_play=0
  fi
  if [ "$launches_since_play" -gt "$AGENT_LAUNCH_BUDGET" ]; then
    stop_run "agent $AGENT_ID has launched $launches_since_play sessions without playing a single turn (cap $AGENT_LAUNCH_BUDGET). Read the newest lines of $LOG."
    break
  fi
  launches_since_play=$(( launches_since_play + 1 ))

  launch=$((launch + 1))
  echo "=== launch #$launch ($PROVIDER) $(date -u +%H:%M:%S)Z ===" >> "$LOG"
  started_at=$(date +%s)

  if [ "$PROVIDER" = "claude" ]; then
    # Run from the isolated run dir, NOT the repo: the repo's CLAUDE.md is a
    # Python engineering contract and makes the model think it was handed a
    # coding task instead of a game to play.
    ( cd "$WORK_DIR" && claude --print --model "$CLAUDE_MODEL" \
        --allowedTools "$TOOLS" < "$PROMPT" ) >> "$LOG" 2>&1
  else
    # NO --sandbox read-only: it blocks outbound network, so every HTTP MCP
    # server fails on each call, reported only as "user cancelled MCP tool
    # call". Run from the isolated run dir (not the repo) so codex does not read
    # AGENTS.md and mistake a game for a coding task.
    ( cd "$WORK_DIR" && \
      codex exec --skip-git-repo-check -m gpt-5.4-mini "$(cat "$PROMPT")" < /dev/null ) \
      >> "$LOG" 2>&1
  fi
  rc=$?
  elapsed=$(( $(date +%s) - started_at ))
  echo "--- exited rc=$rc after ${elapsed}s $(date -u +%H:%M:%S)Z ---" >> "$LOG"

  [ -f "$RUN_DIR/STOP" ] && break

  # BACK OFF ON THE SHAPE OF THE FAILURE, NOT ON ITS WORDING.
  #
  # This used to grep the log for "quota reached|rate limit|usage limit|too many
  # requests". The real subscription wall says "You've hit your session limit",
  # which matches NONE of those, so the supervisor fell through to `sleep 3` and
  # retried every three seconds for hours. One agent in M_6855 logged 3,873
  # launches to play 24 turns: 3,848 of them were the wall, hammered in a tight
  # loop, and the quota it burned is what stalled the match's final round.
  #
  # A session that exits non-zero within seconds did not play a turn — it hit a
  # wall of some kind. That is true whatever the message says, and it stays true
  # when the wording changes again. Wait longer each time, and reset the moment a
  # session runs long enough to have actually played.
  #
  # THE EXIT CODE IS NOT PART OF THE TEST, and that is the third lesson here.
  # Keying on `rc != 0` looked equivalent and was not: when the sign-in died in
  # M_7360 the agent failed *politely* — it explained the problem, told the user
  # to run `/mcp`, and exited rc=0. A clean exit sailed straight past a check
  # built for crashes, so 1,595 sessions relaunched at full speed.
  #
  # Duration alone is the honest signal. A real turn takes ~100s; there is no
  # such thing as a healthy ten-second session. That stays true however the next
  # failure chooses to present itself.
  if [ "$elapsed" -lt "$FAST_FAIL_SECONDS" ]; then
    fast_fails=$(( fast_fails + 1 ))
    backoff=$(( 30 * fast_fails ))
    [ "$backoff" -gt "$MAX_BACKOFF_SECONDS" ] && backoff=$MAX_BACKOFF_SECONDS
    echo "!!! fast failure #$fast_fails (rc=$rc after ${elapsed}s) — waiting ${backoff}s" >> "$LOG"
    sleep "$backoff"
  else
    fast_fails=0
    sleep 3
  fi
done

echo "=== supervisor stopped after $launch launches $(date -u +%H:%M:%S)Z ===" >> "$LOG"
