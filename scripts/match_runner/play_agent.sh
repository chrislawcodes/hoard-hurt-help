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
LOG="$RUN_DIR/agent_${AGENT_ID}.log"
PROMPT="$RUN_DIR/prompt_${AGENT_ID}.txt"

sed -e "s/__AGENT_ID__/$AGENT_ID/g" -e "s/__AGENT_NAME__/$AGENT_NAME/g" \
    "$RUN_DIR/prompt_template.txt" > "$PROMPT"

TOOLS="ToolSearch,mcp__agentludum__get_next_turn,mcp__agentludum__submit_talk,mcp__agentludum__submit_action,mcp__agentludum__get_game_state,mcp__agentludum__get_instructions,mcp__agentludum__get_chat"

# A session exiting non-zero faster than this did not play a turn.
FAST_FAIL_SECONDS=15
# Ceiling on the wait between retries. The subscription wall can last hours, so
# this is generous — a stuck supervisor costs nothing, a hot loop costs quota.
MAX_BACKOFF_SECONDS=900

launch=0
fast_fails=0
while [ ! -f "$RUN_DIR/STOP" ]; do
  launch=$((launch + 1))
  echo "=== launch #$launch ($PROVIDER) $(date -u +%H:%M:%S)Z ===" >> "$LOG"
  started_at=$(date +%s)

  if [ "$PROVIDER" = "claude" ]; then
    # Run from the isolated run dir, NOT the repo: the repo's CLAUDE.md is a
    # Python engineering contract and makes the model think it was handed a
    # coding task instead of a game to play.
    ( cd "$RUN_DIR" && claude --print --model claude-haiku-4-5 \
        --allowedTools "$TOOLS" < "$PROMPT" ) >> "$LOG" 2>&1
  else
    # NO --sandbox read-only: it blocks outbound network, so every HTTP MCP
    # server fails on each call, reported only as "user cancelled MCP tool
    # call". Run from the isolated run dir (not the repo) so codex does not read
    # AGENTS.md and mistake a game for a coding task.
    ( cd "$RUN_DIR" && \
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
  if [ "$rc" -ne 0 ] && [ "$elapsed" -lt "$FAST_FAIL_SECONDS" ]; then
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
