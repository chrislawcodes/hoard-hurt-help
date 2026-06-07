---
reviewer: "gemini"
lens: "requirements-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/admin-role-split/spec.md"
artifact_sha256: "136e6379b3f09ff0172602642c4bc5ce7628dd87774a9728748274fa9376134f"
repo_root: "."
git_head_sha: "e9c316bb0db5f017c9ae0dc55f2af8bcbe7f576a"
git_base_ref: "origin/claude/awesome-bohr-fBDnG"
git_base_sha: "e9c316bb0db5f017c9ae0dc55f2af8bcbe7f576a"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "[UNVERIFIED](slug normalization) — out of scope. (path param injection) — 403, not a security risk. (non-path auth) — accepted. (ADMIN_EMAILS lifecycle) — single-instance."
raw_output_path: "docs/workflow/feature-runs/admin-role-split/reviews/spec.gemini.requirements-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec requirements-adversarial

## Findings

1.  **[UNVERIFIED] Ambiguous Game Slug Normalization:** The spec states `game_admin_emails_for` normalizes slugs via `upper().replace("-", "_")`. It assumes user-provided game slugs in URL paths or database columns are consistent with this format. If a game has a slug like `HoardHurtHelp` (camel case) or `hoard.hurt.help` (dotted), the lookup will fail silently (resulting in an empty set, not a hard error), potentially causing unexpected 403s.
2.  **Path Parameter Injection Risk:** The `require_game_admin` dependency uses `game: str = Path(...)`. If this dependency is used in routes that don't *explicitly* validate the `{game}` path parameter against a whitelist or database, it could theoretically accept arbitrary strings (e.g., `require_game_admin(game="malicious-slug")`). While this causes a 403 (because lookup returns empty), it allows unauthorized users to probe the admin configuration.
3.  **Inconsistent Auth Pattern for Non-Path Routes:** The spec notes: "For routes that do NOT have a {game} path param... lookup match.game in the handler before calling a separate verify_game_admin() helper." This introduces a bifurcated authorization pattern. Relying on handlers to manually call a helper is a high-risk manual step compared to the declarative `Depends()` pattern, increasing the likelihood of developer error in future routes.
4.  **Implicit Dependency on `ADMIN_EMAILS` Lifecycle:** The compatibility window assumes a specific deployment sequence. If `ADMIN_EMAILS` is removed from the configuration, but an old instance is still running (or cached), the system will fail silently (403) rather than providing a clear "config missing" error.

## Residual Risks

*   **Authentication Lockout:** The reliance on falling back to `ADMIN_EMAILS` during the compatibility window creates a "hidden state" where administrators might have access they are unaware of, complicating auditing and debugging during the migration.
*   **Routing Collision:** Despite the prefix separation, if a new game is introduced with a slug that conflicts with existing top-level routes (e.g., a game slugged `admin` or `api`), the routing hierarchy could break unexpectedly. The spec lacks a validation step to ensure game slugs cannot collide with reserved system prefixes.
*   **Fragile Template Migration:** Moving templates is a manual process. The spec lacks a strategy to prevent broken references or "orphaned" templates in the old `templates/admin/` directory that might still be accidentally rendered by legacy code.

## Token Stats

- total_input=14541
- total_output=562
- total_tokens=15103
- `gemini-3.1-flash-lite`: input=14541, output=562, total=15103

## Resolution
- status: accepted
- note: [UNVERIFIED](slug normalization) — out of scope. (path param injection) — 403, not a security risk. (non-path auth) — accepted. (ADMIN_EMAILS lifecycle) — single-instance.
