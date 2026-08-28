#!/bin/bash
# Hold all eight supervisors in ONE long-lived foreground process, so the
# harness keeps them alive instead of reaping them when a Bash call returns.
RUN="${1:?usage: run_all.sh <run_dir>}"
rm -f "$RUN/STOP"

# WHICH MODEL EACH SEAT PLAYS COMES FROM THE SERVER, NOT FROM HERE.
#
# The server already answers this — `resolve_seat_model` picks the agent's
# configured model, or its provider's default — and puts the answer in every
# turn payload. The always-on connector reads it from there. This runner used to
# ignore all of that and launch every agent on one CLAUDE_MODEL, so a match
# whose agents were set to Sonnet and Opus on the site could really have run
# entirely on Haiku, and the export would still have said Sonnet and Opus.
# Nothing recorded the difference, which made "does the model change who wins?"
# unanswerable from the data.
#
# So: read each seat's model out of the match export (the same resolved answer)
# and launch that agent on it. To change what a match runs on, change the
# agent's model on the site. There is nothing to set here.
declare -A SEAT_MODEL=()
if [ -n "${MATCH_ID:-}" ] && [ -s "$HOME/.agentludum/mcp-key" ]; then
  export_json=$(curl -fsS -H "X-Connection-Key: $(cat "$HOME/.agentludum/mcp-key")" \
      "https://agentludum.com/api/admin/matches/$MATCH_ID/export.json" 2>/dev/null) || export_json=""
  if [ -n "$export_json" ]; then
    while IFS=$'\t' read -r aid amodel; do
      [ -n "$aid" ] && [ -n "$amodel" ] && SEAT_MODEL["$aid"]="$amodel"
    done < <(printf '%s' "$export_json" | "$(dirname "$0")/seat_models.py")
  fi
fi
if [ ${#SEAT_MODEL[@]} -eq 0 ]; then
  # Loud, not silent: launching the whole field on one default while the site
  # says otherwise is exactly the failure this block exists to stop.
  echo "WARNING: could not read seat models from the server."
  echo "         Every agent runs on ${CLAUDE_MODEL:-claude-haiku-4-5}, whatever the site says."
fi

while IFS=$'\t' read -r id name prov; do
  case "$id" in ''|\#*) continue ;; esac
  seat_model="${SEAT_MODEL[$id]:-${CLAUDE_MODEL:-claude-haiku-4-5}}"
  CLAUDE_MODEL="$seat_model" \
    "$RUN/play_agent.sh" "$id" "$name" "$prov" "$RUN" >> "$RUN/supervisor_${id}.log" 2>&1 &
  echo "started $id ($name) on $seat_model"
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

      # STORM ALARM — measured against THE MATCH'S OWN PACE, not the clock.
      #
      # This used to stop the run when an agent had played nothing for three
      # minutes. That threshold came from Sonnet matches, where a turn takes
      # about 57 seconds. M_7558 put one Opus agent in the field and turns
      # stretched to 450 seconds, because every turn waits for the slowest
      # player — so a perfectly healthy agent looked stuck, the alarm killed a
      # live match, and 120 of 280 moves were defaulted into HOARDs.
      #
      # A wall-clock threshold asks "has N seconds passed", which is not the
      # question. The question is "is this agent falling behind the others", and
      # that is answerable without knowing how fast a turn should be: if the
      # MATCH advanced and one agent did not, that agent missed a turn.
      #
      # Turns missed in a row, not minutes elapsed. Slow is fine; behind is not.
      MISSED_TURNS_BEFORE_STOP=3
      if [ "$state" = "active" ] && [ "$moves" != "$prev_moves" ]; then
        # The match moved. Anyone who did not move with it missed this window.
        for lg in "$RUN"/agent_*.log; do
          [ -e "$lg" ] || continue
          aid=$(basename "$lg" .log | sed 's/^agent_//')
          real=$(cd "$RUN/a/$aid" 2>/dev/null && pwd -P)
          [ -n "$real" ] || continue
          mcpdir="$HOME/Library/Caches/claude-cli-nodejs/$(printf '%s' "$real" | tr '/' '-')/mcp-logs-agentludum"
          n=$(grep -rh 'submit_action' "$mcpdir" 2>/dev/null | grep -c 'completed successfully')
          prev_n=$(cat "$RUN/.played_$aid" 2>/dev/null || echo -1)
          echo "$n" > "$RUN/.played_$aid"
          missed_f="$RUN/.missed_$aid"
          if [ "$n" = "$prev_n" ]; then
            m=$(cat "$missed_f" 2>/dev/null || echo 0)
            m=$(( m + 1 ))
            echo "$m" > "$missed_f"
            if [ "$m" -ge "$MISSED_TURNS_BEFORE_STOP" ]; then
              echo "STORM: agent $aid has missed $m turns the rest of the field played."
              echo "  It has launched $(grep -c 'launch #' "$lg") sessions and is getting"
              echo "  nowhere. Read the newest lines of $lg — a session that ends in"
              echo "  seconds did not play, whatever it exited with."
              echo "  Stopping the whole run: a silent agent defaults every turn, a default"
              echo "  scores as a HOARD, so these numbers are already spoilt."
              touch "$RUN/STOP"
              break
            fi
          else
            echo 0 > "$missed_f"
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

# FETCH THE EXPORT AND REPORT, WITHOUT A HUMAN.
#
# The export is the only source carrying `was_defaulted` and `thinking`, so it
# is the only thing that can say whether these numbers are trustworthy or why an
# agent chose what it chose. Until the admin export accepted a connection key it
# needed a signed-in browser, which meant every report was fed by hand and the
# pooled report only ever saw the matches somebody remembered to download.
#
# Saved into EXPORT_DIR so the pool grows on its own. A pooled figure computed
# from whatever a person happened to click is not a sample of the matches
# played, and it looks exactly like one.
if [ -n "${MATCH_ID:-}" ]; then
  KEY_FILE="${AGENTLUDUM_MCP_KEY_FILE:-$HOME/.agentludum/mcp-key}"
  KEY="${AGENTLUDUM_MCP_KEY:-}"
  [ -z "$KEY" ] && [ -f "$KEY_FILE" ] && KEY=$(tr -d '\n\r' < "$KEY_FILE")
  EXPORT_DIR="${AGENTLUDUM_EXPORT_DIR:-$HOME/.agentludum/exports}"
  REPORTS="$(cd "$(dirname "$0")" && pwd)"

  case "$KEY" in
    sk_conn_*)
      mkdir -p "$EXPORT_DIR"
      out="$EXPORT_DIR/$MATCH_ID.json"
      code=$(curl -sS -o "$out" -w '%{http_code}' --max-time 60 \
        -H "Authorization: Bearer $KEY" \
        "https://agentludum.com/api/admin/matches/$MATCH_ID/export.json")
      if [ "$code" = "200" ]; then
        echo ""
        python3 "$REPORTS/match_report.py" "$out"
        echo ""
        python3 "$REPORTS/preset_fidelity.py" "$EXPORT_DIR"/*.json
        echo ""
        python3 "$REPORTS/pooled_report.py" "$EXPORT_DIR"/*.json
      else
        # Loud, not silent. A missing export means the next pooled report is
        # quietly answering from a smaller pool than you think.
        rm -f "$out"
        echo "" >&2
        echo "could not fetch the export for $MATCH_ID (HTTP $code)." >&2
        echo "  The key must belong to a platform admin. Reports skipped;" >&2
        echo "  this match is NOT in the pool." >&2
      fi;;
    *)
      echo "" >&2
      echo "no connection key, so no export and no reports for $MATCH_ID." >&2;;
  esac
fi
