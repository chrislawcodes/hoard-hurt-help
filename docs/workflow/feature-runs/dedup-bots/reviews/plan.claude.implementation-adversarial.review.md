---
reviewer: "claude"
lens: "implementation-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/dedup-bots/plan.md"
artifact_sha256: "6d6b48deb87bbb20c2df628353c06c4345a7a6ab14ba7c1deb4304b37b8e011c"
repo_root: "."
git_head_sha: "ac6bcd073ffcb5c4e9776756789daf2220971b07"
git_base_ref: "origin/main"
git_base_sha: "cbef9fdbc8e79cc3f181fb89564695924e81ade2"
generation_method: "claude-subagent"
resolution_status: "accepted"
resolution_note: "No blockers; one major (distinguishing per-site inputs incl. _probe_target turn-flip) + minors (mypy annotations, keep _choose_from_candidates pre-filter in caller, pin fixture_zero_floor, sort test-ID diff) all incorporated. D3 byte-safe + cycle + writability CODE-CONFIRMED."
raw_output_path: "docs/workflow/feature-runs/dedup-bots/reviews/plan.claude.implementation-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan implementation-adversarial

## Findings

**[minor] The three "plausible clean win" sites don't share a uniform call — `_choose_from_candidates:340` passes `_seed_int(context, aid)` (no profile), `_talk_target:225` passes `(profile, context, aid)`, `_probe_target:227` passes `(profile, context, aid, context.turn)`.** [CODE-CONFIRMED] Not a design defect — the plan mandates each caller pass its OWN exact seed closure, and a `seed` callable accommodates all three. But it refutes "homogeneous cluster": they share only `favor_high=True` + `.get`-access. The net-clarity bar the plan sets is genuinely at risk; routing stays optional (correct).

**[minor] `_probe_target` is reached from two sites with different `context.phase` (strategies.py:126 and runtime._decide_action:180); `_seed_int` folds phase via `seed_basis`.** [CODE-CONFIRMED] Turn-varying alone doesn't cover the phase axis. Refactor is verbatim-closure so safe regardless; a test-coverage note, not a routing risk.

**[blocker-cleared] `BotProfile` is `@dataclass(frozen=True)` ⇒ `eq=True` (types.py:12).** [CODE-REFUTED as risk] The D3 equality test works directly; the plan's field-by-field hedge is unnecessary.

**D3 extraction is byte-safe; cycle claim holds; slice ordering sound.** [CODE-CONFIRMED] presets.py:129-136 vs :142-149 identical mapping; strategies imports neither runtime nor trust (strategies.py:9-12); presets imports only `.types`.

## Residual Risks

- **mypy on `pick_by_trust` closures.** Annotate `candidates: Sequence[str]`, `trust_key: Callable[[str], int]`, `seed: Callable[[str], int]` to pass `mypy app/` without suppressions. The `[aid]` (KeyError) vs `.get(aid,0)` access MUST be preserved in each closure — do not harmonize.
- **`_choose_from_candidates` two-branch trap (strategies.py:337-341):** favor-high first filters to `trusted` (HOSTILE_TRUST gate) and returns None if empty; favor-low does not. If routed, that pre-filter + the favor-low branch stay in the caller; only the final `min` moves. Naively routing both branches changes the favor-low behavior.
- The `_seed_int(` tuple grep stays valid post-refactor (the call moves into a closure but the text is unchanged).

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: No blockers; one major (distinguishing per-site inputs incl. _probe_target turn-flip) + minors (mypy annotations, keep _choose_from_candidates pre-filter in caller, pin fixture_zero_floor, sort test-ID diff) all incorporated. D3 byte-safe + cycle + writability CODE-CONFIRMED.
