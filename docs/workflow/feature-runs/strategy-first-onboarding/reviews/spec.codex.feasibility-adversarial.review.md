---
reviewer: "codex"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/strategy-first-onboarding/spec.md"
artifact_sha256: "c42697eb20f7b868ca287b802ca6f283ff648c5ca36eb5cbaa54db29bdcb3774"
repo_root: "."
git_head_sha: "fec4fcad2535856ded3533e67243ba454ba02f9b"
git_base_ref: "origin/main"
git_base_sha: "4723b62322a808d5a9c34d77e84e714d681d863e"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "FR-001/FR-002 now require changing BOTH the POST gate and the GET form/template (new_agent_form has_enabled_provider + agents/new.html connect-first card + disabled picker). FR-004 requires a ?provider= hint to preselect the connect tab (one client=one provider per #392); generic fallback kept. FR-006 names agents/list.html + detail.html and a provider-scoped CTA."
raw_output_path: "docs/workflow/feature-runs/strategy-first-onboarding/reviews/spec.codex.feasibility-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

- **HIGH** `[CODE-CONFIRMED]` The spec only describes removing the POST-time redirect gate, but the zero-connection dead-end is also enforced in the GET/template path. `new_agent_form` still computes `has_enabled_provider`, and `agents/new.html` still renders a "Connect an AI client first" card plus a `/me/connections` CTA whenever that flag is false. The picker also disables every provider group and option when nothing is enabled. That means User Story 1 is still blocked unless the spec explicitly requires changing both the route and the template. [app/routes/agents_create.py](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/routes/agents_create.py#L144) [app/templates/agents/new.html](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/templates/agents/new.html#L10)
- **MEDIUM** `[CODE-CONFIRMED]` The spec assumes the post-create handoff can be made specific to the chosen provider, but the current connections surface is provider-neutral. `list_connections()` only accepts `next`, the page renders one generic client picker for all providers, and the create handler redirects to `/me/connections` without any provider hint. Without an explicit provider parameter or preselected tab state, "connect Claude Code" is not derivable from the existing flow. [app/routes/connections_pages.py](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/routes/connections_pages.py#L49) [app/templates/connections/list.html](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/templates/connections/list.html#L15) [app/routes/agents_create.py](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/routes/agents_create.py#L202)
- **MEDIUM** `[CODE-CONFIRMED]` FR-006 is underspecified against the current agent UI. The list page only shows a health badge, name, model, and row link, with no connect CTA at all. The detail page does have a recovery action, but it is the generic `/me/connections` link, not a provider-scoped action. If the intent is a per-agent "needs connecting" fix path, the spec needs to name those template changes explicitly. [app/templates/agents/list.html](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/templates/agents/list.html#L17) [app/templates/agents/detail.html](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/templates/agents/detail.html#L27)

## Residual Risks

- The spec still does not define how the chosen provider should be carried into the generic connect page. If that state is not encoded in the URL or template context, users may still have to hunt for the right client or tab.
- The current connection model can enable multiple providers on one connection, which does not cleanly match the spec’s "specific provider" framing. That mismatch needs to be reconciled before implementation to avoid a confusing handoff.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: FR-001/FR-002 now require changing BOTH the POST gate and the GET form/template (new_agent_form has_enabled_provider + agents/new.html connect-first card + disabled picker). FR-004 requires a ?provider= hint to preselect the connect tab (one client=one provider per #392); generic fallback kept. FR-006 names agents/list.html + detail.html and a provider-scoped CTA.
