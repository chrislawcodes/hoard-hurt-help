"""Deterministic spectator insights: season overview + per-round detail.

Turns the resolved action log into the human-facing analysis — the round-win
race, round results, grudges, and a per-round event feed. No AI and no
message-text reading: everything is derived from actions. Reuses
`board_signals` for per-round mood / alliances / surging.

Two scopes, kept deliberately distinct:
- SEASON (carries across rounds): round-wins, round results, grudges, tiebreaker.
- ROUND (resets each round): leaderboard-from-0, mood, alliances, the event feed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.engine.action_vocab import pd_action_names
from app.engine.board_signals import compute_board_signals
from app.engine.game_records import ActionRecord, PlayerRecord

GRUDGE_CAP = 6
MIN_FEUD_HITS = 2
MIN_ALLY_HELPS = 2
MIN_VENDETTA_HITS = 2
MIN_ONESIDED_HELPS = 2
PILE_ON_MIN = 2


# ---------- shapes (plain dataclasses; templates read attributes) ----------


@dataclass
class StandingRow:
    agent_id: str
    round_wins: float
    total_score: int
    rank: int


@dataclass
class RoundResult:
    round: int
    winner: str
    score: int
    tie: bool


@dataclass
class Grudge:
    kind: str  # feud | alliance | vendetta | one_sided
    icon: str
    text: str
    weight: int


@dataclass
class Event:
    round: int
    turn: int
    kind: str  # open | mood | alliance | surge | pileon | betrayal | result
    icon: str
    text: str


@dataclass
class SeasonOverview:
    standings: list[StandingRow]
    results: list[RoundResult]
    rounds_played: list[int]
    total_rounds: int
    tiebreaker: str | None
    grudges: list[Grudge]
    grudge_total: int
    season_feed: list[Event]
    live_round: int | None


@dataclass
class RoundLeader:
    agent_id: str
    score: int
    rank: int


@dataclass
class RoundDetail:
    round: int
    leaderboard: list[RoundLeader]
    mood: float
    mood_label: str
    alliances: list[list[str]]
    surging: list[str]
    events: list[Event]
    intro: str
    winner: str | None
    complete: bool


# ---------- helpers ----------


def _round_final_scores(round_num: int, actions: Sequence[ActionRecord]) -> dict[str, int]:
    """Each player's round score at their last submission in the round."""
    last: dict[str, tuple[int, int]] = {}  # agent -> (turn, round_score_after)
    for a in actions:
        if a.round != round_num:
            continue
        if a.actor_id not in last or a.turn > last[a.actor_id][0]:
            last[a.actor_id] = (a.turn, a.round_score_after)
    return {agent: score for agent, (_, score) in last.items()}


def _round_winner(round_num: int, actions: Sequence[ActionRecord]) -> RoundResult | None:
    scores = _round_final_scores(round_num, actions)
    if not scores:
        return None
    top = max(scores.values())
    winners = sorted(a for a, s in scores.items() if s == top)
    name = winners[0] if len(winners) == 1 else " / ".join(winners)
    return RoundResult(round=round_num, winner=name, score=top, tie=len(winners) > 1)


def round_results(actions: Sequence[ActionRecord]) -> list[RoundResult]:
    out: list[RoundResult] = []
    for r in sorted({a.round for a in actions}):
        res = _round_winner(r, actions)
        if res is not None:
            out.append(res)
    return out


def _relationships(actions: Sequence[ActionRecord]) -> tuple[Counter[tuple[str, str]], Counter[tuple[str, str]]]:
    _, help_action, hurt_action = pd_action_names()
    helps: Counter[tuple[str, str]] = Counter()
    hurts: Counter[tuple[str, str]] = Counter()
    for a in actions:
        if a.target_id is None:
            continue
        if a.action == help_action:
            helps[(a.actor_id, a.target_id)] += 1
        elif a.action == hurt_action:
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
    _, help_action, _ = pd_action_names()
    earliest: dict[tuple[str, str], tuple[int, int]] = {}
    for a in actions:
        if a.action == help_action and a.target_id is not None:
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
    _, _, hurt_action = pd_action_names()
    earliest_help = _earliest_helps(actions)
    round_actions = [a for a in actions if a.round == round_num]
    turns = sorted({a.turn for a in round_actions})
    events: list[Event] = []

    for turn in turns:
        this = [a for a in round_actions if a.turn == turn]
        # Pile-on: 2+ HURTs on the same target this turn.
        hurt_targets: Counter[str] = Counter(
            a.target_id for a in this if a.action == hurt_action and a.target_id is not None
        )
        for target, n in sorted(hurt_targets.items()):
            if n >= PILE_ON_MIN:
                attackers = sorted(a.actor_id for a in this if a.action == hurt_action and a.target_id == target)
                events.append(Event(round_num, turn, "pileon", "\U0001f3af",
                    f"Pile-on: {', '.join(attackers)} all hit {target}."))
        # Betrayal: hurts someone it helped earlier in the game.
        for a in this:
            if a.action == hurt_action and a.target_id is not None:
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
    standings_sorted = sorted(players, key=lambda p: (-p.round_wins, -p.total_score, p.agent_id))
    standings = [
        StandingRow(p.agent_id, p.round_wins, p.total_score, i + 1)
        for i, p in enumerate(standings_sorted)
    ]
    results = round_results(actions)
    rounds_played = sorted({a.round for a in actions})

    # Tiebreaker watch: someone with the top total score but not the most wins.
    tiebreaker = None
    if standings:
        top_wins = standings[0].round_wins
        leaders_by_score = sorted(players, key=lambda p: (-p.total_score, p.agent_id))
        score_leader = leaders_by_score[0] if leaders_by_score else None
        if score_leader is not None and score_leader.round_wins < top_wins:
            tiebreaker = (
                f"{score_leader.agent_id} has the highest total score "
                f"({score_leader.total_score}) but fewer round-wins — wins the title on "
                f"the tiebreaker if the round-wins stay level."
            )

    grudge_list, grudge_total = grudges(actions)
    season_feed = [
        Event(r.round, 0, "result", "\U0001f3c6", f"Round {r.round}: {r.winner} wins ({r.score}).")
        for r in reversed(results)
    ]
    live_round = current_round if game_active else None
    return SeasonOverview(
        standings=standings,
        results=results,
        rounds_played=rounds_played,
        total_rounds=total_rounds,
        tiebreaker=tiebreaker,
        grudges=grudge_list,
        grudge_total=grudge_total,
        season_feed=season_feed,
        live_round=live_round,
    )


def round_detail(
    round_num: int,
    players: Sequence[PlayerRecord],
    actions: Sequence[ActionRecord],
) -> RoundDetail:
    scores = _round_final_scores(round_num, actions)
    all_ids = {p.agent_id for p in players} | set(scores)
    ranked = sorted(all_ids, key=lambda a: (-scores.get(a, 0), a))
    leaderboard = [RoundLeader(a, scores.get(a, 0), i + 1) for i, a in enumerate(ranked)]

    signals = compute_board_signals(players, actions, round_num)
    events = _round_events(round_num, players, actions)

    # Is this round finished? (a later round exists in the log)
    later_round_exists = any(a.round > round_num for a in actions)
    result = _round_winner(round_num, actions) if later_round_exists else None

    prev = _round_winner(round_num - 1, actions) if round_num > 1 else None
    if prev is not None:
        intro = f"Round {round_num} opened after {prev.winner} took round {round_num - 1} ({prev.score}). Scores reset to 0; grudges carry over."
    else:
        intro = f"Round {round_num} — the opening round. Everyone starts at 0."

    return RoundDetail(
        round=round_num,
        leaderboard=leaderboard,
        mood=signals.cooperation_temperature,
        mood_label=signals.temperature_label,
        alliances=[al.members for al in signals.alliances],
        surging=signals.surging,
        events=events,
        intro=intro,
        winner=result.winner if result else None,
        complete=result is not None,
    )
