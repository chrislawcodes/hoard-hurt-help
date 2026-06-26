"""Unit tests for the deterministic spectator insights, using real G_0009 data."""

from __future__ import annotations

from app.engine.game_insights import (
    default_round_detail,
    default_season_overview,
    round_results,
)
from app.engine.game_records import Action, ActionRecord, PlayerRecord
from app.games.hoard_hurt_help.insights import grudges, round_detail, season_overview


def act(rnd: int, turn: int, actor: str, action: Action, target: str | None, after: int) -> ActionRecord:
    return ActionRecord(
        round=rnd,
        turn=turn,
        actor_id=actor,
        action=action,
        target_id=target,
        message="",
        points_delta=0,
        round_score_after=after,
        was_defaulted=False,
    )


# The actual G_0009 prod game: 3 bots, 2 rounds × 3 turns.
ACTIONS = [
    act(1, 1, "RAW_3", "HURT", "RAW_1", 0),
    act(1, 1, "RAW_1", "HELP", "RAW_2", 0),
    act(1, 1, "RAW_2", "HURT", "RAW_3", 4),
    act(1, 2, "RAW_2", "HURT", "RAW_1", 4),
    act(1, 2, "RAW_3", "HOARD", None, 6),
    act(1, 2, "RAW_1", "HELP", "RAW_3", 0),
    act(1, 3, "RAW_2", "HURT", "RAW_1", 0),
    act(1, 3, "RAW_3", "HURT", "RAW_2", 6),
    act(1, 3, "RAW_1", "HURT", "RAW_2", 0),
    act(2, 1, "RAW_2", "HURT", "RAW_3", 0),
    act(2, 1, "RAW_3", "HURT", "RAW_1", 0),
    act(2, 1, "RAW_1", "HELP", "RAW_3", 0),
    act(2, 2, "RAW_1", "HOARD", None, 2),
    act(2, 2, "RAW_3", "HELP", "RAW_1", 0),
    act(2, 2, "RAW_2", "HURT", "RAW_1", 0),
    act(2, 3, "RAW_2", "HOARD", None, 2),
    act(2, 3, "RAW_3", "HOARD", None, 2),
    act(2, 3, "RAW_1", "HOARD", None, 4),
]

PLAYERS = [
    PlayerRecord("RAW_1", round_score=4, total_score=4, round_wins=1.0),
    PlayerRecord("RAW_2", round_score=2, total_score=2, round_wins=0.0),
    PlayerRecord("RAW_3", round_score=2, total_score=8, round_wins=1.0),
]


def test_round_results() -> None:
    res = round_results(ACTIONS)
    assert [(r.round, r.winner, r.score) for r in res] == [(1, "RAW_3", 6), (2, "RAW_1", 4)]


def test_season_standings_break_tie_by_total_score() -> None:
    ov = season_overview(PLAYERS, ACTIONS, total_rounds=2, current_round=2, game_active=False)
    # RAW_1 and RAW_3 each have 1 round-win; RAW_3 ranks first on total score (8 vs 4).
    assert [s.agent_id for s in ov.standings] == ["RAW_3", "RAW_1", "RAW_2"]
    assert ov.live_round is None  # game not active


def test_grudges_finds_feud_and_vendetta() -> None:
    gr, total = grudges(ACTIONS)
    kinds = {(g.kind) for g in gr}
    assert "feud" in kinds
    assert "vendetta" in kinds  # RAW_3 hunting RAW_1 (hurt it both rounds, never helped back... )
    # RAW_1 ⇄ RAW_2 traded hurts → a feud is among the top grudges.
    assert any("RAW_1" in g.text and "RAW_2" in g.text and g.kind == "feud" for g in gr)


def test_round_detail_betrayal_and_pileon() -> None:
    rd = round_detail(1, PLAYERS, ACTIONS)
    assert rd.winner == "RAW_3"          # round 1 is complete (round 2 exists)
    assert rd.complete is True
    assert rd.leaderboard[0].agent_id == "RAW_3" and rd.leaderboard[0].score == 6
    kinds = {e.kind for e in rd.events}
    assert "betrayal" in kinds           # RAW_1 helped RAW_2 (T1) then hurt it (T3)
    assert "pileon" in kinds             # RAW_1 + RAW_3 both hit RAW_2 on T3
    assert any("RAW_2" in e.text for e in rd.events if e.kind == "pileon")


def test_round_detail_live_round_not_complete() -> None:
    rd = round_detail(2, PLAYERS, ACTIONS)
    assert rd.complete is False          # no round 3 in the log
    assert rd.winner is None
    assert "Round 2 opened after RAW_3 took round 1" in rd.intro


def test_season_overview_reuses_default_scaffolding_and_overlays_only_grudges() -> None:
    """PD season overview = the default skeleton plus grudges; nothing else differs."""
    base = default_season_overview(
        PLAYERS, ACTIONS, total_rounds=2, current_round=2, game_active=False
    )
    pd = season_overview(PLAYERS, ACTIONS, total_rounds=2, current_round=2, game_active=False)

    # The shared scaffolding is byte-identical between the default and PD.
    assert pd.standings == base.standings
    assert pd.results == base.results
    assert pd.rounds_played == base.rounds_played
    assert pd.total_rounds == base.total_rounds
    assert pd.tiebreaker == base.tiebreaker
    assert pd.season_feed == base.season_feed
    assert pd.live_round == base.live_round

    # The default path stays relationship-free; PD overlays real grudges.
    assert base.grudges == [] and base.grudge_total == 0
    expected_grudges, expected_total = grudges(ACTIONS)
    assert pd.grudges == expected_grudges
    assert pd.grudge_total == expected_total
    assert pd.grudges  # the G_0009 fixture has feuds/vendettas, so this is non-empty


def test_round_detail_reuses_default_scaffolding_and_overlays_only_signals() -> None:
    """PD round detail = the default skeleton plus mood/alliances/events; rest identical."""
    base = default_round_detail(1, PLAYERS, ACTIONS)
    pd = round_detail(1, PLAYERS, ACTIONS)

    # The shared scaffolding is byte-identical between the default and PD.
    assert pd.round == base.round
    assert pd.leaderboard == base.leaderboard
    assert pd.surging == base.surging
    assert pd.intro == base.intro
    assert pd.winner == base.winner
    assert pd.complete == base.complete

    # The default path stays neutral; PD overlays real relationship signals.
    assert base.mood == 0.5 and base.mood_label == "mixed" and base.alliances == []
    # The default feed is surge/open-only (no relationships); PD adds betrayal/pile-on.
    default_kinds = {e.kind for e in base.events}
    assert default_kinds <= {"surge", "open"}
    pd_kinds = {e.kind for e in pd.events}
    assert "betrayal" in pd_kinds
    assert "pileon" in pd_kinds
