## Findings

**[minor] The three "plausible clean win" sites don't share a uniform call — `_choose_from_candidates:340` passes `_seed_int(context, aid)` (no profile), `_talk_target:225` passes `(profile, context, aid)`, `_probe_target:227` passes `(profile, context, aid, context.turn)`.** [CODE-CONFIRMED] Not a design defect — the plan mandates each caller pass its OWN exact seed closure, and a `seed` callable accommodates all three. But it refutes "homogeneous cluster": they share only `favor_high=True` + `.get`-access. The net-clarity bar the plan sets is genuinely at risk; routing stays optional (correct).

**[minor] `_probe_target` is reached from two sites with different `context.phase` (strategies.py:126 and runtime._decide_action:180); `_seed_int` folds phase via `seed_basis`.** [CODE-CONFIRMED] Turn-varying alone doesn't cover the phase axis. Refactor is verbatim-closure so safe regardless; a test-coverage note, not a routing risk.

**[blocker-cleared] `BotProfile` is `@dataclass(frozen=True)` ⇒ `eq=True` (types.py:12).** [CODE-REFUTED as risk] The D3 equality test works directly; the plan's field-by-field hedge is unnecessary.

**D3 extraction is byte-safe; cycle claim holds; slice ordering sound.** [CODE-CONFIRMED] presets.py:129-136 vs :142-149 identical mapping; strategies imports neither runtime nor trust (strategies.py:9-12); presets imports only `.types`.

## Residual Risks

- **mypy on `pick_by_trust` closures.** Annotate `candidates: Sequence[str]`, `trust_key: Callable[[str], int]`, `seed: Callable[[str], int]` to pass `mypy app/` without suppressions. The `[aid]` (KeyError) vs `.get(aid,0)` access MUST be preserved in each closure — do not harmonize.
- **`_choose_from_candidates` two-branch trap (strategies.py:337-341):** favor-high first filters to `trusted` (HOSTILE_TRUST gate) and returns None if empty; favor-low does not. If routed, that pre-filter + the favor-low branch stay in the caller; only the final `min` moves. Naively routing both branches changes the favor-low behavior.
- The `_seed_int(` tuple grep stays valid post-refactor (the call moves into a closure but the text is unchanged).
