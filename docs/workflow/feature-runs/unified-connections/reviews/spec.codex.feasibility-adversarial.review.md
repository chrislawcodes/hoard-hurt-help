---
reviewer: "codex"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/unified-connections/spec.md"
artifact_sha256: "bf224355a41057d10138ec0d4cfe83b2c8745ebac9582942a7d57a927464e4d5"
repo_root: "."
git_head_sha: "1b8506be62344350e95f0062eae000dbf0417a74"
git_base_ref: "origin/claude/awesome-mendel-y2hj7c"
git_base_sha: "1b8506be62344350e95f0062eae000dbf0417a74"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "CONVERGED at spec level. All round-4 findings name specific FUNCTIONS inside files already in the spec's scope (no new systems vs round 3). Accepted and carried forward as explicit PLAN inputs (function-level enumeration is plan work, not spec work): (1) connections_setup.py needs provider-NULL branches in _provider_label(), _setup_message() [single connector script], _load_detached_agents() [PROVIDER_MODELS index], connection_setup_detail() and the detail/list routes (agent_count/health rendering); acceptance #1 and #8 already gate that these pages must not break. (2) agents_setup.py new_agent_form() must build model/provider choices from the grouped availability-aware dropdown (not Connection.provider), and edit_agent_version_page()/save_version() must validate models without an attached connection; web_player.py:148/181/243 readiness + capacity move off Agent.connection_id to the stored-provider + enabled-providers + join-gate-sum model (spec §4). (3) compute_connection_health() (connection_health.py:98/150) + connection list/detail agent_count move to the provider-coverage + sticky-pin model (spec §2 helper list). Residuals already covered: null/orphan-version migration -> §6 step-2c loud-fail (+--dry-run); atomic pin claim -> §2 race-safe conditional UPDATE + required two-claim test; template audit -> §5. The PLAN must address each named function; the plan checkpoint will re-review."
raw_output_path: "docs/workflow/feature-runs/unified-connections/reviews/spec.codex.feasibility-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

- HIGH [CODE-CONFIRMED] The spec removes provider selection for new connections, but the existing setup/detail flow still hard-requires `connection.provider`/`setup.provider` in several places. `connection_setup_detail()` always passes `setup.provider` into `_provider_label()` and `_setup_message()`, `_setup_message()` chooses the download script from that provider, and `_load_detached_agents()` indexes `PROVIDER_MODELS` by `connection.provider.value`. If the new machine-style path באמת sets `provider=NULL` as the spec says, `/me/connections/setup/{id}` and the connection detail page will break unless the spec adds a provider-less branch for these routes. [connections_setup.py](/Users/chrislaw/hoard-hurt-help--claude-awesome-mendel-y2hj7c/app/routes/connections_setup.py#L69) [connections_setup.py](/Users/chrislaw/hoard-hurt-help--claude-awesome-mendel-y2hj7c/app/routes/connections_setup.py#L138) [connections_setup.py](/Users/chrislaw/hoard-hurt-help--claude-awesome-mendel-y2hj7c/app/routes/connections_setup.py#L333) [connections_setup.py](/Users/chrislaw/hoard-hurt-help--claude-awesome-mendel-y2hj7c/app/routes/connections_setup.py#L376)

- MEDIUM [CODE-CONFIRMED] The spec only rewrites the agent creation POST, but `agents_setup.py` and the join flow still depend on the old attachment model in paths the spec does not explicitly cover. `new_agent_form()` still builds provider choices from `Connection.provider`, `edit_agent_version_page()` / `save_version()` still validate models through an attached connection, and `web_player.py` still loads a single `Connection` off `Agent.connection_id` to decide readiness and per-connection capacity. After agents stop being attached to connections, these pages will either show no valid choices, reject valid edits, or keep blocking joins on a capacity rule that no longer matches the new machine model. [agents_setup.py](/Users/chrislaw/hoard-hurt-help--claude-awesome-mendel-y2hj7c/app/routes/agents_setup.py#L384) [agents_setup.py](/Users/chrislaw/hoard-hurt-help--claude-awesome-mendel-y2hj7c/app/routes/agents_setup.py#L454) [agents_setup.py](/Users/chrislaw/hoard-hurt-help--claude-awesome-mendel-y2hj7c/app/routes/agents_setup.py#L501) [agents_setup.py](/Users/chrislaw/hoard-hurt-help--claude-awesome-mendel-y2hj7c/app/routes/agents_setup.py#L281) [agents_setup.py](/Users/chrislaw/hoard-hurt-help--claude-awesome-mendel-y2hj7c/app/routes/agents_setup.py#L331) [web_player.py](/Users/chrislaw/hoard-hurt-help--claude-awesome-mendel-y2hj7c/app/routes/web_player.py#L148) [web_player.py](/Users/chrislaw/hoard-hurt-help--claude-awesome-mendel-y2hj7c/app/routes/web_player.py#L181) [web_player.py](/Users/chrislaw/hoard-hurt-help--claude-awesome-mendel-y2hj7c/app/routes/web_player.py#L243)

- MEDIUM [CODE-CONFIRMED] The spec never redefines connection health and agent counts after detaching agents, but the current UI state is attachment-based end to end. `compute_connection_health()` decides READY/LIVE/STALLED/DISCONNECTED from `Agent.connection_id` and attached players, and the connection list/detail routes render attached agents and `agent_count` from that same model. Under the new design those values will go to zero or report the wrong health even when the machine is actively serving covered providers, so the connection cards and badge state will lie unless the spec replaces this definition explicitly. [connection_health.py](/Users/chrislaw/hoard-hurt-help--claude-awesome-mendel-y2hj7c/app/engine/connection_health.py#L98) [connection_health.py](/Users/chrislaw/hoard-hurt-help--claude-awesome-mendel-y2hj7c/app/engine/connection_health.py#L150) [connections_setup.py](/Users/chrislaw/hoard-hurt-help--claude-awesome-mendel-y2hj7c/app/routes/connections_setup.py#L118) [connections_setup.py](/Users/chrislaw/hoard-hurt-help--claude-awesome-mendel-y2hj7c/app/routes/connections_setup.py#L393)

## Residual Risks

- The migration path for detached agents with no `current_version_id` is still a sharp edge. The spec says the migration must fail loudly there, but the operational recovery is manual and remains a rollout risk.
- The sticky-pin failover claim needs the atomic update test the spec calls for; without that test, duplicate turn serving remains a plausible race.
- Template and copy changes outside the shown route code are still only partially specified, so some user-facing pages may continue to describe the old provider-centric model until every template is audited.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: CONVERGED at spec level. All round-4 findings name specific FUNCTIONS inside files already in the spec's scope (no new systems vs round 3). Accepted and carried forward as explicit PLAN inputs (function-level enumeration is plan work, not spec work): (1) connections_setup.py needs provider-NULL branches in _provider_label(), _setup_message() [single connector script], _load_detached_agents() [PROVIDER_MODELS index], connection_setup_detail() and the detail/list routes (agent_count/health rendering); acceptance #1 and #8 already gate that these pages must not break. (2) agents_setup.py new_agent_form() must build model/provider choices from the grouped availability-aware dropdown (not Connection.provider), and edit_agent_version_page()/save_version() must validate models without an attached connection; web_player.py:148/181/243 readiness + capacity move off Agent.connection_id to the stored-provider + enabled-providers + join-gate-sum model (spec §4). (3) compute_connection_health() (connection_health.py:98/150) + connection list/detail agent_count move to the provider-coverage + sticky-pin model (spec §2 helper list). Residuals already covered: null/orphan-version migration -> §6 step-2c loud-fail (+--dry-run); atomic pin claim -> §2 race-safe conditional UPDATE + required two-claim test; template audit -> §5. The PLAN must address each named function; the plan checkpoint will re-review.
