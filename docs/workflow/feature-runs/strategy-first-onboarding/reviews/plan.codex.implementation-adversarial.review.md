---
reviewer: "codex"
lens: "implementation-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/strategy-first-onboarding/plan.md"
artifact_sha256: "a2ba750064689303cbf1fdc349f62d950ef708519b49850e8690c9d8d0f342bf"
repo_root: "."
git_head_sha: "99c9abec482e7d75209b9ecf558e618a38b40474"
git_base_ref: "origin/main"
git_base_sha: "4723b62322a808d5a9c34d77e84e714d681d863e"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "r3 HIGH: live poll (live_status_fragment) now also only short-circuits on target-provider liveness. r3 MEDIUM1: hint on availability_notes links + all-provider mapping (hermes/openclaw->generic) + create-without-next still routes to connect. r3 MEDIUM2: needs-connecting respects PAUSED status (status-aware coverage). Verifications added."
raw_output_path: "docs/workflow/feature-runs/strategy-first-onboarding/reviews/plan.codex.implementation-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan implementation-adversarial

## Findings

- **HIGH [CODE-CONFIRMED]** The plan only fixes the initial `/me/connections` redirect, but the live poll path still has the same unconditional bounce. `app/routes/connections_pages.py`'s `live_status_fragment` returns `HX-Redirect` whenever `context["is_live_now"]` is true, and `app/templates/connections/_live_status.html` polls that fragment every 4 seconds. That means any live provider will still kick the user back to `?next` before they finish connecting the targeted provider, which breaks the provider-scoped handoff the plan is trying to add.
- **MEDIUM [CODE-CONFIRMED]** The provider-scoped handoff is incomplete across the actual entry points. The plan covers the main create-success redirect and some CTAs, but `app/templates/agents/new.html` still has per-provider “connect {{ Provider }}” links in `availability_notes` that go to `/me/connections` without `?provider=`, and the plan never says what should happen when `create_agent_or_connection` is reached without `?next` so the current `/me/agents/{id}` fallback can still bypass the connect flow. The plan also only names Claude/Gemini/OpenAI, while the codebase defines Hermes/OpenClaw providers too, so the hint mapping is undefined for real provider values.
- **MEDIUM [CODE-CONFIRMED]** The new readiness rule will misclassify paused machines as “set up.” `app/engine/connection_health.py` already has `ConnectionStatus.PAUSED`, but `enabled_provider_values` and `provider_enabled_on_any_connection` intentionally ignore connection state and only look for enabled provider rows. The plan says “needs connecting” should key off enabled coverage, not live status, so a paused-but-enabled connection will still count as ready enough to suppress the reconnect state even though it cannot actually serve turns.

## Residual Risks

- The plan still needs tests that cover both redirect paths on the connect page: initial GET and the HTMX poll fragment.
- The `?next` preservation path on validation failure is still a likely regression point, especially once the create flow stops hard-redirecting to `/me/connections`.
- The batched agent-list query needs mixed-provider fixtures to prove the new state is correct across enabled, stale, paused, and absent providers.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: r3 HIGH: live poll (live_status_fragment) now also only short-circuits on target-provider liveness. r3 MEDIUM1: hint on availability_notes links + all-provider mapping (hermes/openclaw->generic) + create-without-next still routes to connect. r3 MEDIUM2: needs-connecting respects PAUSED status (status-aware coverage). Verifications added.
