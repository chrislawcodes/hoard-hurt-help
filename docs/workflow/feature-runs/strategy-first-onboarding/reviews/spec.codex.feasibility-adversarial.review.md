---
reviewer: "codex"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/strategy-first-onboarding/spec.md"
artifact_sha256: "71a45879b10d602ea73cab24094252f5d2fe9377bcba5dc7e335103e395face7"
repo_root: "."
git_head_sha: "d46884de0735b6e334b670dfffe151f50205462c"
git_base_ref: "origin/main"
git_base_sha: "4723b62322a808d5a9c34d77e84e714d681d863e"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "Round 3: no actionable findings — spec converged."
raw_output_path: "docs/workflow/feature-runs/strategy-first-onboarding/reviews/spec.codex.feasibility-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

1. **[HIGH] FR-004 is not actually provider-scoped in the existing connect surface.** The connections page auto-redirects whenever *any* connection is live (`[connections_pages.py](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/routes/connections_pages.py#L95)`), and the connect UI is just a generic client tab set with multiple client choices, including Claude Code, Codex, Gemini, and Claude Desktop (`[connections_connect_guide.py](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/routes/connections_connect_guide.py#L94)`, `[connections/_connect_picker.html](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/templates/connections/_connect_picker.html#L12)`). A `?provider=` hint alone does not tell the page which client tab to open or prevent unrelated live connections from short-circuiting the flow, so users can still skip the intended provider setup. `[CODE-CONFIRMED]`

2. **[MEDIUM] The spec omits the no-`?next` destination after agent creation.** The current create route falls back to `/me/agents/{agent.id}` when `next` is absent (`[agents_create.py](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/routes/agents_create.py#L245)`), but FR-004/US2 only define the join-chain case. Once the provider-specific connect hop is added, standalone creates still need an explicit landing contract or behavior will diverge by entry point. `[CODE-CONFIRMED]`

3. **[MEDIUM] FR-006’s batching requirement is underspecified and the obvious implementation path stays query-heavy.** The agent list currently computes readiness inside the per-agent loop with `provider_is_covered()` (`[agents_list.py](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/routes/agents_list.py#L45)`), and that helper itself runs a SQL lookup per provider (`[connection_health.py](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/engine/connection_health.py#L269)`). The spec says “one coverage query, not one per agent,” but it never defines the batch shape or a shared coverage map, so this can easily ship as N repeated queries anyway. `[CODE-CONFIRMED]`

4. **[LOW] The artifact collapses an existing “offline vs unconfigured” distinction into one “needs connecting” state.** Join flow already distinguishes provider states as `live`, `offline` (configured but stale), and `unconfigured` (`[web_player.py](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/routes/web_player.py#L261)`), but the new readiness language only covers “ready” vs “needs connecting.” That loses a useful distinction for agents whose provider is already set up but merely not live right now. `[CODE-CONFIRMED]`

## Residual Risks

- The spec still assumes the generic `/me/connections` page can be targeted cleanly with a provider hint and `?next`; if that page’s client selection or auto-forward logic is more rigid than expected, the post-create hop will need more than a query param.
- Existing edge data is not covered: for example, AI agents with a missing current version are still skipped/rejected by the current join and detail code paths, so the new flow should be tested against partially broken legacy records.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: Round 3: no actionable findings — spec converged.
