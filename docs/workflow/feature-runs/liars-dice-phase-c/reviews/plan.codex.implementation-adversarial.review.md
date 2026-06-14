---
reviewer: "codex"
lens: "implementation-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/liars-dice-phase-c/plan.md"
artifact_sha256: "a0b7965968b4510aeefdb7b03555963bd3a703586d960e015e6286208d3d0a9c"
repo_root: "."
git_head_sha: "c2a95bd672a7ad771f3d2835e8ab5c984d205530"
git_base_ref: "origin/main"
git_base_sha: "ab0afa5788a8b6a8eef3c83ad594fc5de508848a"
generation_method: "codex-runner"
resolution_status: "open"
resolution_note: ""
raw_output_path: "docs/workflow/feature-runs/liars-dice-phase-c/reviews/plan.codex.implementation-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan implementation-adversarial

## Findings

- HIGH [CODE-CONFIRMED] The “atomic seeding” claim is wrong as written. `create_match()` commits on success by default, and the listed create paths call it that way today. If the route then adds `MatchState` afterward, the match row is already durable and a crash before the second write leaves a half-initialized match without its config/state. Evidence: [`app/engine/match_creation.py`](/private/tmp/wt-liars-dice-engine-factory/app/engine/match_creation.py), [`app/routes/admin_api.py`](/private/tmp/wt-liars-dice-engine-factory/app/routes/admin_api.py), [`app/routes/game_admin_api.py`](/private/tmp/wt-liars-dice-engine-factory/app/routes/game_admin_api.py), [`app/routes/game_admin_web.py`](/private/tmp/wt-liars-dice-engine-factory/app/routes/game_admin_web.py).

- HIGH [CODE-CONFIRMED] The “route validators only” bounds plan will still reject legal Liar’s Dice sizes before the validator ever runs. `CreateGameRequest` hard-codes `min_players`/`max_players` to 6..10, so any game-specific bound outside that range is blocked at schema validation time. The browser create path also has fixed bounds/defaults, so the create UX is not actually game-aware unless those layers change too. Evidence: [`app/schemas/admin.py`](/private/tmp/wt-liars-dice-engine-factory/app/schemas/admin.py), [`app/routes/game_admin_web.py`](/private/tmp/wt-liars-dice-engine-factory/app/routes/game_admin_web.py).

- MEDIUM [CODE-CONFIRMED] The plan leaves no transport for the shared state it wants non-active clients and spectators to see. `WaitingResponse` has no `public_state` field, and `poll_turn()` returns that model for every non-active case. `SpectatorState` also has no `public_state` slot, so the viewer/spectator path cannot surface `module.public_state_for()` as the plan claims. Evidence: [`app/schemas/agent.py`](/private/tmp/wt-liars-dice-engine-factory/app/schemas/agent.py), [`app/engine/agent_play.py`](/private/tmp/wt-liars-dice-engine-factory/app/engine/agent_play.py), [`app/schemas/spectator.py`](/private/tmp/wt-liars-dice-engine-factory/app/schemas/spectator.py), [`app/routes/spectator_api.py`](/private/tmp/wt-liars-dice-engine-factory/app/routes/spectator_api.py).

## Residual Risks

- The new Liar’s Dice rules engine still needs explicit coverage for wild/ace edge cases and showdown idempotency; those are the easiest places for “pure” math and resume logic to drift apart.

- If any viewer or agent path keeps deriving state from generic timeline rows instead of the module-owned state hooks, hidden dice can still leak or the UI can desync. That needs an end-to-end leak test across agent poll, spectator JSON, and the rendered viewer.

- Bot determinism still depends on picking a stable seed source and reusing the same turn index after restart. That should be pinned by a restart/replay test, not just by a happy-path bots-only match.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: open
- note: 