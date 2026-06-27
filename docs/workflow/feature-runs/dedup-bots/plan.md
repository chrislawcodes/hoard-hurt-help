# Plan — Bots D3 + D5 dedup

Built on the checkpointed `spec.md` + `reuse-report.md`. Behavior-preserving. No new
modules; no import cycle.

## Approach & homes
- **D3 (hard floor):** add `_entry_to_profile(entry: BotPackEntry, pack, seed_base: int) -> BotProfile`
  in `presets.py`; `resolve_profile_choice` and `expand_pack` both call it. Byte-identical
  field mapping incl. `fixture_pack=pack.id if pack.hidden else None`.
- **D5 (adjudicate per site):** add
  `pick_by_trust(candidates: Sequence[str], *, trust_key: Callable[[str], int], favor_high: bool, seed: Callable[[str], int]) -> str | None`
  in `strategies.py` (annotated, to pass `mypy app/` without suppressions). Route a site
  through it ONLY where it's a clean win that preserves the pick; each caller passes its
  OWN `trust_key` (keeping its exact `[aid]` vs `.get(aid,0)` access — do NOT harmonize)
  and `seed` closure (exact `_seed_int(...)` tuple). For `_choose_from_candidates`, the
  favor-high `trusted`/HOSTILE_TRUST pre-filter and the favor-low branch stay in the
  caller; only the final `min` moves. Otherwise leave the site byte-unchanged
  (`not-a-true-duplicate`) with a reason. Expected (per both spec lenses): the favor-high
  `.get`-access sites (`_choose_from_candidates:340`, `runtime._talk_target:225`, and
  `_probe_target:226`) are the plausible clean wins; the `[aid]`-access sites
  (`_best_partner`, `_most_hostile`) and the favor-low branch are likely left as-is.
  Final dispositions decided during implementation and recorded in the ledger.

## Slices (each = full Preflight + commit)
- **Slice 0 — baseline:** record `.venv/bin/pytest -q --co` count + the collected test-ID
  list (for the AC4 name-level diff) into `tasks.md`. No code.
- **Slice 1 — D3:** characterization test FIRST — assert `resolve_profile_choice` and
  `expand_pack` produce equal `BotProfile`s for the same entry, using **`fixture_zero_floor`
  (hidden ⇒ assert `fixture_pack="fixture_zero_floor"`) AND a non-hidden pack** (e.g.
  `mixed_20` ⇒ assert `fixture_pack=None`), so the `pack.hidden` branch is exercised.
  `BotProfile` is `@dataclass(frozen=True)` ⇒ `eq=True`, so `==` is field-wise. Show green
  on base, then extract `_entry_to_profile`, keep green.
- **Slice 2 — D5:** per-site recorded-pick characterization tests FIRST — committed and
  green on base BEFORE any `pick_by_trust` edit — for EACH site intended to route. Each
  site's **≥2 inputs must produce DIFFERENT recorded picks** (else a constant-returning
  helper passes); specifically for `_probe_target`, two inputs differing ONLY in `turn`
  must FLIP the pick (proving `context.turn` is still in the seed). Then route the
  clean-win sites through `pick_by_trust`, preserving each closure exactly; leave the rest
  byte-unchanged. Record the 6-site disposition ledger.

(If, on inspection, NO D5 site is a clean win, Slice 2 ships only the per-site tests +
the ledger marking all six `not-a-true-duplicate` byte-unchanged — `pick_by_trust` is
then not added. That is an accepted outcome; D3 remains the delivered dedup.)

## Verification (each cluster's residual risk → concrete check)
- **D5 determinism:** each routed site's recorded-pick test (green on base, still green
  after) is unchanged; `rg "_seed_int\(" app/engine/bots/` shows the same arg tuples
  before/after; refactored sites keep their original `[]`/`.get` form (grep the closures).
- **D5 completeness/honesty:** PR Validation ledger lists all 6 sites by file:line →
  disposition; every `not-a-true-duplicate` proven byte-unchanged via
  `git diff origin/main -- <file>` on that line.
- **D3:** the hidden+non-hidden equivalence test; `rg` shows one `BotProfile(` build path
  (`_entry_to_profile`) reused by both functions.
- **Import cycle:** `.venv/bin/python -c "import app.engine.bots.strategies, app.engine.bots.runtime, app.engine.bots.trust, app.engine.bots.presets, app.engine.bots.service"` clean.
- **Test inventory:** AC4 name-level diff of collected test IDs (base vs branch), **both
  sides sorted**, via `.venv` — only additions, none removed/renamed/skip/xfail.

## Residual Risks
- **D5 over-abstraction → forced bad helper.** *verification:* diff review judges net
  clarity per routed site; a thin-win site must be left `not-a-true-duplicate`. D5 ending
  mostly-not-unified is acceptable (D3 is the floor).
- **Single-input per-site test misses a closure defect.** *verification:* ≥2 inputs per
  site incl. turn-varying for `_probe_target`, backstopped by the `_seed_int`-tuple grep.
- **`BotProfile` equality semantics.** *verification:* confirm `dataclass(eq=True)`; else
  the D3 test compares fields explicitly.

## Plan review reconciliation (round 1)

| # | Finding (lens) | Sev | Resolution |
|---|----------------|-----|------------|
| 1 | ≥2 inputs per D5 site must produce DIFFERENT picks; `_probe_target` turn-only pair must flip (testability) | major | Slice 2 updated: distinguishing inputs required; turn-flip for `_probe_target`. |
| 2 | mypy annotations on `pick_by_trust` closures (implementation) | minor | Signature annotated `Sequence[str]`/`Callable[[str],int]`. |
| 3 | `_choose_from_candidates` pre-filter + favor-low branch must stay in caller (implementation) | minor | Stated in Approach. |
| 4 | Pin `fixture_zero_floor` + a non-hidden pack for D3 test (testability) | minor | Slice 1 pins both. |
| 5 | `BotProfile` frozen ⇒ eq=True — drop hedge (both) | minor | Stated outright in Slice 1. |
| 6 | Sort test-ID lists before diffing; commit D5 tests before refactor edit (testability) | minor | Verification + Slice 2 updated. |
| 7 | Cycle proof is structural grep, not just import smoke (testability) | minor | Verification keeps both. |

No blockers. D3 byte-safe, cycle holds, tests writable — all CODE-CONFIRMED by the reviews.

## Review Reconciliation

- review: reviews/spec.claude.feasibility-adversarial.review.md | status: accepted | note: 3 rounds; all blockers/majors incorporated. Carrying 2 round-3 minors into plan: >=2 inputs per D5 site test (incl. turn-varying); assert BotProfile field-equality.
- review: reviews/spec.claude.requirements-adversarial.review.md | status: accepted | note: 3 rounds; all blockers/majors incorporated. Carrying 2 round-3 minors into plan: >=2 inputs per D5 site test (incl. turn-varying); assert BotProfile field-equality.
- review: reviews/plan.claude.implementation-adversarial.review.md | status: accepted | note: No blockers; one major (distinguishing per-site inputs incl. _probe_target turn-flip) + minors (mypy annotations, keep _choose_from_candidates pre-filter in caller, pin fixture_zero_floor, sort test-ID diff) all incorporated. D3 byte-safe + cycle + writability CODE-CONFIRMED.
- review: reviews/plan.claude.testability-adversarial.review.md | status: accepted | note: No blockers; one major (distinguishing per-site inputs incl. _probe_target turn-flip) + minors (mypy annotations, keep _choose_from_candidates pre-filter in caller, pin fixture_zero_floor, sort test-ID diff) all incorporated. D3 byte-safe + cycle + writability CODE-CONFIRMED.
