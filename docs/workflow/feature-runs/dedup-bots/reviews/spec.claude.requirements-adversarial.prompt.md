Review this spec artifact using a requirements-adversarial lens.
Stay scoped to that lens.
Approach the artifact adversarially: look for hidden flaws, omitted cases, and weak assumptions before giving credit.
No code context files were provided. Flag any finding that depends on an assumption about the existing codebase as [UNVERIFIED] and limit it to MEDIUM severity or lower.
The full review artifact text is included below in this prompt.
Return markdown using exactly these sections:
## Findings
## Residual Risks
Keep the response concrete and ordered by severity.

Artifact: spec.md
# Spec — Bots D-series duplication cleanup (D3 + D5)

## Summary

Behavior-preserving dedup of the remaining bot duplication in `app/engine/bots/`:
**D3** (the preset→`BotProfile` mapping built twice — a clean unify) and **D5** (the
seeded trust-tiebreak selection idiom repeated across 6 selectors — borderline, and
likely partly over-abstraction). No change to bot move/talk selection, strategy
knobs, or seeded determinism. Claude-only Feature Factory run (Claude subagents staff
every review gate via `prepare-claude-reviews`).

Revised after two adversarial spec lenses; reconciliation table at the end.

## Background

PR #551 unified D1 (crowd-follower) and **already folded in D4** (`latest_turn` /
`records_for_latest_round`, `strategies.py:344-359`) — out of scope. **D2** (leader)
is intentionally left: `runtime._leader_id` seeds `_seed_int("leader", aid)` while
`strategies._leader` seeds `_seed_int(context, aid)` — different seeds, collapsing
them changes tie outcomes. **D5 carries the same determinism hazard** and is the main
risk here.

## Honest framing of D5 (both lenses agreed)

The D5 "duplication" is **structural, not textual**: each site is a single
`min(candidates, key=lambda aid: (±trust, _seed_int(...)))` line, and the bodies
already differ on three axes — so there is no copy-pasted code to remove, only a
shared *shape*. A `pick_by_trust(candidates, *, trust_key, favor_high, seed)` helper
that takes caller-supplied closures preserves picks, but at most sites it replaces one
readable line with two lambdas + keyword args that are **longer** than the original.
So D5 may legitimately end mostly or entirely `not-a-true-duplicate`. That is an
acceptable, audited outcome — NOT a silent scope cut: every site is explicitly
dispositioned with a reason and reviewed (see AC2).

## In scope

### D3 — preset→profile mapping (clean unify; hard floor)
`resolve_profile_choice` (`presets.py:124`) and `expand_pack` (`presets.py:139`) build
`BotProfile(strategy, truthfulness, trust_model, seed=seed_base+entry.seed_offset,
version=pack.version, fixture_pack=pack.id if pack.hidden else None)` identically.
Extract `_entry_to_profile(entry, pack, seed_base) -> BotProfile` in **`presets.py`**;
both delegate. Disposition: **unified (required)**.

### D5 — seeded trust-tiebreak selectors (adjudicate every site)
Six sites, with their three differing axes (verified):

| Site | sign | trust access | seed args |
|------|------|--------------|-----------|
| `strategies._probe_target:226` | favor-high | `.get(aid,0)` | `(profile, context, aid, context.turn)` |
| `strategies._best_partner:237` | favor-high | `[aid]` | `(profile, context, aid)` |
| `strategies._most_hostile:246` | favor-low | `[aid]` | `(profile, context, aid)` |
| `strategies._choose_from_candidates:340` (favor_high) | favor-high | `.get(aid,0)` | `(context, aid)` |
| `strategies._choose_from_candidates:341` (favor_low) | favor-low | `.get(aid,0)` | `(context, aid)` |
| `runtime._talk_target:225` | favor-high | `.get(aid,0)` | `(profile, context, aid)` |

Optional shared core (in **`strategies.py`**, cycle-free — `runtime`/`trust` import
`strategies`, not the reverse):
```python
def pick_by_trust(candidates, *, trust_key, favor_high, seed) -> str | None:
    if not candidates: return None
    sign = -1 if favor_high else 1
    return min(candidates, key=lambda aid: (sign * trust_key(aid), seed(aid)))
```
A site is routed through it ONLY if that is a clean readability win that provably
preserves its pick. Each caller passes its OWN `trust_key` (keeping its exact `[aid]`
vs `.get(aid,0)` form — do NOT standardize, see Risks) and `seed` closure (its exact
`_seed_int(...)` tuple). The `candidates` passed in is the already-resolved list
(e.g. `_probe_target`'s `[...] or others` fallback runs in the caller).

## Out of scope (non-goals)
- D4 (`latest_turn`) — already unified in #551.
- D2 (`_leader_id` vs `_leader`) — different seeds, intentionally separate.
- **Seed-only tiebreaks** `crowd_choice:310` and `_leader:367`
  (`min(..., key=lambda aid: _seed_int(context, aid))`) — a different idiom (no trust
  term); do NOT fold into `pick_by_trust`.
- `_seed_int` itself and every per-site seed-argument tuple (must stay identical).
- Any change to bot behavior, trust thresholds/signs, or seeded determinism.

## Constraints
- Behavior-preserving only. Every `_seed_int(...)` argument tuple, trust access form
  (`[]` vs `.get`), and sign stays identical per call site.
- CLAUDE.md: full type annotations, no `# type: ignore`/`# noqa`, no bare except,
  fail-loud, no vague filenames. Homes: `_entry_to_profile`→`presets.py`,
  `pick_by_trust` (if introduced)→`strategies.py`. No import cycle.
- One feature per branch (`claude/dedup-bots`); full Preflight before push.

## Acceptance criteria
1. **D3 unified** (required): one `_entry_to_profile`; both `resolve_profile_choice`
   and `expand_pack` delegate. Test compares both paths' `BotProfile` output on the
   same entry for **both a hidden and a non-hidden pack** (exercising the
   `fixture_pack` conditional).
2. **D5 fully adjudicated**: every one of the 6 sites ends `unified` or
   `not-a-true-duplicate`.
   - For each `unified` site: (a) a **per-site** characterization test that records
     the exact agent picked for a fixed `(context, candidates, trust_map)` — and the
     expected agent id is **captured from the pre-refactor (`origin/main`) behavior**:
     author the test and show it GREEN on the base code FIRST (pinning current
     behavior), then refactor and require it to stay green. (A test written only
     against the post-refactor code pins nothing.) One assertion per routed site — not
     a representative pair — so the 4-tuple `context.turn` seed and each sign/access
     are covered. (b) an `rg` **absence proof** that the inline
     `min(...key=lambda aid: (...trust..., _seed_int...))` is gone from that site,
     replaced by a `pick_by_trust` call.
   - For each `not-a-true-duplicate` site: it must be **byte-unchanged from base** —
     proven by `git diff origin/main -- <file>` showing that selector's line untouched
     — so it needs no new test precisely because the code did not change; plus a
     one-line reason (which axis blocked it / readability) in the ledger. (If a
     left-alone site is in fact edited, it must instead become `unified` with the
     test above.)
3. **No behavior change** to any bot move/talk output; each refactored site keeps its
   exact trust-access form, seed tuple, and sign.
4. **Baseline measured, not hardcoded**: record `pytest -q --co` collected count on the
   branch base (via `.venv`) into `tasks.md`. Because a raw count can stay equal under
   substitution and is blind to `skip`/`xfail`, verify with a **name-level diff of
   collected test IDs** (base vs branch): only additions, no existing test removed,
   renamed-away, or flipped to skip/xfail. (System `python3` mis-collects — use `.venv`.)
5. **Full Preflight green**: `ruff check . && mypy app/ mcp_server/ && pytest -q`.
6. **PR `Validation` section** lists: ruff/mypy/full-pytest results + the per-site D5
   disposition ledger (all 6 sites by `file:line` → `unified` / `not-a-true-duplicate`
   + reason) + the D3 disposition.

## Risks
- **D5 determinism drift (highest).** Reordering the key tuple, standardizing
  `[aid]`→`.get(aid,0)` (erases the latent "`[aid]` sites build candidates from
  `trust_map.items()` so keys are always present" invariant), dropping a seed part
  (e.g. `_probe_target`'s `context.turn`), or mis-passing `favor_high` (flips
  most→least trusted) all change picks silently. *verification:* the per-site
  recorded-pick test (AC2a) for every routed site must be unchanged; `rg "_seed_int("
  app/engine/bots/` shows the same argument tuples; refactored sites keep their
  original `[]`/`.get` form.
- **Over-abstraction.** `pick_by_trust` may not improve clarity at any site. *verification:*
  the diff review judges net clarity per routed site and validates each
  `not-a-true-duplicate` reason; D5 ending mostly-not-unified is acceptable.

## Verification of "no behavior change"
The full `pytest` suite (bot strategy/runtime/trust tests) is the oracle; each routed
D5 site adds a recorded-pick test that fails if its choice changes, and D3 adds the
hidden+visible equivalence test.

## Adversarial review reconciliation (spec stage, round 1)

| # | Finding (lens) | Sev | Resolution |
|---|----------------|-----|------------|
| 1 | AC2 char test must pin EVERY routed selector, not a representative pair (both) | blocker | AC2a: per-site recorded-pick test for each `unified` site. |
| 2 | "not-a-true-duplicate" hatch has no floor → D5 could shrink to zero (requirements) | blocker | D3 is the hard floor; D5 requires full per-site adjudication (disposition + reason, reviewed) — audited, not silent. |
| 3 | 1331 baseline brittle / system-python mis-collects (both) | major | AC4: measure on branch base via `.venv`, not a literal. |
| 4 | Don't standardize `[aid]`→`.get` (erases keys-present invariant) (requirements) | major | Constraint + AC3: keep each site's exact access form in its closure. |
| 5 | D3 test must cover hidden + non-hidden pack (`fixture_pack`) (requirements) | major | AC1 covers both. |
| 6 | Absence proof the old idiom is gone (requirements) | major | AC2b: per-site `rg` absence proof. |
| 7 | Per-site disposition ledger as a concrete artifact (requirements) | major | AC6: PR Validation lists all 6 sites + reasons. |
| 8 | `favor_high` boolean drift flips selection (feasibility) | major | Covered by AC2a per-site pinning. |
| 9 | `pick_by_trust` likely over-abstraction; be honest (both) | major | "Honest framing of D5" section; not-a-true-duplicate is an accepted outcome. |
| 10 | Seed-only tiebreaks out of scope; pin homes (both) | minor | Out-of-scope list + Constraints pin homes. |

### Round 2

| # | Finding (lens) | Sev | Resolution |
|---|----------------|-----|------------|
| 11 | Characterization test written during refactor pins new code against itself (requirements) | major | AC2a: author the test and show it GREEN on the `origin/main` base FIRST (expected pick captured from base), then refactor and keep green. |
| 12 | `not-a-true-duplicate` sites have no behavioral pin (requirements) | major | Such sites must be byte-unchanged from base (proven by `git diff origin/main -- <file>`); no test needed because the code is unchanged. |
| 13 | AC4 count-equality misses substitution/skip/xfail (requirements) | minor | AC4: name-level diff of collected test IDs, not just count. |
| 14 | Redundant double empty-check weakens readability win (feasibility) | minor | Diff gate (already empowered) weighs it per routed site. |


Return only markdown with exactly these sections:
## Findings
## Residual Risks
Do not include any other sections.