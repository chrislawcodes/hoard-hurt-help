"""Read-side projection for the public global leaderboard."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from math import pow
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.games import GameError
from app.games import get as get_game_module
from app.games import known_types
from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.user import User
from app.match_naming import humanize_game_type, is_smoke_test_match_name
from app.provider_labels import provider_label
from app.read_models.agent_display import agent_display_name

LeaderboardRatingMode = Literal["standard", "bonus"]
LeaderboardIncluded = Literal["agents", "bot", "all"]

LEADERBOARD_CUTOFF = datetime(2026, 6, 3, tzinfo=timezone.utc)
INITIAL_RATING = 1500.0
K_FACTOR = 24.0
FIRST_PLACE_WEIGHT = 1.2
@dataclass(frozen=True)
class LeaderboardRow:
    """One ranked competitor inside a game section."""

    rank: int
    display_name: str
    # The owner's public handle, shown as "by @handle". None for bots and for
    # agents whose owner has not picked a handle yet.
    owner_handle: str | None
    rating: float
    match_count: int
    last_played_at: datetime | None
    is_bot: bool
    provisional: bool
    is_archived: bool
    archived_at: datetime | None
    # Provider that played this agent's most recent match (Claude/Gemini/…), shown
    # as a badge. None for bots and for agents not yet served by a connection.
    provider: str | None = None


@dataclass(frozen=True)
class LeaderboardSection:
    """A game-specific slice of the leaderboard."""

    game_type: str
    game_name: str
    rows: list[LeaderboardRow]
    match_count: int
    has_bots: bool


@dataclass(frozen=True)
class _Participant:
    # For bots and agents: str(agent.id).
    competitor_key: str
    display_name: str
    owner_handle: str | None
    is_bot: bool
    is_archived: bool
    archived_at: datetime | None
    round_wins: float
    total_score: int
    last_played_at: datetime
    provider: str | None = None


@dataclass(frozen=True)
class _MatchBundle:
    game_type: str
    match_id: str
    scheduled_start: datetime
    played_at: datetime
    participants: list[_Participant]
    has_bots: bool


@dataclass
class _CompetitorState:
    rating: float = INITIAL_RATING
    match_count: int = 0
    last_played_at: datetime | None = None
    is_bot: bool = False
    is_archived: bool = False
    archived_at: datetime | None = None
    display_name: str = ""
    owner_handle: str | None = None
    provider: str | None = None


def _agent_display_name(agent: Agent, version: AgentVersion | None) -> str:
    return agent_display_name(agent, version)


def _competitor_key(agent: Agent) -> str:
    """Leaderboard grouping key. Preset bots are re-seated as a fresh per-match
    agent each game, so group them by their stable profile (as the old bot board
    did) rather than the throwaway per-match agent id. AI agents are persistent
    competitors, so they key by their own id."""
    if agent.kind == AgentKind.BOT:
        return f"bot:{agent.bot_profile_id or agent.bot_profile_name or agent.id}"
    return str(agent.id)


def _merge_same_key_participants(participants: list[_Participant]) -> list[_Participant]:
    """Merge duplicate competitor keys within one match."""
    grouped: dict[str, list[_Participant]] = {}
    for p in participants:
        grouped.setdefault(p.competitor_key, []).append(p)

    merged: list[_Participant] = []
    for key, group in grouped.items():
        if len(group) == 1:
            merged.append(group[0])
        else:
            avg_wins = sum(p.round_wins for p in group) / len(group)
            avg_score = sum(p.total_score for p in group) // len(group)
            latest_participant = max(group, key=lambda p: p.last_played_at)
            merged.append(
                _Participant(
                    competitor_key=key,
                    display_name=group[0].display_name,
                    owner_handle=group[0].owner_handle,
                    is_bot=group[0].is_bot,
                    is_archived=group[0].is_archived,
                    archived_at=group[0].archived_at,
                    round_wins=avg_wins,
                    total_score=avg_score,
                    last_played_at=latest_participant.last_played_at,
                    provider=latest_participant.provider,
                )
            )
    return merged


def _game_display_name(game_type: str) -> str:
    # The display title is owned by the game module. Unregistered (legacy) game
    # types have no module, so fall back to the humanized type — the same fallback
    # the section ordering and placement-key lookup above already use.
    try:
        return get_game_module(game_type).display_name()
    except GameError:
        return humanize_game_type(game_type)


def _normalize_rating_mode(rating_mode: str) -> LeaderboardRatingMode:
    return "bonus" if rating_mode == "bonus" else "standard"


def _normalize_included(included: str) -> LeaderboardIncluded:
    if included in {"bot", "bots", "sims"}:
        return "bot"
    if included == "all":
        return "all"
    return "agents"


def _is_included(is_bot: bool, included: LeaderboardIncluded) -> bool:
    if included == "all":
        return True
    if included == "bot":
        return is_bot
    return not is_bot


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
            select(Match, Player, Agent, AgentVersion, User)
            .join(Player, Player.match_id == Match.id)
            .join(Agent, Agent.id == Player.agent_id)
            .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
            .join(User, User.id == Agent.user_id)
            .where(
                Match.state == GameState.COMPLETED,
                Match.scheduled_start >= LEADERBOARD_CUTOFF,
            )
            .order_by(Match.game, Match.scheduled_start, Match.id, Player.id)
        )
    ).all()

    match_groups: dict[str, _MatchBundle] = {}
    skipped_matches: set[str] = set()
    for match, player, agent, version, user in rows:
        if match.id in skipped_matches:
            continue
        if is_smoke_test_match_name(match.name):
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
                has_bots=agent.kind == AgentKind.BOT,
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
                    competitor_key=_competitor_key(agent),
                    display_name=_agent_display_name(agent, version),
                    owner_handle=None if agent.kind == AgentKind.BOT else user.handle,
                    is_bot=agent.kind == AgentKind.BOT,
                    is_archived=agent.archived_at is not None,
                    archived_at=agent.archived_at,
                    round_wins=float(player.total_round_wins),
                    total_score=player.total_round_score,
                    last_played_at=match.completed_at or match.started_at or match.scheduled_start,
                    provider=None if agent.kind == AgentKind.BOT else player.played_provider,
                ),
            ],
            has_bots=bundle.has_bots or agent.kind == AgentKind.BOT,
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
        states: dict[str, _CompetitorState] = {}
        contributed_matches = 0
        has_bots = any(bundle.has_bots for bundle in bundles)

        # Placement is per-game (shared rating math, per-game finish order). PD's
        # key is (round_wins, total_score); a game overrides match_placement_key to
        # rank its own way. Unregistered legacy game types fall back to the default.
        try:
            placement_key = get_game_module(game_type).match_placement_key
        except GameError:
            def placement_key(*, round_wins: float, total_score: int) -> tuple[float, ...]:
                return (round_wins, float(total_score))

        for bundle in bundles:
            participants = _merge_same_key_participants(
                [p for p in bundle.participants if _is_included(p.is_bot, included_choice)]
            )
            if len(participants) < 2:
                continue

            contributed_matches += 1
            keys_by_competitor = {
                participant.competitor_key: placement_key(
                    round_wins=participant.round_wins, total_score=participant.total_score
                )
                for participant in participants
            }
            group_keys = sorted(set(keys_by_competitor.values()), reverse=True)
            placement_groups: list[list[_Participant]] = []
            for gkey in group_keys:
                placement_groups.append(
                    sorted(
                        [
                            participant
                            for participant in participants
                            if keys_by_competitor[participant.competitor_key] == gkey
                        ],
                        key=lambda participant: participant.competitor_key,
                    )
                )

            group_index_by_key: dict[str, int] = {
                participant.competitor_key: index
                for index, group in enumerate(placement_groups)
                for participant in group
            }
            first_place_keys = {
                participant.competitor_key for participant in placement_groups[0]
            }
            start_ratings = {
                participant.competitor_key: states.get(participant.competitor_key, _CompetitorState()).rating
                for participant in participants
            }
            deltas = {participant.competitor_key: 0.0 for participant in participants}
            opponent_counts = {participant.competitor_key: 0 for participant in participants}

            for index, left in enumerate(participants):
                for right in participants[index + 1:]:
                    left_group = group_index_by_key[left.competitor_key]
                    right_group = group_index_by_key[right.competitor_key]
                    if left_group == right_group:
                        left_score = 0.5
                        weight = 1.0
                    elif left_group < right_group:
                        left_score = 1.0
                        weight = (
                            FIRST_PLACE_WEIGHT
                            if rating_mode_choice == "bonus" and left.competitor_key in first_place_keys
                            else 1.0
                        )
                    else:
                        left_score = 0.0
                        weight = (
                            FIRST_PLACE_WEIGHT
                            if rating_mode_choice == "bonus" and right.competitor_key in first_place_keys
                            else 1.0
                        )

                    expected_left = _logistic_expected(
                        start_ratings[left.competitor_key],
                        start_ratings[right.competitor_key],
                    )
                    delta = K_FACTOR * weight * (left_score - expected_left)
                    deltas[left.competitor_key] += delta
                    deltas[right.competitor_key] -= delta
                    opponent_counts[left.competitor_key] += 1
                    opponent_counts[right.competitor_key] += 1

            for participant in participants:
                state = states.get(participant.competitor_key)
                current_rating = start_ratings[participant.competitor_key]
                match_delta = deltas[participant.competitor_key] / opponent_counts[participant.competitor_key]
                if state is None:
                    state = _CompetitorState(
                        rating=current_rating,
                        match_count=0,
                        last_played_at=None,
                        is_bot=participant.is_bot,
                        is_archived=participant.is_archived,
                        archived_at=participant.archived_at,
                        display_name=participant.display_name,
                        owner_handle=participant.owner_handle,
                    )
                state.rating = current_rating + match_delta
                state.match_count += 1
                # Keep the provider from the agent's most recent *served* match —
                # a later match that no connection ever played (NULL provider)
                # must not wipe an earlier real badge.
                if participant.provider is not None and (
                    state.last_played_at is None
                    or participant.last_played_at >= state.last_played_at
                ):
                    state.provider = participant.provider
                state.last_played_at = max(
                    state.last_played_at or participant.last_played_at,
                    participant.last_played_at,
                )
                state.is_bot = participant.is_bot
                state.is_archived = participant.is_archived
                state.archived_at = participant.archived_at
                state.display_name = participant.display_name
                state.owner_handle = participant.owner_handle
                states[participant.competitor_key] = state

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
                    owner_handle=state.owner_handle,
                    rating=state.rating,
                    match_count=state.match_count,
                    last_played_at=state.last_played_at,
                    is_bot=state.is_bot,
                    provisional=state.match_count < 5,
                    is_archived=state.is_archived,
                    archived_at=state.archived_at,
                    provider=(
                        provider_label(state.provider) if state.provider else None
                    ),
                )
            )

        sections.append(
            LeaderboardSection(
                game_type=game_type,
                game_name=_game_display_name(game_type),
                rows=rows_out,
                match_count=contributed_matches,
                has_bots=has_bots,
            )
        )

    return sections
