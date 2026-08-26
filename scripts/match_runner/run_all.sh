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
    # WATCH THE WORK, NOT THE SYMPTOMS.
    #
    # Every previous guard asked "does this look broken?" and each was outrun by
    # a failure that looked different. Grepping for "quota" missed "session
    # limit". Checking for a non-zero exit missed a sign-in failure that exited
    # rc=0. Whatever comes next will look different again.
    #
    # So ask the one question that does not depend on the failure: HAS ANY MOVE
    # BEEN MADE? The server records every move and the count is already in the
    # reply we poll for the self-stop, so this costs nothing extra. A live match
    # whose move count has not risen in STALL_POLLS minutes is broken, and it no
    # longer matters why.
    #
    # And it STOPS rather than backing off. Backing off still burns a session
    # every few minutes forever, and no amount of waiting fixes a dead sign-in —
    # that needs a person. In M_7360 this would have fired at 18:59 instead of a
    # human noticing at 20:52: ~100 wasted sessions instead of 2,584, with
    # rounds 3 and 4 still usable.
    STALL_POLLS=3
    prev_moves=-1
    stalled=0
    while [ ! -f "$RUN/STOP" ]; do
      body=$(curl -s --max-time 20 \
        "https://agentludum.com/api/spectator/matches/$MATCH_ID/state" 2>/dev/null)
      state=$(printf '%s' "$body" | sed -n 's/.*"state":"\([a-z]*\)".*/\1/p')
      case "$state" in
        completed|cancelled)
          echo "match $MATCH_ID is $state — stopping the loops"
          touch "$RUN/STOP"
          break;;
      esac

      # Total moves recorded so far. Counting "action" keys in the history is
      # deliberately crude: it needs no auth, no jq, and no parsing that a
      # payload change could quietly break into a constant zero.
      moves=$(printf '%s' "$body" | grep -o '"action"' | wc -l | tr -d ' ')

      # STORM ALARM — count TURNS ACTUALLY PLAYED, per agent.
      #
      # Three signals were measured against M_7360 before settling on this one.
      # Two of them cannot work:
      #
      #   Launches per minute. The storm ran at 4.5/min and healthy ran at 1.4
      #   peaking at 5 — the distributions OVERLAP, so no threshold separates
      #   them. The storm is capped by session length, not by frenzy.
      #
      #   Moves in the public feed. A defaulted turn is still recorded as a
      #   move, with a talk message on it 100% of the time. So an agent that has
      #   stopped playing looks identical to one that is playing.
      #
      # What does separate, cleanly: a successful `submit_action` in Claude
      # Code's own MCP log. Over the same windows that gave 4.5-vs-1.4 and no
      # separation at all, this gave 8 and 94 in the clean minutes against 0, 0
      # and 0 through the storm. It is also the honest question — "did this
      # agent play a turn" — rather than a proxy for it.
      #
      # It stops the WHOLE run, not the one agent. A silent agent is defaulted
      # every turn and a default scores as a HOARD, so the moment one stops
      # playing these numbers are spoilt; finishing only spends tokens on a
      # match nobody can use.
      if [ "$state" = "active" ]; then
        played_any=0
        for lg in "$RUN"/agent_*.log; do
          [ -e "$lg" ] || continue
          aid=$(basename "$lg" .log | sed 's/^agent_//')
          # Claude Code files MCP logs under the escaped REAL path of the working
          # directory, so resolve it — /tmp is a symlink to /private/tmp on macOS
          # and the unresolved form finds nothing.
          real=$(cd "$RUN/a/$aid" 2>/dev/null && pwd -P)
          [ -n "$real" ] || continue
          mcpdir="$HOME/Library/Caches/claude-cli-nodejs/$(printf '%s' "$real" | tr '/' '-')/mcp-logs-agentludum"
          n=$(grep -rh 'submit_action' "$mcpdir" 2>/dev/null | grep -c 'completed successfully')
          prev_n=$(cat "$RUN/.played_$aid" 2>/dev/null || echo -1)
          echo "$n" > "$RUN/.played_$aid"
          [ "$n" != "$prev_n" ] && played_any=1
          stuck_f="$RUN/.stuck_$aid"
          if [ "$n" = "$prev_n" ]; then
            st=$(cat "$stuck_f" 2>/dev/null || echo 0)
            st=$(( st + 1 ))
            echo "$st" > "$stuck_f"
            if [ "$st" -ge 3 ]; then
              echo "STORM: agent $aid has played no turn for 3 minutes while the match is live."
              echo "  It has launched $(grep -c 'launch #' "$lg") sessions in total and is"
              echo "  getting nowhere. Read the newest lines of $lg — a session that ends"
              echo "  in seconds did not play, whatever it exited with."
              echo "  Stopping the whole run: a silent agent defaults every turn, a default"
              echo "  scores as a HOARD, so these numbers are already spoilt."
              touch "$RUN/STOP"
              break
            fi
          else
            echo 0 > "$stuck_f"
          fi
        done
        [ -f "$RUN/STOP" ] && break
      fi

      if [ "$state" = "active" ] && [ "$moves" = "$prev_moves" ]; then
        stalled=$(( stalled + 1 ))
        if [ "$stalled" -ge "$STALL_POLLS" ]; then
          echo "STALLED: $MATCH_ID is active but no move has been recorded in ${STALL_POLLS} minutes."
          echo "  The agents are not playing. Check the newest agent_*.log for why —"
          echo "  a sign-in failure exits cleanly and looks like success."
          echo "  Stopping the loops so they do not burn sessions against it."
          touch "$RUN/STOP"
          break
        fi
      else
        stalled=0
      fi
      prev_moves="$moves"
      sleep 60
    done
  ) &
fi

wait

# EFFICIENCY REPORT — printed every run, whether or not an alarm fired.
#
# The alarms above catch a storm while it happens. This catches the slow bleed
# they are tuned to miss, and it is the layer that would have made this visible
# months ago: the waste was always in the logs, nothing ever put a number on it.
# Launches per move is the whole story. Near 0.05 is healthy; 10.9 was M_7360.
echo ""
echo "=== efficiency: launches per turn PLAYED (healthy ~0.05, a storm is >1) ==="
total_l=0
for lg in "$RUN"/agent_*.log; do
  [ -e "$lg" ] || continue
  aid=$(basename "$lg" .log | sed 's/^agent_//')
  l=$(grep -c 'launch #' "$lg" 2>/dev/null)
  m=$(cat "$RUN/.played_$aid" 2>/dev/null || echo 0)
  total_l=$(( total_l + l ))
  if [ "$m" -gt 0 ] 2>/dev/null; then
    printf '  agent %-6s %5s launches  %4s turns  %s per turn\n' \
      "$aid" "$l" "$m" "$(awk -v a="$l" -v b="$m" 'BEGIN{printf "%.2f", a/b}')"
  else
    printf '  agent %-6s %5s launches  %4s turns  (PLAYED NOTHING)\n' "$aid" "$l" "$m"
  fi
done
echo "  total launches: $total_l"
