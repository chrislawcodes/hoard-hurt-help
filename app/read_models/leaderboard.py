"""Read-side projection for the public global leaderboard."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from math import pow
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.games import known_types
from app.models.bot import Bot, BotKind
from app.models.match import GameState, Match
from app.models.player import Player

LeaderboardRatingMode = Literal["standard", "bonus"]
LeaderboardIncluded = Literal["agents", "sims", "all"]

LEADERBOARD_CUTOFF = datetime(2026, 6, 3, tzinfo=timezone.utc)
INITIAL_RATING = 1500.0
K_FACTOR = 24.0
FIRST_PLACE_WEIGHT = 1.2
_TEST_NAME_PREFIX = "prod smoke"


@dataclass(frozen=True)
class LeaderboardRow:
    """One ranked competitor inside a game section."""

    rank: int
    display_name: str
    rating: float
    match_count: int
    last_played_at: datetime | None
    is_sim: bool
    provisional: bool


@dataclass(frozen=True)
class LeaderboardSection:
    """A game-specific slice of the leaderboard."""

    game_type: str
    game_name: str
    rows: list[LeaderboardRow]
    match_count: int
    has_sims: bool


@dataclass(frozen=True)
class _Participant:
    bot_id: int
    display_name: str
    is_sim: bool
    round_wins: float
    total_score: int
    last_played_at: datetime


@dataclass(frozen=True)
class _MatchBundle:
    game_type: str
    match_id: str
    scheduled_start: datetime
    played_at: datetime
    participants: list[_Participant]
    has_sims: bool


@dataclass
class _CompetitorState:
    rating: float = INITIAL_RATING
    match_count: int = 0
    last_played_at: datetime | None = None
    is_sim: bool = False
    display_name: str = ""


def _game_display_name(game_type: str) -> str:
    if game_type == "hoard-hurt-help":
        return "Hoard · Hurt · Help"
    return game_type.replace("-", " ").title()


def _is_test_match(match_name: str) -> bool:
    return match_name.strip().lower().startswith(_TEST_NAME_PREFIX)


def _normalize_rating_mode(rating_mode: str) -> LeaderboardRatingMode:
    return "bonus" if rating_mode == "bonus" else "standard"


def _normalize_included(included: str) -> LeaderboardIncluded:
    if included == "sims":
        return "sims"
    if included == "all":
        return "all"
    return "agents"


def _is_included(is_sim: bool, included: LeaderboardIncluded) -> bool:
    if included == "all":
        return True
    if included == "sims":
        return is_sim
    return not is_sim


def _logistic_expected(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + pow(10.0, (rating_b - rating_a) / 400.0))


async def load_leaderboard_sections(
    db: AsyncSession,
    *,
    rating_mode: str = "standard",
    included: str = "agents",
) -> list[LeaderboardSection]:
    """Load game-grouped leaderboard sections from completed matches."""

    rating_mode_choice = _normalize_rating_mode(rating_mode)
    included_choice = _normalize_included(included)

    rows = (
        await db.execute(
            select(Match, Player, Bot)
            .join(Player, Player.match_id == Match.id)
            .join(Bot, Bot.id == Player.bot_id)
            .where(
                Match.state == GameState.COMPLETED,
                Match.scheduled_start >= LEADERBOARD_CUTOFF,
            )
            .order_by(Match.game, Match.scheduled_start, Match.id, Player.id)
        )
    ).all()

    match_groups: dict[str, _MatchBundle] = {}
    skipped_matches: set[str] = set()
    for match, player, bot in rows:
        if match.id in skipped_matches:
            continue
        if _is_test_match(match.name):
            skipped_matches.add(match.id)
            match_groups.pop(match.id, None)
            continue
        bundle = match_groups.get(match.id)
        if bundle is None:
            match_groups[match.id] = _MatchBundle(
                game_type=match.game,
                match_id=match.id,
                scheduled_start=match.scheduled_start,
                played_at=match.completed_at or match.started_at or match.scheduled_start,
                participants=[],
                has_sims=bot.kind == BotKind.SIM,
            )
            bundle = match_groups[match.id]
        match_groups[match.id] = _MatchBundle(
            game_type=bundle.game_type,
            match_id=bundle.match_id,
            scheduled_start=bundle.scheduled_start,
            played_at=bundle.played_at,
            participants=[
                *bundle.participants,
                _Participant(
                    bot_id=bot.id,
                    display_name=bot.sim_profile_name or bot.name,
                    is_sim=bot.kind == BotKind.SIM,
                    round_wins=float(player.total_round_wins),
                    total_score=player.total_round_score,
                    last_played_at=match.completed_at or match.started_at or match.scheduled_start,
                ),
            ],
            has_sims=bundle.has_sims or bot.kind == BotKind.SIM,
        )

    games: dict[str, list[_MatchBundle]] = defaultdict(list)
    for bundle in match_groups.values():
        games[bundle.game_type].append(bundle)

    ordered_game_types = [game_type for game_type in known_types() if game_type in games]
    ordered_game_types.extend(sorted(game_type for game_type in games if game_type not in ordered_game_types))

    sections: list[LeaderboardSection] = []
    for game_type in ordered_game_types:
        bundles = sorted(
            games[game_type],
            key=lambda bundle: (bundle.scheduled_start, bundle.match_id),
        )
        states: dict[int, _CompetitorState] = {}
        contributed_matches = 0
        has_sims = any(bundle.has_sims for bundle in bundles)

        for bundle in bundles:
            participants = [
                participant for participant in bundle.participants if _is_included(participant.is_sim, included_choice)
            ]
            if len(participants) < 2:
                continue

            contributed_matches += 1
            group_keys = sorted(
                {
                    (participant.round_wins, participant.total_score)
                    for participant in participants
                },
                reverse=True,
            )
            placement_groups: list[list[_Participant]] = []
            for round_wins, total_score in group_keys:
                placement_groups.append(
                    sorted(
                        [
                            participant
                            for participant in participants
                            if participant.round_wins == round_wins
                            and participant.total_score == total_score
                        ],
                        key=lambda participant: participant.bot_id,
                    )
                )

            group_index_by_bot: dict[int, int] = {
                participant.bot_id: index
                for index, group in enumerate(placement_groups)
                for participant in group
            }
            first_place_bot_ids = {
                participant.bot_id for participant in placement_groups[0]
            }
            start_ratings = {
                participant.bot_id: states.get(participant.bot_id, _CompetitorState()).rating
                for participant in participants
            }
            deltas = {participant.bot_id: 0.0 for participant in participants}
            opponent_counts = {participant.bot_id: 0 for participant in participants}

            for index, left in enumerate(participants):
                for right in participants[index + 1 :]:
                    left_group = group_index_by_bot[left.bot_id]
                    right_group = group_index_by_bot[right.bot_id]
                    if left_group == right_group:
                        left_score = 0.5
                        weight = 1.0
                    elif left_group < right_group:
                        left_score = 1.0
                        weight = (
                            FIRST_PLACE_WEIGHT
                            if rating_mode_choice == "bonus" and left.bot_id in first_place_bot_ids
                            else 1.0
                        )
                    else:
                        left_score = 0.0
                        weight = (
                            FIRST_PLACE_WEIGHT
                            if rating_mode_choice == "bonus" and right.bot_id in first_place_bot_ids
                            else 1.0
                        )

                    expected_left = _logistic_expected(
                        start_ratings[left.bot_id],
                        start_ratings[right.bot_id],
                    )
                    delta = K_FACTOR * weight * (left_score - expected_left)
                    deltas[left.bot_id] += delta
                    deltas[right.bot_id] -= delta
                    opponent_counts[left.bot_id] += 1
                    opponent_counts[right.bot_id] += 1

            for participant in participants:
                state = states.get(participant.bot_id)
                current_rating = start_ratings[participant.bot_id]
                match_delta = deltas[participant.bot_id] / opponent_counts[participant.bot_id]
                if state is None:
                    state = _CompetitorState(
                        rating=current_rating,
                        match_count=0,
                        last_played_at=None,
                        is_sim=participant.is_sim,
                        display_name=participant.display_name,
                    )
                state.rating = current_rating + match_delta
                state.match_count += 1
                state.last_played_at = max(
                    state.last_played_at or participant.last_played_at,
                    participant.last_played_at,
                )
                state.is_sim = participant.is_sim
                state.display_name = participant.display_name
                states[participant.bot_id] = state

        if not states:
            continue

        ranked_states = sorted(
            states.items(),
            key=lambda item: (-item[1].rating, -item[1].match_count, item[1].display_name.lower(), item[0]),
        )
        rows_out: list[LeaderboardRow] = []
        previous_rating: float | None = None
        current_rank = 0
        for position, (_, state) in enumerate(ranked_states, start=1):
            if previous_rating is None or abs(state.rating - previous_rating) > 1e-9:
                current_rank = position
                previous_rating = state.rating
            rows_out.append(
                LeaderboardRow(
                    rank=current_rank,
                    display_name=state.display_name,
                    rating=state.rating,
                    match_count=state.match_count,
                    last_played_at=state.last_played_at,
                    is_sim=state.is_sim,
                    provisional=state.match_count < 5,
                )
            )

        sections.append(
            LeaderboardSection(
                game_type=game_type,
                game_name=_game_display_name(game_type),
                rows=rows_out,
                match_count=contributed_matches,
                has_sims=has_sims,
            )
        )

    return sections
