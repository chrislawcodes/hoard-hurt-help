#!/bin/bash
# Point a run directory's Claude sessions at /mcp using a CONNECTION KEY
# instead of the Google sign-in.
#
# WHY A KEY, NOT THE GOOGLE SIGN-IN.
#
# The Google sign-in renews itself with a single-use ticket: spending a refresh
# token destroys it and issues a replacement, so a replayed one is worthless to
# a thief. That is good security and a disaster for a match runner. Eight agents
# are eight processes sharing one saved sign-in, and when two of them renew at
# the same moment the second is left holding a ticket the server has already
# deleted. It is told "already rotated, expired, or revoked ... forces the
# client to re-authenticate", and it is signed out for good.
#
# M_7360 died that way nine minutes in, while the token itself still had 85 days
# left on its clock. 2,584 sessions burned against a dead credential and six
# turns defaulted — and a defaulted move scores as a HOARD, so the damage lands
# in exactly the number these runs measure.
#
# A connection key never renews and never rotates on its own (key_auth.py:
# "the key is the credential and stays valid until the owner reissues it"), so
# there is nothing to race over and sharing one across eight agents is safe.
# Separate per-agent sign-ins were tried first and do NOT work on macOS: config
# directories separate which servers are configured, but credentials live in the
# system keychain, so eight logins overwrite one entry.
#
# usage: setup_mcp_key.sh <run_dir>
# key:   $AGENTLUDUM_MCP_KEY, else ~/.agentludum/mcp-key (chmod 600)
#
# Get a key from /me/connections/<id> → "Allow key sign-in on MCP" → Rotate Key.
# It is shown once.

set -uo pipefail

RUN_DIR="${1:?usage: setup_mcp_key.sh <run_dir>}"
MCP_URL="${AGENTLUDUM_MCP_URL:-https://agentludum.com/mcp}"
KEY_FILE="${AGENTLUDUM_MCP_KEY_FILE:-$HOME/.agentludum/mcp-key}"

KEY="${AGENTLUDUM_MCP_KEY:-}"
if [ -z "$KEY" ] && [ -f "$KEY_FILE" ]; then
  KEY=$(tr -d '\n\r' < "$KEY_FILE")
fi
case "$KEY" in
  sk_conn_*) ;;
  *) echo "no connection key: set AGENTLUDUM_MCP_KEY or write one to $KEY_FILE" >&2; exit 1;;
esac

# Reject the key BEFORE a match rather than after, when an ACTIVE match cannot
# be stopped and every failed turn defaults to a HOARD.
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 -X POST "$MCP_URL" \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"match-runner","version":"1"}}}')
if [ "$code" != "200" ]; then
  echo "key rejected by $MCP_URL (HTTP $code)" >&2
  echo "  Check 'Allow key sign-in on MCP' is ON for this connection, and that" >&2
  echo "  the key has not been rotated since it was saved." >&2
  exit 1
fi

# ONE WORKING DIRECTORY PER AGENT — and it is the monitoring that needs it.
#
# Claude Code files its MCP logs by working directory. Agents sharing one
# directory share one log, so there is no way to tell which agent played a turn
# and which has been failing for an hour. A directory each gives each agent its
# own log, which is the only local signal that separates a healthy agent from a
# storming one (see run_all.sh).
#
# `claude mcp add` also scopes a server to the current directory, so each of
# these needs the key registered in it — running the command anywhere else files
# the key under that other path and the agent never sees it.
while IFS=$'\t' read -r agent_id agent_name provider <&3; do
  [ -z "${agent_id:-}" ] && continue
  case "$agent_id" in \#*) continue ;; esac
  wd="$RUN_DIR/a/$agent_id"
  mkdir -p "$wd"
  ( cd "$wd" && claude mcp remove agentludum ) >/dev/null 2>&1
  if ! ( cd "$wd" && claude mcp add --transport http agentludum "$MCP_URL" \
         --header "Authorization: Bearer $KEY" ) >/dev/null 2>&1; then
    echo "claude mcp add failed for agent $agent_id" >&2
    exit 1
  fi
done 3< "$RUN_DIR/roster.txt"

first_wd="$RUN_DIR/a/$(awk -F'\t' 'NF{print $1; exit}' "$RUN_DIR/roster.txt")"
if ( cd "$first_wd" && claude mcp list ) 2>/dev/null | grep -q "agentludum.*Connected"; then
  echo "run dir signed in with a connection key — no token to expire or race"
else
  echo "configured, but 'claude mcp list' does not report Connected" >&2
  exit 1
fi
