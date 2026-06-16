"""Unit tests for the deterministic play-by-play headline (`_turn_headline`)."""

from __future__ import annotations

from app.engine.viewer_presentation import _turn_headline


def act(agent, action, target=None, *, mutual=False, betrayal=False, delta=0, missed=False):
    return {
        "agent_id": agent,
        "action": action,
        "target_id": target,
        "mutual": mutual,
        "betrayal": betrayal,
        "display_delta": delta,
        "was_defaulted": missed,
    }


def hoards(names):
    return [act(n, "HOARD", delta=2) for n in names]


def test_betrayal_is_narrated() -> None:
    actions = [act("Aria", "HURT", "Zed", betrayal=True, delta=-4), *hoards(["Eli", "Fin"])]
    h = _turn_headline(actions, [], None, None, ordinal=2)
    assert "Aria" in h and "Zed" in h


def test_new_pact_is_narrated() -> None:
    actions = [
        act("Bex", "HELP", "Cy", mutual=True, delta=8),
        act("Cy", "HELP", "Bex", mutual=True, delta=8),
        *hoards(["Eli", "Fin"]),
    ]
    h = _turn_headline(actions, [], None, None, ordinal=1)
    assert "Bex" in h and "Cy" in h and "+8" in h


def test_unchanged_pact_is_not_re_announced() -> None:
    pact = [
        act("Bex", "HELP", "Cy", mutual=True, delta=8),
        act("Cy", "HELP", "Bex", mutual=True, delta=8),
    ]
    actions = [*pact, *hoards(["Eli", "Fin", "Gus", "Hana"])]
    h = _turn_headline(actions, prev_actions=pact, leader=None, prev_leader=None, ordinal=4)
    assert not any(w in h for w in ("lock in", "shake hands", "alliance"))


def test_revenge_gangup_references_the_betrayal() -> None:
    prev = [act("Aria", "HURT", "Zed", betrayal=True, delta=-4)]
    actions = [
        act("Zed", "HURT", "Aria", delta=-4),
        act("Dot", "HURT", "Aria", delta=-4),
        act("Nyx", "HURT", "Aria", delta=-4),
        *hoards(["Eli", "Fin"]),
    ]
    h = _turn_headline(actions, prev_actions=prev, leader=None, prev_leader=None, ordinal=3)
    assert "Aria" in h and "betrayal" in h


def test_lead_change_folds_onto_the_top_beat() -> None:
    actions = [act("Aria", "HURT", "Zed", betrayal=True, delta=-4), *hoards(["Eli", "Fin"])]
    h = _turn_headline(actions, [], leader="Bex", prev_leader="Aria", ordinal=2)
    assert "Bex" in h and any(w in h for w in ("lead", "first place", "top"))


def test_mutual_help_is_not_described_as_a_strike() -> None:
    # A +8 mutual HELP must never be narrated with violent "swing" verbs.
    actions = [
        act("Bex", "HELP", "Cy", mutual=True, delta=8),
        act("Cy", "HELP", "Bex", mutual=True, delta=8),
        *hoards(["Eli", "Fin", "Gus", "Hana"]),
    ]
    # prev has the same pact, so it's not re-announced — leaving only the (now
    # HURT-only) swing beat as a risk. There are no hurts, so none should fire.
    h = _turn_headline(actions, prev_actions=actions, leader=None, prev_leader=None, ordinal=3)
    assert not any(w in h for w in ("clobbers", "reeling", "strike"))


def test_quiet_turn_fallback() -> None:
    actions = hoards([f"b{i}" for i in range(16)])
    h = _turn_headline(actions, [], None, None, ordinal=5)
    assert any(w in h.lower() for w in ("quiet", "calm"))


def test_is_deterministic() -> None:
    actions = [act("Aria", "HURT", "Zed", betrayal=True, delta=-4), *hoards(["Eli", "Fin"])]
    a = _turn_headline(actions, [], "Bex", "Aria", ordinal=7)
    b = _turn_headline(actions, [], "Bex", "Aria", ordinal=7)
    assert a == b
    assert a.endswith(".")
