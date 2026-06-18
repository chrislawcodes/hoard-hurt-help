---
reviewer: "gemini"
lens: "requirements-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/mcp-prompt-tools-cleanup/spec.md"
artifact_sha256: "443a7e7b08e366b4239ce3b059dc7e2c7666fff1aab42c860c713864bb12d5d0"
repo_root: "."
git_head_sha: "3e2e306b973c079e940e7bf316224db8faa2037a"
git_base_ref: "origin/main"
git_base_sha: "3e2e306b973c079e940e7bf316224db8faa2037a"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "HIGH (stripping re-bloat): addressed — the pinned MCP 'static' key-set test is the tripwire; any new heavy field fails the test and forces a conscious denylist decision. MEDIUM (instruction invalidation): rules are fixed per match; strategy changes only on a new agent version; the kickoff prompt instructs re-calling get_instructions, which covers it. LOW (4 hardcoded sections): accepted — adding a 5th section later is additive; a metadata schema is overkill now."
raw_output_path: "docs/workflow/feature-runs/mcp-prompt-tools-cleanup/reviews/spec.gemini.requirements-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec requirements-adversarial

## Findings

1.  **[HIGH] Payload Stripping Logic Coupling:** The specification requires stripping `static.rules`, `base_prompt`, and `your_strategy` from the `get_next_turn` payload in `mcp_server/server.py`. Because the shared payload builder `app/engine/agent_play_next_turn.py` (`_build_turn_payload`) remains the source of truth for both the connector and MCP routes, the stripping logic is fragile. Adding new heavy fields in the future requires simultaneous, manual maintenance in the `mcp_server/server.py` wrapper, risking "re-bloat" despite the regression test plan [CODE-CONFIRMED].
2.  **[MEDIUM] Instruction Invalidation Dependency:** The design assumes the AI client will correctly cache the output of `get_instructions` and re-fetch it only when "it loses the rules." There is no explicit mechanism for invalidating this information if the game rules or agent strategy change during a match. The robustness of this design relies entirely on the AI client's ability to interpret and follow the kickoff prompt correctly [CODE-CONFIRMED].
3.  **[LOW] Hardcoded Instruction Sections:** The design specifies that `get_instructions` returns four hardcoded, labeled sections. This structural rigidity complicates future schema evolutions, such as adding a fifth section for "Game-specific warnings" or "Dynamic context," requiring changes across all `GameModule` implementations rather than relying on a more flexible metadata structure [UNVERIFIED].

## Residual Risks

1.  **Context Bloat via Client-Side Mismanagement:** If the AI client fails to cache the `get_instructions` output properly, it will re-invoke this tool every turn, needlessly increasing latency and token usage, potentially degrading the performance of the stateless MCP endpoint.
2.  **Disconnected Tool Evolution:** By removing tools (`get_standings`, `get_turn_detail`, etc.) and moving their information into the `get_next_turn` payload, the platform loses the ability to granularly track or throttle specific types of data requests independently of turn-based actions. If a client needs only standings, it *must* pull the full `get_next_turn` payload, which may increase server load.

## Token Stats

- total_input=41130
- total_output=479
- total_tokens=43768
- `gemini-3.1-flash-lite`: input=41130, output=479, total=43768

## Resolution
- status: accepted
- note: HIGH (stripping re-bloat): addressed — the pinned MCP 'static' key-set test is the tripwire; any new heavy field fails the test and forces a conscious denylist decision. MEDIUM (instruction invalidation): rules are fixed per match; strategy changes only on a new agent version; the kickoff prompt instructs re-calling get_instructions, which covers it. LOW (4 hardcoded sections): accepted — adding a 5th section later is additive; a metadata schema is overkill now.
