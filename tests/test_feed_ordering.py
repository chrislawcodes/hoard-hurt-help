"""Unit tests for the game-viewer feed's highlights-first ordering + counts."""

from __future__ import annotations

from app.routes.web_viewer import _feed_sort_key, _turn_groups, _turn_summary


def _ga(agent, action, target=None, *, mutual=False, betrayal=False):
    return {
        "agent_id": agent,
        "action": action,
        "target_id": target,
        "mutual": mutual,
        "betrayal": betrayal,
    }


def test_groups_collapse_hoards_and_state_delta_once() -> None:
    actions = [
        _ga("Aria", "HURT", "Zed", betrayal=True),
        _ga("Dot", "HURT", "Eli"),
        _ga("Bex", "HELP", "Cy", mutual=True),
        _ga("Cy", "HELP", "Bex", mutual=True),
        _ga("Fin", "HELP", "Gus"),
        *[_ga(n, "HOARD") for n in ("Hana", "Ivy", "Jax", "Kai")],
    ]
    groups = {g["kind"]: g for g in _turn_groups(actions)}
    # One pact entry for the Bex<->Cy pair, not two.
    assert len(groups["pact"]["members"]) == 1
    # Hoards collapse to a single group listing all four names.
    assert len(groups["hoard"]["members"]) == 4
    assert groups["hoard"]["delta"] == "+2"
    # Betrayal sorts first within the hurt group.
    assert groups["hurt"]["members"][0]["betrayal"] is True
    # Order leads with conflict, ends with the quiet hoard list.
    kinds = [g["kind"] for g in _turn_groups(actions)]
    assert kinds == ["hurt", "pact", "help", "hoard"]


def test_groups_omit_empty_kinds() -> None:
    actions = [_ga(n, "HOARD") for n in ("A", "B", "C")]
    groups = _turn_groups(actions)
    assert [g["kind"] for g in groups] == ["hoard"]


def _act(agent, action, *, delta=0, mutual=False, betrayal=False, missed=False):
    return {
        "agent_id": agent,
        "action": action,
        "display_delta": delta,
        "mutual": mutual,
        "betrayal": betrayal,
        "was_defaulted": missed,
    }


def test_highlights_sort_orders_by_tier_then_swing() -> None:
    actions = [
        _act("Hoarder", "HOARD", delta=2),
        _act("Helper", "HELP", delta=4),
        _act("BigHurt", "HURT", delta=-4),
        _act("Mutualist", "HELP", delta=8, mutual=True),
        _act("Traitor", "HURT", delta=-4, betrayal=True),
        _act("NoShow", "HOARD", delta=2, missed=True),
    ]
    ordered = [a["agent_id"] for a in sorted(actions, key=_feed_sort_key)]
    # betrayal, then mutual, then hurt, then help, then hoard, then missed last.
    assert ordered == ["Traitor", "Mutualist", "BigHurt", "Helper", "Hoarder", "NoShow"]


def test_within_tier_bigger_swing_comes_first() -> None:
    actions = [
        _act("Small", "HURT", delta=-2),
        _act("Big", "HURT", delta=-9),
        _act("Mid", "HURT", delta=-4),
    ]
    ordered = [a["agent_id"] for a in sorted(actions, key=_feed_sort_key)]
    assert ordered == ["Big", "Mid", "Small"]


def test_equal_swing_breaks_ties_by_agent_id() -> None:
    actions = [_act("Zara", "HOARD", delta=2), _act("Abe", "HOARD", delta=2)]
    ordered = [a["agent_id"] for a in sorted(actions, key=_feed_sort_key)]
    assert ordered == ["Abe", "Zara"]


def test_turn_summary_counts_each_kind() -> None:
    actions = [
        _act("a", "HELP", delta=8, mutual=True),
        _act("b", "HELP", delta=8, mutual=True),
        _act("c", "HELP", delta=4),
        _act("d", "HURT", delta=-4, betrayal=True),
        _act("e", "HURT", delta=-4),
        _act("f", "HOARD", delta=2),
        _act("g", "HOARD", delta=2, missed=True),
    ]
    summary = _turn_summary(actions)
    assert summary == {
        "help": 3,
        "hurt": 2,
        "hoard": 2,
        "betrayal": 1,
        "mutual": 2,
        "missed": 1,
    }
