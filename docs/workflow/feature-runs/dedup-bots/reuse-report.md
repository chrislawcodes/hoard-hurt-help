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
