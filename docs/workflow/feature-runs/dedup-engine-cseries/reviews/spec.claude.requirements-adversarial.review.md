---
reviewer: "claude"
lens: "requirements-adversarial"
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
raw_output_path: "docs/workflow/feature-runs/dedup-engine-cseries/reviews/spec.claude.requirements-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec requirements-adversarial

## Findings

**[major] Criterion 4's test-count baseline ("1291") is unverifiable and contradicted by the working checkout, making the acceptance gate unfalsifiable as written.** Criterion 4 requires "final test count ≥ branch-base (1291) + the new characterization tests." Local collection in this repo reports `1149 tests collected, 23 errors in collection` (`python3 -m pytest --collect-only -q`). The "1291" number is asserted with no command that produces it, no pinned commit/branch it was measured on, and no statement of which pytest invocation/markers were used (the full suite vs. fast lane differ). A reviewer cannot tell whether 1291 is stale, measured on a different base, or whether the 23 collection errors are pre-existing. The criterion should (a) state the exact command and commit that yields the baseline, and (b) require the baseline to be re-measured on the branch base at run start rather than hard-coding a literal — otherwise an implementer can satisfy "≥ 1291" while real coverage regressed, or fail the gate for reasons unrelated to this work.

**[major] C8 acceptance criterion 3 ("captured-`now`" / "NO fresh timestamp" at all sites) does not match the actual sites and is not testable as one uniform pin.** The spec's C8 row and criterion 3 describe all ~6 inline cancels as `state=CANCELLED; cancelled_at=<captured now>` with "NO fresh timestamp," and the required behavior is "identical cancel side effects incl. captured-`now` timestamp." But the sites are not uniform: `scheduler.py:185,320,401` and `arena.py:301,332` use a captured `now`, while `arena.py:189` and `scheduler_turn_loop.py:215` use an inline `datetime.now(timezone.utc)` (a fresh timestamp at the call). The `_mark_cancelled(match, now)` helper takes `now` as a parameter, so the inline sites must pass `datetime.now(timezone.utc)` — which is correct — but criterion 3's blanket "captured-`now` / no fresh timestamp" wording is false for two of the sites and gives no per-site acceptance check distinguishing the two cases. There is no characterization test required for C8 at all (unlike C2/C4/C5), so this medium-risk cluster's most subtle invariant (which sites keep fresh-now vs. captured-now) rests entirely on the full suite, whose adequacy for this path is unproven.

**[major] No characterization test is required for C8 despite it being flagged medium-risk with subtle side-effect semantics.** Section "Required characterization tests" lists tests only for C2, C4, C5. C8 is explicitly medium-risk ("cancel side effects + import cycle") and the spec itself stresses that the helper must NOT absorb `registry.stop`, must preserve per-site commit batching, and must keep the captured-vs-fresh `now` distinction. The only C8 verification offered is an import-cycle smoke check and a grep for added `registry.stop`. Neither proves the cancel *field writes* are behavior-identical across the six refactored sites. By the spec's own logic in reconciliation finding #2 ("pytest is the oracle" is invalid for a divergent path until a test pins it), C8 needs at least one characterization test pinning that a cancelled match gets `cancelled_at` from the passed `now` and gains no new side effects.

**[minor] Anchor line numbers are off by one at several C8 sites (line drift), weakening "Anchor locations (verified)".** The table marks anchors "(verified)," but C8 lists `scheduler.py:184` (actual `185`), `arena.py:188` (actual `189`), `scheduler_turn_loop.py:214` (actual `215`). The targets exist and are correct in substance, so this is cosmetic, but a table claiming verified line numbers that are uniformly off-by-one suggests the verification was done against a slightly different revision; tasks.md should re-resolve anchors by symbol/grep, not by line, so checks don't silently miss.

**[minor] C5's "byte-identical queries — verified" is overstated; the two `_has_moved` bodies are semantically equal but not byte-identical.** `connection_activity.py:80` returns `(await db.execute(stmt)).first() is not None` with a named `stmt` and parameter `bot_id`; `agent_onboarding.py:127` inlines the select and returns `row is not None` with parameter `agent_id`. Functionally identical, so unification is safe, but the C4-style "verified safe / byte-identical" language is inaccurate and the C5-precedence test (criterion 4) only pins truth-value and onboarding precedence — fine — yet the spec's confidence wording could lead an implementer to skip confirming the join/filter really match. Low impact because the required test covers the observable behavior.

**[minor] Criterion 2's presence checks are illustrative, not exhaustive, and the per-cluster checks are deferred to tasks.md (not in this artifact).** Criterion 2 gives example greps for C5 and C1 only and states "The exact check per cluster is listed in `tasks.md`." For a spec checkpoint, the per-cluster falsifiable check for C2/C3/C4/C6/C7/C8 cannot be evaluated here at all — the acceptance criterion is a promissory note. This is acceptable for a spec→tasks pipeline but means criterion 2 is presently unverifiable for six of eight clusters; reviewers of tasks.md must confirm each one resolves by symbol, not line.

## Residual Risks

- **C2 "not-a-true-duplicate" escape hatch can mask a real-but-hard merge.** The spec correctly allows C2 to land as not-a-true-duplicate if a clean 3-axis opener is "contorted," but "contorted" is subjective and C2 is the highest-risk cluster. The C2-seq/C2-sim tests pin behavior either way, so a wrong *merge* is caught, but a premature not-a-true-duplicate (giving up on a safe unification) is not detectable by any criterion. Risk is bounded to "missed cleanup," not "broken behavior."
- **Collection errors in the working tree (23) may indicate the Preflight Gate (criterion 5) is not currently green on this base.** If those errors are pre-existing on the branch base, the "full Preflight Gate green" criterion may be blocked for reasons unrelated to this refactor; the spec assumes a green starting point but does not require validating it before starting. [UNVERIFIED] — could be an environment/dependency artifact of this checkout rather than the branch base.
- **C4 helper's "parameterized by whether reserved seats are excluded" still allows a wrong default.** The C4-watchdog test pins the held-seat ACTIVE case for `_watchdog` and `_active_player_count`, but the helper has four call sites with two filter modes; only two are pinned by the test. A wrong filter mode at `arena.py:98` or `arena.py:109/113` could pass the suite if no existing test exercises that exact seated/confirmed distinction. [UNVERIFIED] — depends on existing arena test coverage not inspected here.
- **"Avoid import cycles" verification is a single `python -c` import smoke test.** It proves the three named modules import clean together but does not prove no *new* cycle is introduced via a transitive third module, nor that lazy in-function imports (the pattern `scheduler_turn_loop.py` already relies on) weren't needed and silently omitted. Low likelihood given the explicit home guidance (`state_machine.py`), but the verification is weaker than the constraint's "assert no cycle."

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: open
- note: 