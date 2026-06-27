## Findings

**[CODE-CONFIRMED] D5 selector tests are writable with no DB.** The sites take only `(context, profile, trust_map)` / a `move` dict; `BotContext`/`BotProfile` are frozen dataclasses constructed directly in tests/test_bots_engine.py (`_context` helper :43, `BotProfile(...)` :230); tests already import `_`-prefixed strategy privates. Writable.

**[CODE-CONFIRMED] `BotProfile` frozen ⇒ eq=True (types.py:12); D3 equality test works.** State this outright; drop the hedge.

**[CODE-CONFIRMED] Hidden-pack fixture exists: `BOT_PACKS["fixture_zero_floor"]` hidden=True (presets.py:84-93); others hidden=False.** D3 hidden+non-hidden test is writable with no new fixture. [minor] Pin `fixture_zero_floor` (hidden, assert `fixture_pack="fixture_zero_floor"`) + a non-hidden pack (assert `fixture_pack=None`) explicitly so the `pack.hidden` branch is actually exercised.

**[major] ≥2 inputs per site is insufficient unless the inputs produce DIFFERENT recorded picks.** Two inputs that pick the same agent can't distinguish "wired to the right closure" from "wired to a wrong-but-similar one"; a constant-returning helper would pass. Require, per routed site, at least one input pair whose recorded picks DIFFER — and specifically for `_probe_target`, two inputs differing ONLY in `turn` that FLIP the pick (proving `context.turn` is still in the seed).

**[minor] "green on base" is by-construction for recorded-pick tests** (they record whatever base produces), so it proves only that the test runs. The load-bearing step is: record the base pick as a literal, commit the test BEFORE any `pick_by_trust` edit, then require that literal still holds post-refactor (enforced by a diff showing the test predates the refactor edit).

**[minor] Import-cycle smoke test is weak; keep the structural grep assertion** (strategies imports neither runtime nor trust) as the real cycle proof.

## Residual Risks

- AC4 test-ID diff: **sort both sides** before diffing (stable order); use `.venv` consistently.
- If no D5 site is a clean win, Slice 2 ships only characterization tests (regression pins) + the ledger — accepted; zero new-code coverage then.
- `_talk_target` test imports from `runtime`; routing it adds no new cycle (runtime already imports from strategies).
