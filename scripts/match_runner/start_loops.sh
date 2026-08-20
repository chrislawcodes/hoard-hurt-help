#!/bin/bash
# Start one supervised play loop per agent for a match.
#
# Reads roster.txt in the run dir: one "agent_id<TAB>name<TAB>provider" per line
# (provider is "claude" or "codex"). Each agent gets its own supervisor, because
# claude --print and codex exec both exit when the model thinks it is finished.
#
# usage: start_loops.sh <run_dir>

set -uo pipefail

RUN_DIR="${1:?usage: start_loops.sh <run_dir>}"
ROSTER="$RUN_DIR/roster.txt"

[ -f "$ROSTER" ] || { echo "no roster at $ROSTER" >&2; exit 1; }

# A leftover STOP from a previous match would kill every loop instantly.
rm -f "$RUN_DIR/STOP"

started=0
while IFS=$'\t' read -r agent_id name provider; do
  case "$agent_id" in ''|\#*) continue ;; esac
  nohup "$RUN_DIR/play_agent.sh" "$agent_id" "$name" "$provider" "$RUN_DIR" \
    >> "$RUN_DIR/supervisor_${agent_id}.log" 2>&1 &
  started=$((started + 1))
  echo "started agent $agent_id ($name) on $provider  pid=$!"
done < "$ROSTER"

echo "--- $started loops running; stop them with: touch $RUN_DIR/STOP ---"
