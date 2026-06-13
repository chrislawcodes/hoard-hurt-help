---
reviewer: "gemini"
lens: "requirements-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/mcp-oauth/spec.md"
artifact_sha256: "4f2b7a50cab09b51633ed703355abe532fe2feb615b812aef277577000f33adc"
repo_root: "."
git_head_sha: "cfba823dfdbcc3dac260c611e20110c267590dee"
git_base_ref: "origin/main"
git_base_sha: "cfba823dfdbcc3dac260c611e20110c267590dee"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "Same three plan-stage decisions as Codex, plus routing: (1) FR-012/R4 credential trade-offs = open design point 1, plan chooses + states verification; (2) /.well-known + /authorize/token mount correctness = open design point 4 (plan specifies how OAuth/metadata routes mount relative to /mcp and the Railway TLS proxy without colliding with existing API routes); (3) get_game_state public-tool gating = open design point 7. Residual risks map to R1 (four-client), the cost/long-poll note (verify long-poll survives the fastmcp migration — fold into plan verification), and FR-006 (DB unique constraint + lock). No spec edit needed; resolved and verified at the plan stage."
raw_output_path: "docs/workflow/feature-runs/mcp-oauth/reviews/spec.gemini.requirements-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec requirements-adversarial

## Findings

1.  **Credential Management Ambiguity (FR-012/R4):** The artifact identifies a conflict between storing only key hashes (current security posture) and the requirement for a credential mechanism for the internal loopback. Using symmetric encryption (Option A) introduces a new persistent secret (the app secret) that, if leaked, compromises all OAuth sessions. Minting short-lived internal tokens (Option B) avoids persistent storage but requires a trusted, internal-only communication channel that isn't fully detailed in the artifact.
    *   **Severity:** HIGH
    *   **Status:** [UNVERIFIED] — The proposed credential mechanisms are high-risk; the plan lacks an exhaustive analysis of the trade-offs regarding secret storage vs. operational complexity.

2.  **Spec Compliance Risk (R2/FR-001/FR-002):** The requirement to host discovery and metadata at `/.well-known/` and `/authorize`/`/token` endpoints requires careful routing configuration. Given the existing app structure, there is a risk that these routes might be intercepted or incorrectly processed by the current middleware/router setup (e.g., `app/main.py`), potentially exposing sensitive metadata or failing the spec discovery handshake.
    *   **Severity:** HIGH
    *   **Status:** [UNVERIFIED] — The artifact does not specify how these endpoints will be mounted to ensure they are served correctly as a spec-compliant AS/Resource server without conflicting with existing API routes.

3.  **Public Tool Exposure under OAuth Gate (Open Design Point 7):** The artifact acknowledges that the `GoogleProvider`/`OAuthProxy` will likely gate the entire `/mcp` mount, which would inadvertently lock the public `get_game_state` tool. The suggested solution (a "public carve-out") creates a split-auth model, which is a notorious source of security vulnerabilities if the routing logic is misconfigured or if new tools are added without explicit, positive security declarations.
    *   **Severity:** MEDIUM
    *   **Status:** [CODE-CONFIRMED] — `app/main.py` shows `/mcp` is mounted as a single ASGI app, confirming that gating the app will indeed hide all tools within it.

## Residual Risks

1.  **Client-Side Incompatibility (R1):** The requirement for all four clients (Claude Code, Claude Desktop, Codex, Gemini CLI) to gate shipping introduces a significant project risk. If one client fails to implement RFC 9728/spec-compliant OAuth, the entire feature may be delayed or forced to have an unsatisfactory fallback that breaks the goal of "no pasted keys."
2.  **Increased Token Usage for Idle Games:** While `get_next_turn` uses long-polling, the artifact acknowledges that the OAuth server implementation may inherently increase token overhead compared to the raw `sk_conn_` connection method. If long-polling is not perfectly implemented in the new OAuth-enabled FastMCP environment, costs could exceed current levels, hurting the "cheaper option" value proposition.
3.  **Authentication/Authorization Race Condition (FR-006):** The guarantee that only one canonical 'Mode A' connection exists per user under concurrent OAuth callbacks is difficult to enforce purely at the application level. Without a strict database-level unique constraint or a robust transactional locking mechanism, race conditions could lead to orphaned connections or credential corruption.

## Token Stats

- total_input=46789
- total_output=858
- total_tokens=93943
- `gemini-3.1-flash-lite`: input=46789, output=858, total=93943

## Resolution
- status: accepted
- note: Same three plan-stage decisions as Codex, plus routing: (1) FR-012/R4 credential trade-offs = open design point 1, plan chooses + states verification; (2) /.well-known + /authorize/token mount correctness = open design point 4 (plan specifies how OAuth/metadata routes mount relative to /mcp and the Railway TLS proxy without colliding with existing API routes); (3) get_game_state public-tool gating = open design point 7. Residual risks map to R1 (four-client), the cost/long-poll note (verify long-poll survives the fastmcp migration — fold into plan verification), and FR-006 (DB unique constraint + lock). No spec edit needed; resolved and verified at the plan stage.
