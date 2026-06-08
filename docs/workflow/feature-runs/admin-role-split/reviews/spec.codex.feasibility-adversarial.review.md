---
reviewer: "codex"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/admin-role-split/spec.md"
artifact_sha256: "136e6379b3f09ff0172602642c4bc5ce7628dd87774a9728748274fa9376134f"
repo_root: "."
git_head_sha: "e9c316bb0db5f017c9ae0dc55f2af8bcbe7f576a"
git_base_ref: "origin/claude/awesome-bohr-fBDnG"
git_base_sha: "e9c316bb0db5f017c9ae0dc55f2af8bcbe7f576a"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "MEDIUM(rolling deploy) — Railway single-instance, no mixed-version pods. MEDIUM(pydantic parsing) — plan will verify. MEDIUM(fails open) — compat window intent; removed after env vars confirmed."
raw_output_path: "docs/workflow/feature-runs/admin-role-split/reviews/spec.codex.feasibility-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

1. **Medium [UNVERIFIED] - The rollout story is not actually safe in a mixed deployment.** The spec says a rolling deploy is safe, but the compatibility logic is one-way: new code can read `ADMIN_EMAILS`, while old code cannot read `PLATFORM_ADMIN_EMAILS` or `GAME_ADMIN_EMAILS__...`. If old and new pods run together with the new env vars already live, they can make different auth decisions and either lock out admins or expose different routes depending on which pod handles the request. This needs a documented two-step rollout or symmetric compatibility.

2. **Medium [UNVERIFIED] - `GAME_ADMIN_EMAILS__...` parsing is underdefined and likely brittle.** The spec relies on pydantic-settings turning nested env vars into `dict[str, str]` entries and preserving comma-separated email lists, but it also says a custom validator may be needed. That is an implementation gamble, not a settled contract. If the installed version parses differently, the game-admin map can come through empty or malformed and the whole auth check fails.

3. **Medium - Missing per-game config fails open to the global admin list.** `game_admin_emails_for(game)` falls back to `ADMIN_EMAILS` whenever a game-specific key is missing. In a role-split design, that means a typo or omission in `GAME_ADMIN_EMAILS__HOARD_HURT_HELP` does not fail closed; it silently grants access based on the global list. That hides configuration mistakes and weakens the separation the spec is trying to create.

## Residual Risks

- The removal of old `/admin/matches` and `/admin/prompts` routes will break any bookmarks, scripts, or docs that still point at them unless redirects or aliases are added.
- The spec only checks strategy prompt leakage in the viewer HTML. Export endpoints, JSON APIs, and template fragments still need an explicit audit.
- The exact FastAPI dependency behavior and settings parsing behavior still need confirmation against the repo’s current library versions before implementation.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: MEDIUM(rolling deploy) — Railway single-instance, no mixed-version pods. MEDIUM(pydantic parsing) — plan will verify. MEDIUM(fails open) — compat window intent; removed after env vars confirmed.
