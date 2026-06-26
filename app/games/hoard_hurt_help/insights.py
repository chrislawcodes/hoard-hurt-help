"""Prisoner's Dilemma spectator insights: season overview + per-round detail.

Turns the resolved action log into the human-facing analysis — the round-win
race, round results, grudges, and a per-round event feed. No AI and no
message-text reading: everything is derived from actions. Reuses this game's
`board_signals` for per-round mood / alliances / surging.

These read HELP/HURT relationships, so they are PD-specific and live in the PD
game module; the platform reaches them only through
`GameModule.season_overview(...)` / `round_detail(...)`.

Two scopes, kept deliberately distinct:
- SEASON (carries across rounds): round-wins, round results, grudges, tiebreaker.
- ROUND (resets each round): leaderboard-from-0, mood, alliances, the event feed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import replace

from app.engine.game_insights import (
    Event,
    Grudge,
    RoundDetail,
    SeasonOverview,
    default_round_detail,
    default_season_overview,
)
from app.engine.game_records import ActionRecord, PlayerRecord
from app.games.hoard_hurt_help.board_signals import compute_board_signals

# This game's action names: HOARD / HELP / HURT. The grudge / event readers
# bucket the action log by HELP/HURT relationships, so the names live here.
_HELP, _HURT = "HELP", "HURT"

GRUDGE_CAP = 6
MIN_FEUD_HITS = 2
MIN_ALLY_HELPS = 2
MIN_VENDETTA_HITS = 2
MIN_ONESIDED_HELPS = 2
PILE_ON_MIN = 2


# ---------- helpers ----------


def _relationships(actions: Sequence[ActionRecord]) -> tuple[Counter[tuple[str, str]], Counter[tuple[str, str]]]:
    helps: Counter[tuple[str, str]] = Counter()
    hurts: Counter[tuple[str, str]] = Counter()
    for a in actions:
        if a.target_id is None:
            continue
        if a.action == _HELP:
            helps[(a.actor_id, a.target_id)] += 1
        elif a.action == _HURT:
            hurts[(a.actor_id, a.target_id)] += 1
    return helps, hurts


def grudges(actions: Sequence[ActionRecord], cap: int = GRUDGE_CAP) -> tuple[list[Grudge], int]:
    """The most notable season-long relationships (feuds, alliances, vendettas)."""
    helps, hurts = _relationships(actions)
    pairs: set[frozenset[str]] = set()
    for a, b in list(helps) + list(hurts):
        if a != b:
            pairs.add(frozenset((a, b)))

    scored: list[Grudge] = []
    for pair in pairs:
        a, b = sorted(pair)
        h_ab, h_ba = hurts[(a, b)], hurts[(b, a)]
        he_ab, he_ba = helps[(a, b)], helps[(b, a)]
        total_hurt, total_help = h_ab + h_ba, he_ab + he_ba
        if min(h_ab, h_ba) >= 1 and total_hurt >= MIN_FEUD_HITS:
            scored.append(Grudge("feud", "⚔", f"{a} ⇄ {b} · feud, {total_hurt} hits", total_hurt))
        elif min(he_ab, he_ba) >= MIN_ALLY_HELPS:
            scored.append(Grudge("alliance", "\U0001f91d", f"{a} ⇄ {b} · allied, {total_help} helps", total_help))
        elif h_ab >= MIN_VENDETTA_HITS and h_ba == 0:
            scored.append(Grudge("vendetta", "\U0001f501", f"{a} hunting {b} · {h_ab} hits", h_ab))
        elif h_ba >= MIN_VENDETTA_HITS and h_ab == 0:
            scored.append(Grudge("vendetta", "\U0001f501", f"{b} hunting {a} · {h_ba} hits", h_ba))
        elif he_ab >= MIN_ONESIDED_HELPS and he_ba == 0:
            scored.append(Grudge("one_sided", "\U0001f971", f"{a} helps {b} · {he_ab}× unreturned", he_ab))
        elif he_ba >= MIN_ONESIDED_HELPS and he_ab == 0:
            scored.append(Grudge("one_sided", "\U0001f971", f"{b} helps {a} · {he_ba}× unreturned", he_ba))
    scored.sort(key=lambda g: (-g.weight, g.text))
    return scored[:cap], len(scored)


def _earliest_helps(actions: Sequence[ActionRecord]) -> dict[tuple[str, str], tuple[int, int]]:
    earliest: dict[tuple[str, str], tuple[int, int]] = {}
    for a in actions:
        if a.action == _HELP and a.target_id is not None:
            key = (a.actor_id, a.target_id)
            when = (a.round, a.turn)
            if key not in earliest or when < earliest[key]:
                earliest[key] = when
    return earliest


def _round_events(
    round_num: int,
    players: Sequence[PlayerRecord],
    actions: Sequence[ActionRecord],
) -> list[Event]:
    """The within-round event feed, newest turn first."""
    earliest_help = _earliest_helps(actions)
    round_actions = [a for a in actions if a.round == round_num]
    turns = sorted({a.turn for a in round_actions})
    events: list[Event] = []

    for turn in turns:
        this = [a for a in round_actions if a.turn == turn]
        # Pile-on: 2+ HURTs on the same target this turn.
        hurt_targets: Counter[str] = Counter(
            a.target_id for a in this if a.action == _HURT and a.target_id is not None
        )
        for target, n in sorted(hurt_targets.items()):
            if n >= PILE_ON_MIN:
                attackers = sorted(a.actor_id for a in this if a.action == _HURT and a.target_id == target)
                events.append(Event(round_num, turn, "pileon", "\U0001f3af",
                    f"Pile-on: {', '.join(attackers)} all hit {target}."))
        # Betrayal: hurts someone it helped earlier in the game.
        for a in this:
            if a.action == _HURT and a.target_id is not None:
                first_help = earliest_help.get((a.actor_id, a.target_id))
                if first_help is not None and first_help < (a.round, a.turn):
                    events.append(Event(round_num, turn, "betrayal", "⚔",
                        f"Betrayal: {a.actor_id} helped {a.target_id} earlier, now hurts it."))

    signals = compute_board_signals(players, actions, round_num)
    for al in signals.alliances:
        events.append(Event(round_num, turns[-1] if turns else 0, "alliance", "\U0001f91d",
            f"Alliance: {' ⇄ '.join(al.members)} help each other."))
    for agent in signals.surging:
        events.append(Event(round_num, turns[-1] if turns else 0, "surge", "\U0001f4c8",
            f"{agent} is surging up the round leaderboard."))
    if turns:
        events.append(Event(round_num, turns[0], "open", "·", "Round opens — scores reset to 0."))

    events.sort(key=lambda e: (-e.turn, e.kind))
    return events


# ---------- public API ----------


def season_overview(
    players: Sequence[PlayerRecord],
    actions: Sequence[ActionRecord],
    total_rounds: int,
    current_round: int,
    game_active: bool,
) -> SeasonOverview:
    """PD season overview = the game-agnostic skeleton plus grudges.

    Builds the relationship-free base (standings, results, tiebreaker, feed) once
    in the platform, then overlays only the PD-specific season signal: the
    season-long grudges read from HELP/HURT relationships.
    """
    base = default_season_overview(players, actions, total_rounds, current_round, game_active)
    grudge_list, grudge_total = grudges(actions)
    return replace(base, grudges=grudge_list, grudge_total=grudge_total)


def round_detail(
    round_num: int,
    players: Sequence[PlayerRecord],
    actions: Sequence[ActionRecord],
) -> RoundDetail:
    """PD round detail = the game-agnostic skeleton plus relationship signals.

    Builds the relationship-free base (leaderboard, intro, result, surge feed)
    once in the platform, then overlays only the PD-specific round signals read
    from HELP/HURT: cooperation mood, alliances, and the betrayal/pile-on event
    feed.
    """
    base = default_round_detail(round_num, players, actions)
    signals = compute_board_signals(players, actions, round_num)
    events = _round_events(round_num, players, actions)
    return replace(
        base,
        mood=signals.cooperation_temperature,
        mood_label=signals.temperature_label,
        alliances=[al.members for al in signals.alliances],
        events=events,
    )
