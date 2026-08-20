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

launch=0
while [ ! -f "$RUN_DIR/STOP" ]; do
  launch=$((launch + 1))
  echo "=== launch #$launch ($PROVIDER) $(date -u +%H:%M:%S)Z ===" >> "$LOG"

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
  echo "--- exited rc=$rc $(date -u +%H:%M:%S)Z ---" >> "$LOG"

  [ -f "$RUN_DIR/STOP" ] && break

  # A subscription quota wall is not a crash — retrying every few seconds just
  # burns relaunches, so wait it out instead.
  if tail -40 "$LOG" | grep -qiE "quota reached|rate limit|usage limit|too many requests"; then
    echo "!!! quota/rate wall hit — backing off 300s" >> "$LOG"
    sleep 300
  else
    sleep 3
  fi
done

echo "=== supervisor stopped after $launch launches $(date -u +%H:%M:%S)Z ===" >> "$LOG"
