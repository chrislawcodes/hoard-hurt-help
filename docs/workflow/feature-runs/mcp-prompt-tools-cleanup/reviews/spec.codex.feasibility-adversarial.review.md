---
reviewer: "codex"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/mcp-prompt-tools-cleanup/spec.md"
artifact_sha256: "443a7e7b08e366b4239ce3b059dc7e2c7666fff1aab42c860c713864bb12d5d0"
repo_root: "."
git_head_sha: "3e2e306b973c079e940e7bf316224db8faa2037a"
git_base_ref: "origin/main"
git_base_sha: "3e2e306b973c079e940e7bf316224db8faa2037a"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "HIGH (no-active-game): IMPLEMENTATION CONTRACT — with no active game/agent, get_instructions returns a short 'no active game yet; start one, then call get_instructions again for the game's rules + your strategy' message and OMITS game-specific rules/how-to-answer (no game selected to source them); matches the kickoff no_game handling. MEDIUM (one agent, multiple live matches, match_id omitted): default to the agent's MOST-URGENT match (same ordering as get_next_turn); rules are identical across that agent's matches (same game), only identity/current differ."
raw_output_path: "docs/workflow/feature-runs/mcp-prompt-tools-cleanup/reviews/spec.codex.feasibility-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

1. **High**: `get_instructions` is underspecified for the valid “no active game” state, which the code already supports. The next-turn path explicitly returns `no_game` when there are no claimable matches for a connection ([`app/engine/agent_play_next_turn.py`](file:///Users/chrislaw/hoard-hurt-help--feat-mcp-prompt-tools-cleanup/app/engine/agent_play_next_turn.py#L348) and [`app/engine/agent_play_next_turn.py`](file:///Users/chrislaw/hoard-hurt-help--feat-mcp-prompt-tools-cleanup/app/engine/agent_play_next_turn.py#L393)), and the fan-out logic is built to handle “several agents across several active matches” ([`app/engine/agent_play_next_turn.py`](file:///Users/chrislaw/hoard-hurt-help--feat-mcp-prompt-tools-cleanup/app/engine/agent_play_next_turn.py#L3)). The spec still requires `get_instructions` to emit game-specific `## The rules` / `## How to answer` sections even when there is no active game, but there is no selected match or game module to source those sections from. That makes the first-connect / idle path impossible without guessing. [CODE-CONFIRMED]

2. **Medium**: The agent-selection rule misses the supported case where one active agent has multiple live matches. The current routing code scans all active matches for a given `agent_id` ([`app/engine/agent_play_next_turn.py`](file:///Users/chrislaw/hoard-hurt-help--feat-mcp-prompt-tools-cleanup/app/engine/agent_play_next_turn.py#L100) and [`app/engine/agent_play_next_turn.py`](file:///Users/chrislaw/hoard-hurt-help--feat-mcp-prompt-tools-cleanup/app/engine/agent_play_next_turn.py#L186)), and `get_next_turn(agent_id=...)` is explicitly meant to work across those matches ([`mcp_server/server.py`](file:///Users/chrislaw/hoard-hurt-help--feat-mcp-prompt-tools-cleanup/mcp_server/server.py#L527)). The spec says `match_id` disambiguates “if that agent is in more than one match,” but it never says what `get_instructions(agent_id=...)` should do when `match_id` is omitted and the agent has multiple live matches. That leaves the tool free to pick the wrong match or fail unpredictably. [CODE-CONFIRMED]

## Residual Risks

- The cleanup of `get_turn_detail`, `get_opponent_history`, and `get_standings` is only partly verifiable from the provided code. `history` is definitely public action history, but the implementations of those removed tools were not included, so it is still possible one of them exposes a distinct projection the spec would unintentionally drop. [UNVERIFIED]
- The spec assumes the new MCP instruction text can be kept game-agnostic while still staying in sync with both built-in game modules and any future game modules. The provided code only shows HHH and Liar’s Dice, so other modules would need the same new seam even though they are not visible here. [UNVERIFIED]

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: HIGH (no-active-game): IMPLEMENTATION CONTRACT — with no active game/agent, get_instructions returns a short 'no active game yet; start one, then call get_instructions again for the game's rules + your strategy' message and OMITS game-specific rules/how-to-answer (no game selected to source them); matches the kickoff no_game handling. MEDIUM (one agent, multiple live matches, match_id omitted): default to the agent's MOST-URGENT match (same ordering as get_next_turn); rules are identical across that agent's matches (same game), only identity/current differ.
