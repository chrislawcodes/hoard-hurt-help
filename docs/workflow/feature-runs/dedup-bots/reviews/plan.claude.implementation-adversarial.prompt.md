Review this plan artifact using a implementation-adversarial lens.
Stay scoped to that lens.
Approach the artifact adversarially: look for hidden flaws, omitted cases, and weak assumptions before giving credit.
Code context files are provided above. Before asserting any finding, check whether it is confirmed or refuted by the provided code. Each finding must include an evidence tag:
  [CODE-CONFIRMED] — the code directly supports this finding
  [CODE-REFUTED] — the code contradicts this finding (do not include as a finding)
  [UNVERIFIED] — relevant code was not provided; treat as lower confidence
Only assign HIGH severity to CODE-CONFIRMED findings.
The full review artifact text is included below in this prompt.
Return markdown using exactly these sections:
## Findings
## Residual Risks
Keep the response concrete and ordered by severity.

Context: reuse-report.md
# Reuse audit — bots D3 + D5

This is a dedup feature; the audit is about WHERE each shared symbol lives and
whether to reuse an existing module. No new modules are needed.

| Capability | Existing home | Verdict | Note |
|---|---|---|---|
| preset→`BotProfile` mapping (D3) | duplicated in `presets.py:124` (`resolve_profile_choice`) + `:139` (`expand_pack`) | **extend** | Add a private `_entry_to_profile(entry, pack, seed_base)` in `presets.py`; both call it. No new module. |
| seeded trust-tiebreak selection (D5) | the `min(candidates, key=(±trust, seed))` idiom across `strategies.py` (×5) + `runtime.py` (×1) | **extend / justified-leave** | Optional `pick_by_trust(candidates, *, trust_key, favor_high, seed)` in `strategies.py` (where the selectors live; `runtime`/`trust` already import `strategies`, so cycle-free). Per the spec's honest framing, sites stay as-is (`not-a-true-duplicate`) where routing isn't a clean win. |
| `latest_turn` / `records_for_latest_round` (D4) | `strategies.py:344-359` | reuse (already unified #551) | Out of scope. |
| `_seed_int` | `strategies.py:385` | reuse unchanged | Must NOT change; per-site arg tuples preserved. |

**Must NOT create:** any new module, any `utils.py`/`helpers.py`, a second
`_entry_to_profile`, or a `pick_by_trust` that hardcodes seed/trust/sign (it must take
caller closures so each site's determinism is preserved).

**Cycle check (to verify in plan/impl):** `strategies.py` imports neither `runtime`
nor `trust`; both import from `strategies`. Adding `pick_by_trust` to `strategies.py`
keeps arrows one-directional. `presets.py` `_entry_to_profile` is local.


Artifact: plan.md
# Plan — Bots D3 + D5 dedup

Built on the checkpointed `spec.md` + `reuse-report.md`. Behavior-preserving. No new
modules; no import cycle.

## Approach & homes
- **D3 (hard floor):** add `_entry_to_profile(entry: BotPackEntry, pack, seed_base: int) -> BotProfile`
  in `presets.py`; `resolve_profile_choice` and `expand_pack` both call it. Byte-identical
  field mapping incl. `fixture_pack=pack.id if pack.hidden else None`.
- **D5 (adjudicate per site):** add `pick_by_trust(candidates, *, trust_key, favor_high, seed) -> str | None`
  in `strategies.py`. Route a site through it ONLY where it's a clean win that preserves
  the pick; each caller passes its OWN `trust_key` (exact `[aid]` vs `.get(aid,0)`) and
  `seed` closure (exact `_seed_int(...)` tuple). Otherwise leave the site byte-unchanged
  (`not-a-true-duplicate`) with a reason. Expected (per both spec lenses): the favor-high
  `.get`-access sites (`_choose_from_candidates:340`, `runtime._talk_target:225`, and
  `_probe_target:226`) are the plausible clean wins; the `[aid]`-access sites
  (`_best_partner`, `_most_hostile`) and the favor-low branch are likely left as-is.
  Final dispositions decided during implementation and recorded in the ledger.

## Slices (each = full Preflight + commit)
- **Slice 0 — baseline:** record `.venv/bin/pytest -q --co` count + the collected test-ID
  list (for the AC4 name-level diff) into `tasks.md`. No code.
- **Slice 1 — D3:** characterization test FIRST — assert `resolve_profile_choice` and
  `expand_pack` produce equal `BotProfile`s for the same entry, across **a hidden and a
  non-hidden pack** (confirm `BotProfile` is a `dataclass(eq=True)`; else compare
  field-by-field). Show green on base, then extract `_entry_to_profile`, keep green.
- **Slice 2 — D5:** per-site recorded-pick characterization tests FIRST, authored and
  green on base — for EACH site intended to route, with **≥2 inputs** including a
  turn-varying case for `_probe_target` (its seed carries `context.turn`). Then route the
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
- **Test inventory:** AC4 name-level diff of collected test IDs (base vs branch) — only
  additions, none removed/renamed/skip/xfail.

## Residual Risks
- **D5 over-abstraction → forced bad helper.** *verification:* diff review judges net
  clarity per routed site; a thin-win site must be left `not-a-true-duplicate`. D5 ending
  mostly-not-unified is acceptable (D3 is the floor).
- **Single-input per-site test misses a closure defect.** *verification:* ≥2 inputs per
  site incl. turn-varying for `_probe_target`, backstopped by the `_seed_int`-tuple grep.
- **`BotProfile` equality semantics.** *verification:* confirm `dataclass(eq=True)`; else
  the D3 test compares fields explicitly.


Return only markdown with exactly these sections:
## Findings
## Residual Risks
Do not include any other sections.