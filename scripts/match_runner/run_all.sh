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
echo "--- holding 8 loops; STOP file ends them ---"
wait
