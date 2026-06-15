---
reviewer: "codex"
lens: "implementation-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/strategy-first-onboarding/plan.md"
artifact_sha256: "d2512b8d0aec2dafb04e74daf48195ac8b5fcf6d1358670baf0812874e9cc814"
repo_root: "."
git_head_sha: "e3e63999d922df4064a53e8b323fb05d6e279489"
git_base_ref: "origin/main"
git_base_sha: "4723b62322a808d5a9c34d77e84e714d681d863e"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "HIGH short-circuit: handoff now only skips when the TARGET provider is live (not global is_live_now). MEDIUM CTAs: agents/new, _live_status, seat_connect now carry ?provider=. MEDIUM readiness: add explicit needs-connecting state in agents_health_presenter._is_ready_to_play + _onboarding.html (don't widen READY). LOW N+1: Slice 4 batches both coverage AND _count_agent_matches. Scope widened to agents_list/agents_health_presenter/connections_pages."
raw_output_path: "docs/workflow/feature-runs/strategy-first-onboarding/reviews/plan.codex.implementation-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan implementation-adversarial

## Findings

- **HIGH** [CODE-CONFIRMED] The new provider-scoped handoff is still globally short-circuited by any live connection. `list_connections()` in [app/routes/connections_pages.py](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/routes/connections_pages.py#L95) returns `next_url` whenever `is_live_now` is true, and `is_live_now` is computed across all connections at [L73](file:///Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/routes/connections_pages.py#L73). In the proposed flow, a user who already has one live provider but is trying to set up a different provider will skip the provider-specific connect step and bounce back to `next_url` before the target provider is actually selected.

- **MEDIUM** [CODE-CONFIRMED] The plan only updates the main `/me/connections` entry, but several existing CTAs still drop users into the generic picker with no `provider=`. That includes the connect links in [app/templates/agents/new.html](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/templates/agents/new.html#L14), the "Create your agent" CTA in [app/templates/connections/_live_status.html](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/templates/connections/_live_status.html#L57), and the seat-hold reconnect link in [app/templates/seat_connect.html](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/templates/seat_connect.html#L17). The provider hint will be lost on these paths unless they are updated too.

- **MEDIUM** [CODE-CONFIRMED] The readiness model the plan proposes is too coarse for the existing onboarding UI. `_is_ready_to_play()` in [app/routes/agents_health_presenter.py](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/routes/agents_health_presenter.py#L35) only accepts `READY/LIVE` plus `join_blocked`, and [app/templates/agents/_onboarding.html](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/templates/agents/_onboarding.html#L23) only has "Ready to play", "At capacity", or the reconnect card. If `READY` is widened to mean "provider enabled somewhere" as the plan says, a stale-but-configured agent will be rendered as "At capacity" or "Ready to play" even though the real blocker is that no live connection exists.

- **LOW** [CODE-CONFIRMED] The list-page performance slice does not eliminate all per-agent queries. `list_agents()` in [app/routes/agents_list.py](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/routes/agents_list.py#L47) still calls `_count_agent_matches(db, agent.id)` inside the loop, so the page remains N+1 on match counts even if coverage lookup is batched.

## Residual Risks

- The provider-tab preselection still needs explicit tests for valid, unknown, and absent `provider=` values so the generic picker fallback does not regress.
- The seat-hold reconnect path should be exercised end-to-end after the routing changes, because it combines the join flow, the held-seat page, and the connect redirect in one path.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: HIGH short-circuit: handoff now only skips when the TARGET provider is live (not global is_live_now). MEDIUM CTAs: agents/new, _live_status, seat_connect now carry ?provider=. MEDIUM readiness: add explicit needs-connecting state in agents_health_presenter._is_ready_to_play + _onboarding.html (don't widen READY). LOW N+1: Slice 4 batches both coverage AND _count_agent_matches. Scope widened to agents_list/agents_health_presenter/connections_pages.
