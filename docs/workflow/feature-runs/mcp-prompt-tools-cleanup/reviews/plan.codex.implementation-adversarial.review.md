---
reviewer: "codex"
lens: "implementation-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/mcp-prompt-tools-cleanup/plan.md"
artifact_sha256: "c69157525b4c132369d07d91ddae17d0c5200f218a42b41279f30431657d0cfc"
repo_root: "."
git_head_sha: "3e2e306b973c079e940e7bf316224db8faa2037a"
git_base_ref: "origin/main"
git_base_sha: "3e2e306b973c079e940e7bf316224db8faa2037a"
generation_method: "codex-runner"
resolution_status: "open"
resolution_note: ""
raw_output_path: "docs/workflow/feature-runs/mcp-prompt-tools-cleanup/reviews/plan.codex.implementation-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan implementation-adversarial

## Findings

- **HIGH: The proposed `How to answer` text is wrong for Liar's Dice and the MCP surface cannot support it as written.** The plan tells the model to use `submit_action(action, target_id, ...)`, but the actual MCP tool only exposes `action`, `target_id`, and `message` ([`mcp_server/server.py`](/Users/chrislaw/hoard-hurt-help--feat-mcp-prompt-tools-cleanup/mcp_server/server.py#L592-L625)). Liar's Dice's own prompt and validator expect bid/challenge moves with bid details (`quantity`, `face`) ([`app/games/liars_dice/game.py`](/Users/chrislaw/hoard-hurt-help--feat-mcp-prompt-tools-cleanup/app/games/liars_dice/game.py#L243-L271), [`app/games/liars_dice/game.py`](/Users/chrislaw/hoard-hurt-help--feat-mcp-prompt-tools-cleanup/app/games/liars_dice/game.py#L364-L425)). As written, the instructions would be false for one of the supported games. `[CODE-CONFIRMED]`
- **MEDIUM: The no-selector / multiple-agent branch is unsafe across mixed-game accounts.** The current turn fan-out already spans all active AI agents in all active matches for a connection ([`app/engine/agent_play_next_turn.py`](/Users/chrislaw/hoard-hurt-help--feat-mcp-prompt-tools-cleanup/app/engine/agent_play_next_turn.py#L100-L170), [`app/engine/agent_play_next_turn.py`](/Users/chrislaw/hoard-hurt-help--feat-mcp-prompt-tools-cleanup/app/engine/agent_play_next_turn.py#L543-L557)). A single generic rules block plus a list of agent ids is ambiguous if the user is running Hoard-Hurt-Help and Liar's Dice at the same time. That branch needs per-agent or per-game instructions, not one shared block. `[CODE-CONFIRMED]`
- **MEDIUM: The no-active-game branch drops the reusable instruction sections entirely.** The plan says to return only a short note when there is no active game, but the documented contract says `get_instructions` should still return rules + how-to-answer with the note. If implemented as written, a cold-start client can be told to fetch instructions first and then receive a response with no usable instructions. `[UNVERIFIED]`

## Residual Risks

- `agent_identity_for` still needs a regression for the “same agent, multiple active matches” case, or it may pick the wrong match when several are open.
- The MCP strip helper should be verified on the serialized payload, not just key presence, or future nested re-bloat can slip back in.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: open
- note: 