"""D3 dedup: resolve_profile_choice and expand_pack share one profile mapping.

Pins that the single-entry and whole-pack builders produce identical BotProfiles
(incl. the `fixture_pack=pack.id if pack.hidden else None` conditional and the
`seed=seed_base+entry.seed_offset` arithmetic) for both a hidden and a non-hidden
pack, so extracting `_entry_to_profile` is provably behavior-preserving.
BotProfile is a frozen dataclass, so `==` is field-wise.
"""
from __future__ import annotations

from app.engine.bots.presets import expand_pack, resolve_profile_choice


def test_choice_matches_expand_for_hidden_pack() -> None:
    seed_base = 5
    pack_id = "fixture_zero_floor"  # hidden=True
    choice = resolve_profile_choice(f"{pack_id}:0", seed_base=seed_base)
    expanded = expand_pack(pack_id, seed_base=seed_base)
    assert choice == expanded[0]
    assert choice.fixture_pack == pack_id  # hidden → fixture_pack set


def test_choice_matches_expand_for_visible_pack() -> None:
    seed_base = 7
    pack_id = "mixed_20"  # hidden=False
    choice = resolve_profile_choice(f"{pack_id}:1", seed_base=seed_base)
    expanded = expand_pack(pack_id, seed_base=seed_base)
    assert choice == expanded[1]
    assert choice.fixture_pack is None  # visible → no fixture_pack


def test_seed_base_offsets_every_entry() -> None:
    base_a = expand_pack("mixed_20", seed_base=0)
    base_b = expand_pack("mixed_20", seed_base=100)
    # seed == seed_base + entry.seed_offset, so a +100 base shifts every seed by 100.
    assert [p.seed for p in base_b] == [p.seed + 100 for p in base_a]
