---
reviewer: "codex"
lens: "implementation-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/strategy-first-onboarding/plan.md"
artifact_sha256: "6b482ae84da5bae1bdfc446b2618908c31d69a9aad4df43a3c85462d0715e96d"
repo_root: "."
git_head_sha: "92ae8342b430e0a106e53f3086f58a7cceb5df4f"
git_base_ref: "origin/main"
git_base_sha: "4723b62322a808d5a9c34d77e84e714d681d863e"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "Round 4: no actionable findings — plan converged."
raw_output_path: "docs/workflow/feature-runs/strategy-first-onboarding/reviews/plan.codex.implementation-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan implementation-adversarial

## Findings

- MEDIUM [CODE-CONFIRMED] The provider-scoped handoff is still globally gated. `list_connections()` and `_live_status_context()` both compute `is_live_now` / `is_playing_now` across all connections, `connections/list.html` branches on those globals, and `_live_status.html` polls only `?next=`. If any unrelated provider is already live or already playing, the new `?provider=` flow can still skip the intended setup path. Relevant code: [connections_pages.py](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/routes/connections_pages.py#L49), [connections_queries.py](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/routes/connections_queries.py#L191), [connections/list.html](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/templates/connections/list.html#L15), [connections/_live_status.html](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/templates/connections/_live_status.html#L12).

- MEDIUM [CODE-CONFIRMED] The plan’s “every provider selectable” claim fails for the empty-allowlist providers. `PROVIDER_MODELS` gives `hermes` and `openclaw` no models, `_build_model_picker_groups()` falls back to `first_any`, and `agents/new.html` only renders model options. Without a provider-level chooser or synthetic entry, those providers cannot be selected intentionally. Relevant code: [config.py](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/config.py#L151), [agents_create.py](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/routes/agents_create.py#L72), [agents/new.html](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/templates/agents/new.html#L10).

- LOW [CODE-CONFIRMED] The `?provider=` hint is not threaded through all connect entry points. `_connect_picker.html` always checks the first radio, and `seat_connect()` still emits a generic `/me/connections?next=...` URL with no provider hint. So even if the URL changes, the tab preselection and seat-reconnect path will still drop the provider context unless both call sites are updated. Relevant code: [connections/_connect_picker.html](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/templates/connections/_connect_picker.html#L12), [web_player.py](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/routes/web_player.py#L543), [seat_connect.html](/Users/chrislaw/hoard-hurt-help--feat-strategy-first-onboarding/app/templates/seat_connect.html#L10).

## Residual Risks

- If you later change the create POST from hard 400s to inline form validation, thread `?next` through the error render; the current code has no re-render path to preserve it.
- The new readiness model needs mixed-state coverage tests across paused, stale, live, and unconfigured connections; otherwise the list/detail badges can drift back to a misleading “Ready” or “No live connection” state.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: Round 4: no actionable findings — plan converged.
