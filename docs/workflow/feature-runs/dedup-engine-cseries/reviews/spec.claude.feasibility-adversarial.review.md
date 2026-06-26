---
reviewer: "claude"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/dedup-engine-cseries/spec.md"
artifact_sha256: "71404f40960dbd515b7a9bca159bfb5ea717e87920cc7998648b8b8e560d245f"
repo_root: "."
git_head_sha: "e439dd6c62cc4e3e71c58c653ccd72d786c6cc1a"
git_base_ref: "origin/main"
git_base_sha: "9d36fdc28273b44ec7b04fbdaf747b1b9f18c221"
generation_method: "claude-subagent"
resolution_status: "open"
resolution_note: ""
raw_output_path: "docs/workflow/feature-runs/dedup-engine-cseries/reviews/spec.claude.feasibility-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

**[major]** C8's core premise — that all ~6 inline cancel sites are homogeneous "captured-now, no fresh timestamp, no per-site commit" — is factually wrong, which undercuts the "behavior-preserving, drop in `_mark_cancelled(match, now)`" plan. Verified counter-evidence:
- "NO fresh timestamp" is false: `scheduler_turn_loop.py:215`, `arena.py:189`, and `arena.py:188-189` use inline `datetime.now(timezone.utc)` (a fresh timestamp), not a captured batch `now`. Only `scheduler.py:184/319/400` and `arena.py:300-301/331-332` use a captured `now`.
- "NO per-site commit" is false: every inline site is immediately followed by `await db.commit()` (e.g. `scheduler.py:186`, `arena.py:190`, `scheduler_turn_loop.py:216`). The spec's acceptance criterion 3 even contradicts itself by requiring "unchanged per-site commit batching."

The field-only helper still works mechanically (callers keep their own commit and pass either `now` or a fresh `datetime.now(timezone.utc)`), but the spec's characterization of the sites is inaccurate, and acceptance criterion 3's "captured-`now` timestamp" pin is wrong for at least three sites — a tester pinning "captured now" there would be pinning behavior that does not exist. The plan/tasks must distinguish fresh-now sites from batch-now sites or the "no behavior change" check is built on a false invariant.

**[major]** C6's claim that the `connection_health_badge.py:311` site is "byte-identical to `ensure_aware` + None-guard" and can be "route[d] through `_within_window`" omits a guard that must not be merged. The anchor "311" is the def line of `_connection_is_live`, but the comparable window logic is at lines 321-325, and that function first does a `ConnectionStatus.PAUSED` early-return (`connection_health_badge.py:319-320`) that `_within_window` (`:44-46`) does not contain. So `_connection_is_live` cannot be replaced by `_within_window`; only its trailing window expression (`:324-325`) can delegate, with the PAUSED guard kept. The same applies to the `provider_readiness.py:186` path, which reaches the window check only through `_connection_is_live` (`provider_readiness.py:183`). The spec's framing risks an over-merge that drops the PAUSED short-circuit. Severity major because it directly threatens behavior-preservation if implemented as written; the helper itself already exists and is partially used (`connection_health_badge.py:200`), so the fix is bounded.

**[minor]** Several C-series anchor line numbers point one line off or at a function header rather than the exact duplicated expression, which will make acceptance criterion 2's "exact per-cluster check" brittle. Examples: C8 lists `scheduler_turn_loop.py:214` but the `cancelled_at` write is at `:215`; C6 lists `:311` (def line) rather than the window expression at `:322-325`. Mechanically locatable, but the spec leans on these exact lines for its presence/absence proofs, so tasks.md should re-derive them rather than trust the table.

**[minor] [UNVERIFIED]** Acceptance criterion 4 hardcodes "final test count ≥ branch-base (1291)." In this environment `pytest --collect-only` reports 1149 collected with 23 import-time collection errors; the errors look environmental (e.g. `from app.main import app` import failure in the sandbox, `tests/test_request_logging.py:11`), so I cannot confirm the 1291 baseline. The figure is a brittle absolute that depends on the exact branch base and a clean environment; if any unrelated test moves, this criterion produces false failures. Recommend expressing it relative to a freshly measured base on the feature branch, not a frozen literal. Flagged UNVERIFIED because the baseline can't be confirmed here.

The following were checked and found accurate, so they are not flaws: C1 (`_SUBMIT_POLL_SECONDS = 0.25` at `turn_drivers.py:33` and `scheduler_turn_loop.py:47`; `_now()` at `turn_drivers.py:50` vs inline `datetime.now(timezone.utc)` on the sim side); C2 three-axis claim (`_open_actor_turn` `turn_drivers.py:192-211` sets `current_turn` only, blind INSERT, phase="act"; `_open_turn` `scheduler_turn_loop.py:281-320` sets `current_round`+`current_turn`, get-or-create resume, phase="talk"); C3 (`_is_bot` value-level at `user_match_start.py:44-46`, DB variant `turn_drivers.py:152-158`, inline inverse `arena.py:296-297`; `AgentKind.BOT`/`.value` equivalence holds); C4 (confirmed = left+reserved at `arena.py:98-108` and `scheduler.py:95`; watchdog left-only at `scheduler.py:316`; `used_names` is a name list at `arena.py:119`, correctly excluded); C5 (`_has_moved` byte-identical query bodies at `connection_activity.py:80-88` and `agent_onboarding.py:127-140`; `_PREGAME_STATES` at `connection_activity.py:44`); C7 (`_public_standings` `agent_play_reads.py:183` inlines the exact key of `_scoreboard_order` `:140`); C8 cycle-free home (`state_machine.py` imports only `app.models.match`, while `match_deletion.py:10` does `from app.engine.scheduler import registry`, confirming the cycle the spec wants to avoid).

## Residual Risks

- C2's "if a clean 3-axis opener is contorted → not-a-true-duplicate" is a genuine off-ramp, but the spec gives no objective threshold for "contorted." With three boolean/parametrized axes (`phase`, `resume_guard`, `set_current_round`), the unifier and the reviewer can disagree on whether the merged opener is acceptable, and the decision is subjective at the gate. The C2-seq/C2-sim characterization tests bound correctness but not the unify-vs-defer judgment.
- The whole run rests on "full pytest is the oracle," but in this sandbox the suite does not even collect cleanly (23 import errors). If those errors persist on the implementer's branch base, the Preflight Gate (criterion 5) cannot go green for reasons unrelated to this refactor, and per-cluster green-gate verification becomes impossible. Confirm a clean baseline before starting.
- C8's helper home (`state_machine.py`) is currently a pure, dependency-light module ("pure functions over GameState"); adding a `Match`-mutating, `now`-taking field-setter changes its character from pure-transition-rules to also doing field mutation. No import cycle results, but it dilutes the module's stated contract; an alternative cycle-free home may read cleaner. This is a design-fit risk, not a feasibility blocker.
- [UNVERIFIED] C5 unifies one `_has_moved` across `connection_activity.py` (param named `bot_id`) and `agent_onboarding.py` (param named `agent_id`); both query `Player.agent_id`, so they are equivalent, but the shared symbol's parameter naming and import direction must avoid a cycle between those two modules. The spec defers the home to plan.md without asserting that pair is cycle-free; verify during planning.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: open
- note: 