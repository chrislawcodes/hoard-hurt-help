"""Shared shapes + game-agnostic skeleton for spectator insights.

The human-facing analysis (season overview + per-round detail) has two layers:

- A **game-agnostic skeleton** — round-win standings, round results, the
  leaderboard-from-0, the round intro, and the season feed of results. These read
  only scores and round-wins, so they belong to the platform and live here. They
  also drive the `BaseGameModule` defaults, so a game with no relationship model
  (anything but PD) still gets a coherent analysis page.

- A **game-specific enrichment** — feuds, grudges, alliances, cooperation mood,
  betrayals, pile-ons. Those read a game's move *relationships* (PD's HELP/HURT),
  so they live in the game module and reach the platform only through
  `GameModule.season_overview(...)` / `round_detail(...)`. The PD enrichment is in
  `app/games/hoard_hurt_help/insights.py`.

`detect_surging` is score-derived (a rank climb), so it is game-agnostic and stays
here; both the default and the PD board-signal builder use it.

Two scopes, kept deliberately distinct:
- SEASON (carries across rounds): round-wins, round results, tiebreaker.
- ROUND (resets each round): leaderboard-from-0, the event feed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.engine.game_records import ActionRecord, PlayerRecord

SURGE_RANK_JUMP = 2
SURGE_WINDOW = 3
MAX_SURGING = 2


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


# ---------- game-agnostic helpers (scores + round-wins only) ----------


def round_final_scores(round_num: int, actions: Sequence[ActionRecord]) -> dict[str, int]:
    """Each player's round score at their last submission in the round."""
    last: dict[str, tuple[int, int]] = {}  # agent -> (turn, round_score_after)
    for a in actions:
        if a.round != round_num:
            continue
        if a.actor_id not in last or a.turn > last[a.actor_id][0]:
            last[a.actor_id] = (a.turn, a.round_score_after)
    return {agent: score for agent, (_, score) in last.items()}


def round_winner(round_num: int, actions: Sequence[ActionRecord]) -> RoundResult | None:
    scores = round_final_scores(round_num, actions)
    if not scores:
        return None
    top = max(scores.values())
    winners = sorted(a for a, s in scores.items() if s == top)
    name = winners[0] if len(winners) == 1 else " / ".join(winners)
    return RoundResult(round=round_num, winner=name, score=top, tie=len(winners) > 1)


def round_results(actions: Sequence[ActionRecord]) -> list[RoundResult]:
    out: list[RoundResult] = []
    for r in sorted({a.round for a in actions}):
        res = round_winner(r, actions)
        if res is not None:
            out.append(res)
    return out


def detect_surging(
    players: Sequence[PlayerRecord],
    round_actions: Sequence[ActionRecord],
) -> list[str]:
    """Agents whose rank improved by >= SURGE_RANK_JUMP over the last window.

    Score-derived (a rank climb), so it is game-agnostic — every game's standing
    is a score. Both the default round detail and the PD board-signal builder use
    it.
    """
    turns = sorted({a.turn for a in round_actions})
    if len(turns) < 2:
        return []
    now_turn = turns[-1]
    past_turn = turns[max(0, len(turns) - 1 - SURGE_WINDOW)]
    now_rank = _ranks_at_turn(players, round_actions, now_turn)
    past_rank = _ranks_at_turn(players, round_actions, past_turn)

    improvements: list[tuple[int, str]] = []
    for agent_id, now_r in now_rank.items():
        past_r = past_rank.get(agent_id)
        if past_r is None:
            continue
        jump = past_r - now_r  # positive == climbed toward rank 1
        if jump >= SURGE_RANK_JUMP:
            improvements.append((jump, agent_id))
    improvements.sort(key=lambda t: (-t[0], t[1]))
    return [agent_id for _, agent_id in improvements[:MAX_SURGING]]


def _ranks_at_turn(
    players: Sequence[PlayerRecord],
    round_actions: Sequence[ActionRecord],
    turn: int,
) -> dict[str, int]:
    """Reconstruct 1-based ranks using each player's round_score_after at <= turn."""
    score: dict[str, int] = {p.agent_id: 0 for p in players}
    latest_turn_seen: dict[str, int] = {}
    for a in round_actions:
        if a.turn <= turn and a.turn >= latest_turn_seen.get(a.actor_id, -1):
            score[a.actor_id] = a.round_score_after
            latest_turn_seen[a.actor_id] = a.turn
    ordered = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
    return {agent_id: i + 1 for i, (agent_id, _) in enumerate(ordered)}


# ---------- game-agnostic defaults (the BaseGameModule "no relationships" path) ----------


def default_season_overview(
    players: Sequence[PlayerRecord],
    actions: Sequence[ActionRecord],
    total_rounds: int,
    current_round: int,
    game_active: bool,
) -> SeasonOverview:
    """The relationship-free season overview every game gets by default.

    Round-win standings, round results, the tiebreaker watch, and a season feed of
    results — all score / round-win derived. No grudges or alliances (those need a
    game's relationship model; PD overrides to add them).
    """
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
        grudges=[],
        grudge_total=0,
        season_feed=season_feed,
        live_round=live_round,
    )


def default_round_detail(
    round_num: int,
    players: Sequence[PlayerRecord],
    actions: Sequence[ActionRecord],
) -> RoundDetail:
    """The relationship-free per-round detail every game gets by default.

    Leaderboard-from-0, the round intro, the round result, plus the score-derived
    surge feed and a neutral "mixed" mood (no HELP/HURT, so no cooperation
    temperature and no alliances; PD overrides to add them).
    """
    scores = round_final_scores(round_num, actions)
    all_ids = {p.agent_id for p in players} | set(scores)
    ranked = sorted(all_ids, key=lambda a: (-scores.get(a, 0), a))
    leaderboard = [RoundLeader(a, scores.get(a, 0), i + 1) for i, a in enumerate(ranked)]

    round_actions = [a for a in actions if a.round == round_num]
    turns = sorted({a.turn for a in round_actions})
    surging = detect_surging(players, round_actions)

    events: list[Event] = []
    for agent in surging:
        events.append(Event(round_num, turns[-1] if turns else 0, "surge", "\U0001f4c8",
            f"{agent} is surging up the round leaderboard."))
    if turns:
        events.append(Event(round_num, turns[0], "open", "·", "Round opens — scores reset to 0."))
    events.sort(key=lambda e: (-e.turn, e.kind))

    later_round_exists = any(a.round > round_num for a in actions)
    result = round_winner(round_num, actions) if later_round_exists else None

    prev = round_winner(round_num - 1, actions) if round_num > 1 else None
    if prev is not None:
        intro = f"Round {round_num} opened after {prev.winner} took round {round_num - 1} ({prev.score}). Scores reset to 0; grudges carry over."
    else:
        intro = f"Round {round_num} — the opening round. Everyone starts at 0."

    return RoundDetail(
        round=round_num,
        leaderboard=leaderboard,
        mood=0.5,
        mood_label="mixed",
        alliances=[],
        surging=surging,
        events=events,
        intro=intro,
        winner=result.winner if result else None,
        complete=result is not None,
    )
