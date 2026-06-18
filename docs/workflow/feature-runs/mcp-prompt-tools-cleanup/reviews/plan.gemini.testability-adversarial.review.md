---
reviewer: "gemini"
lens: "testability-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/mcp-prompt-tools-cleanup/plan.md"
artifact_sha256: "c69157525b4c132369d07d91ddae17d0c5200f218a42b41279f30431657d0cfc"
repo_root: "."
git_head_sha: "3e2e306b973c079e940e7bf316224db8faa2037a"
git_base_ref: "origin/main"
git_base_sha: "3e2e306b973c079e940e7bf316224db8faa2037a"
generation_method: "gemini-cli"
resolution_status: "open"
resolution_note: ""
raw_output_path: "docs/workflow/feature-runs/mcp-prompt-tools-cleanup/reviews/plan.gemini.testability-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan testability-adversarial

## Findings

1.  **[CODE-CONFIRMED]** The plan assumes the `GameModule` interface can be extended with `semantic_rules_text` without modifying all existing game modules. However, `app/games/base.py` defines the `GameModule` protocol. Adding a mandatory method requires updating all implementing classes (`HoardHurtHelpModule`, `LiarsDiceModule`) to avoid runtime crashes or type errors, or adding a default implementation in `BaseGameModule` (which might still require runtime awareness/overrides if the default `NotImplementedError` is deemed insufficient or too risky to break existing game modules silently). The plan mentions "base default" but the "fail loud" philosophy for the platform necessitates careful handling of this new contract hook.
2.  **[CODE-CONFIRMED]** The plan relies on `agent_identity_for` being able to resolve agent strategies and identities from "active (non-archived) agents" independently of "claimable open turns". While `agent_play_next_turn.py` contains the logic, it heavily depends on `_collect_candidates` (which filters by turn-eligibility). Extracting this logic safely requires a decoupled query that respects the same `AgentVersion` and `seat_name` derivations without duplicating the filtering logic improperly. The plan acknowledges the complexity but risks subtle logic divergence if the extraction is not meticulous.

## Residual Risks

1.  **Stale `_PLAY_PROMPT` usage:** The reliance on `_PLAY_PROMPT` in `app/routes/connections_connect_guide.py` (kickoff prompt) being synchronized with `docs/setup-mcp.md` is a recurring tension point. If the implementation of `get_instructions` deviates from the documentation, the "paste-once" prompt will instruct agents to use tools incorrectly.
2.  **Payload Stripping Fragility:** The plan uses `dict.pop(k, None)` in `mcp_server/server.py` to strip payload keys. If the shared `_build_turn_payload` changes the structure (e.g., nesting these keys differently) without explicit coordination, the MCP payload will either leak the undesired `static` content (if the keys move) or fail silently (if the pop fails to find a key that is now nested). The risk is mitigated by the proposed regression tests.
3.  **MCP Tool Surface Drift:** Cutting the 4 tools (`get_turn`, `get_standings`, `get_turn_detail`, `get_opponent_history`) is a significant breaking change for any existing MCP client session that relied on these specific tool signatures, even if not fully officially exposed in the current prompt workflow. If any secondary client tool relies on these, those integrations will break.

## Token Stats

- total_input=31770
- total_output=588
- total_tokens=32358
- `gemini-3.1-flash-lite`: input=31770, output=588, total=32358

## Resolution
- status: open
- note: