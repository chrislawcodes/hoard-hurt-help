#!/bin/bash
# Hold all eight supervisors in ONE long-lived foreground process, so the
# harness keeps them alive instead of reaping them when a Bash call returns.
RUN="${1:?usage: run_all.sh <run_dir>}"
rm -f "$RUN/STOP"
while IFS=$'\t' read -r id name prov; do
  case "$id" in ''|\#*) continue ;; esac
  "$RUN/play_agent.sh" "$id" "$name" "$prov" "$RUN" >> "$RUN/supervisor_${id}.log" 2>&1 &
  echo "started $id ($name)"
done < "$RUN/roster.txt"
echo "--- holding loops; STOP file ends them ---"

# SELF-STOP WHEN THE MATCH IS OVER.
#
# A supervisor has no idea a match ended: the agent gets "no_game", exits, and
# the supervisor relaunches. Each of those is a full session startup for nothing
# -- about 280 wasted sessions an hour across eight agents, running until a human
# remembers to touch STOP. That happened for real after M_6809.
#
# So if MATCH_ID is set, watch the public state and write STOP ourselves. Costs
# one unauthenticated curl a minute and removes the whole failure mode.
if [ -n "${MATCH_ID:-}" ]; then
  (
    while [ ! -f "$RUN/STOP" ]; do
      state=$(curl -s --max-time 20 \
        "https://agentludum.com/api/spectator/matches/$MATCH_ID/state" 2>/dev/null \
        | sed -n 's/.*"state":"\([a-z]*\)".*/\1/p')
      case "$state" in
        completed|cancelled)
          echo "match $MATCH_ID is $state — stopping the loops"
          touch "$RUN/STOP"
          break;;
      esac
      sleep 60
    done
  ) &
fi

wait
