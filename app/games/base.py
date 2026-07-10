"""The Match plugin contract — the interface every turn-based game implements.

The platform (scheduler turn loop, agent API, viewer, lobby) depends only on
this `GameModule` protocol, never on a specific game. Games register themselves
in `app/games/__init__.py`, keyed by `game_type`. PD is the first module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from app.match_naming import humanize_game_type

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.engine.game_insights import RoundDetail, SeasonOverview
    from app.engine.game_records import ActionRecord, PlayerRecord
    from app.models.match import Match
    from app.models.player import Player
    from app.models.turn import Turn, TurnMessage, TurnSubmission
    from app.read_models.matches import TimelineTurn
    from app.schemas.agent import BoardSignals


class GameError(Exception):
    """Raised by a game module on an illegal move.

    `code` / `message` / `details` map straight onto the platform's standard
    error envelope, so a module owns its own validation errors while the
    platform stays game-agnostic.
    """

    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class GameConfig:
    """Default settings a module ships with (a game may be created overriding them)."""

    total_rounds: int
    turns_per_round: int
    per_turn_deadline_seconds: int
    min_players: int
    max_players: int
    simultaneous: bool = True
    # When True the game is hidden from non-admins everywhere a user could
    # encounter it (lobby, match lists, leaderboard, create/join) — used to keep a
    # game under construction out of sight until it's ready. Admins still see it.
    admin_only: bool = False


@dataclass(frozen=True)
class StrategyPreset:
    """A named starting strategy a game offers (a player picks one or writes their own)."""

    id: str
    name: str
    description: str
    prompt: str


@dataclass(frozen=True)
class GameTheme:
    """A game's color identity, layered *inside* the fixed platform shell.

    The platform chrome — nav, footer, brand, button shape, type scale — is the
    shared design language across every game and never reads these values. A
    game's pages stamp `data-game=<key>` on the content region (`<main>`) and
    the platform applies `vars` as scoped CSS custom properties there, so only
    that game's content takes the tint while the surrounding chrome stays
    constant. The color travels with the module: adding a game means returning a
    theme here, touching no shared CSS or template.

    `key` is the `data-game` value (use the module's `game_type`). `vars` maps
    CSS custom-property names to values (e.g. `{"--brand": "#e2640e"}`) — only
    content tokens (accents, semantic move colors, surfaces), never chrome.
    """

    key: str
    vars: dict[str, str]


class GameModule(Protocol):
    """The contract a turn-based game module implements."""

    game_type: str

    # Keys `validation_snapshot` merges into the move for `validate_move` only.
    # The shared submit path strips exactly these before `record_submission`, so
    # they never persist. A game with no validation-only keys declares none.
    validation_snapshot_keys: frozenset[str]

    def display_name(self) -> str:
        """The game's title as shown to people (catalog, leaderboard, headings)."""
        ...

    def tagline(self) -> str:
        """A one-line description shown under the title in the catalog."""
        ...

    def config_defaults(self) -> GameConfig: ...

    def action_names(self) -> tuple[str, ...]:
        """This game's action names, in the canonical display order.

        The read-side "insight" engines (opponent stats, board signals, season /
        round analysis) bucket the action log by these names. Returning them here
        keeps those engines from hardcoding one game's move vocabulary. The order
        is the order those engines present per-action tallies in."""
        ...

    def rules_text(self, total_rounds: int = 7, turns_per_round: int = 7) -> str: ...

    def semantic_rules_text(self, total_rounds: int = 7, turns_per_round: int = 7) -> str:
        """Game rules text without the connector's response protocol."""
        ...

    def mcp_setup_hint_lines(self) -> list[str]:
        """Extra setup lines for the MCP "## You" section. Default: none.

        Returned lines are appended verbatim under the agent-identity line in the
        MCP instructions (a game that hides per-player state uses this to point
        the agent at your_private_state). Keeps the platform's MCP layer from
        branching on a specific game id."""
        ...

    def strategy_presets(self) -> list[StrategyPreset]:
        """Named starting strategies offered to a player entering this game."""
        ...

    def default_strategy(self) -> str:
        """Strategy text a player's entry textarea is pre-filled with."""
        ...

    def agent_base_prompt(
        self,
        *,
        your_agent_id: str,
        all_agent_ids: list[str],
        total_rounds: int = 7,
        turns_per_round: int = 7,
    ) -> str:
        """Stable model instructions supplied separately from player strategy."""
        ...

    def validate_move(
        self, move: dict[str, Any], *, your_agent_id: str, all_agent_ids: list[str]
    ) -> None:
        """Raise GameError if `move` is illegal for this game. Pure (no DB)."""
        ...

    async def validation_snapshot(
        self,
        db: AsyncSession,
        match: Match,
        player: Player,
    ) -> dict[str, Any]:
        """Optional read-only state the submit route can merge into `move`."""
        ...

    async def record_submission(
        self,
        db: AsyncSession,
        turn: Turn,
        player: Player,
        move: dict[str, Any],
        *,
        existing: TurnSubmission | None,
        is_connector_fallback: bool = False,
    ) -> None:
        """Persist a validated move into the module's storage (create or replace).

        When `is_connector_fallback` is True the move was produced by the connector
        because the LLM subprocess failed; it should be stored with was_defaulted=True
        so it is distinguishable from a genuine agent decision.
        """
        ...

    async def record_message(
        self,
        db: AsyncSession,
        turn: Turn,
        player: Player,
        message: str,
        thinking: str,
        *,
        existing: TurnMessage | None,
        is_connector_fallback: bool = False,
    ) -> None:
        """Persist a validated talk-phase message into the module's storage.

        When `is_connector_fallback` is True the message was emitted because the
        LLM subprocess failed; it should be stored with was_defaulted=True.
        """
        ...

    async def resolve_turn(self, db: AsyncSession, turn: Turn) -> None: ...

    async def award_round(self, db: AsyncSession, game: Match, round_num: int) -> None: ...

    async def finalize(self, db: AsyncSession, game: Match) -> None: ...

    async def bot_move(self, db: AsyncSession, match: Match, player: Player) -> dict[str, Any]:
        """Move to submit for a bot actor."""
        ...

    def move_effect(self, action: str) -> tuple[int, int | None]:
        """Per-move display for the spectator viewer: (actor_delta, target_delta)."""
        ...

    def theme(self) -> GameTheme:
        """This game's content color identity (see `GameTheme`)."""
        ...

    # --- Loop progression (sequential games override; simultaneous games don't) ---

    async def next_actor(self, db: AsyncSession, match: Match) -> str | None:
        """For a sequential game, the seat_name of the single player to act now,
        or None when the current round is over. Simultaneous games never call this."""
        ...

    async def active_actors(
        self, db: AsyncSession, matches: Sequence[Match]
    ) -> dict[str, str | None]:
        """Batched `next_actor` for the turn-serving fan-out: match_id -> the
        seat_name owing a move right now (None = nobody owes one). Called only
        for sequential games; the implementation must batch its state reads
        across `matches` (the fan-out runs on every poll)."""
        ...

    async def on_round_start(self, db: AsyncSession, match: Match, round_num: int) -> None:
        """Set up a new round (e.g. deal dice). Default: nothing."""
        ...

    async def is_match_over(self, db: AsyncSession, match: Match) -> bool:
        """True when the match should finalize. Default: the fixed-grid end."""
        ...

    async def default_move(
        self, db: AsyncSession, match: Match, player: Player
    ) -> dict[str, Any]:
        """The move to record when a player misses its deadline."""
        ...

    # --- Player-facing payload (the contract owns "what a player sees") ---

    async def private_state_for(
        self, db: AsyncSession, match: Match, player: Player
    ) -> dict[str, Any]:
        """Per-player secret state for the turn payload. Default: none."""
        ...

    async def public_state_for(
        self, db: AsyncSession, match: Match, viewer: Player | None
    ) -> dict[str, Any]:
        """Game-rendered public state block for the payload/spectator. Default: none."""
        ...

    # --- Viewer / replay (the contract owns "how a match is replayed") ---

    async def build_replay_view(
        self,
        db: AsyncSession,
        match: Match,
        players: list[Player],
        scoreboard: list[dict[str, Any]],
        timeline: list[TimelineTurn],
        viewer_seat: str | None,
    ) -> dict[str, Any]:
        """Game-specific display payload merged into the viewer template context.

        The platform route loads the generic skeleton (players, scoreboard,
        timeline, messages); this returns the game's own replay data — for PD
        the enriched ``history`` (pacts/betrayals, per-move display, feed
        ordering/summary/groups, play-by-play headline) plus the robot-circle
        ``rc_data`` JSON; for another game whatever its fragments render."""
        ...

    def viewer_fragment(self) -> str:
        """Template path of this game's live-region feed fragment."""
        ...

    # --- Records / Elo ---

    async def final_placement(self, db: AsyncSession, match: Match) -> list[int]:
        """player_ids ranked best→worst for a completed match."""
        ...

    def match_placement_key(
        self, *, round_wins: float, total_score: int
    ) -> tuple[float, ...]:
        """Sort key (descending = better) ranking a completed match's participants
        for the shared rating engine; equal keys are a placement tie. Default:
        PD's (round_wins, total_score)."""
        ...

    # --- Spectator insights (the contract owns "what the analysis shows") ---

    def board_signals(
        self,
        players: Sequence[PlayerRecord],
        actions: Sequence[ActionRecord],
        current_round: int,
    ) -> BoardSignals:
        """Whole-board signals for the current round (mood, alliances, surging).

        These read a game's move *relationships*, so the game owns them. The
        platform default returns a neutral, relationship-free signal; PD overrides
        to add cooperation temperature and alliances."""
        ...

    def season_overview(
        self,
        players: Sequence[PlayerRecord],
        actions: Sequence[ActionRecord],
        total_rounds: int,
        current_round: int,
        game_active: bool,
    ) -> SeasonOverview:
        """The spectator season analysis: round-win race, results, grudges, feed.

        Default is the relationship-free skeleton (standings/results/feed); PD
        overrides to add grudges and alliances."""
        ...

    def round_detail(
        self,
        round_num: int,
        players: Sequence[PlayerRecord],
        actions: Sequence[ActionRecord],
    ) -> RoundDetail:
        """The spectator per-round analysis: leaderboard, mood, alliances, feed.

        Default is the relationship-free skeleton (leaderboard/intro/surge feed);
        PD overrides to add mood, alliances, betrayals, and pile-ons."""
        ...


class BaseGameModule:
    """Default implementations of the newer contract hooks, so a game only
    overrides what it needs and "the platform default" stays identical to PD.

    A game module subclasses this and implements the abstract members of
    `GameModule` (config, rules, validate_move, record_submission, resolve_turn,
    award_round, finalize, theme). The defaults here reproduce the simultaneous,
    public, fixed-grid behavior PD has always had — including a no-talk-phase
    `record_message` and a no-display `move_effect`. Games that need sequential
    turns, hidden state, a talk phase, per-move display, or a custom finish order
    override the relevant method.

    The PD module subclasses this; the conformance stub does not (it never drives
    the platform paths that call these hooks).
    """

    # Every concrete module sets this as a class attribute; declared here so the
    # shared defaults (e.g. display_name) can read it.
    game_type: str

    # Deliberately empty (not fail-loud): a game whose `validation_snapshot`
    # adds nothing to the move has nothing for the submit path to strip — so
    # "strip nothing" is the correct default, not a missing override. Games
    # that merge validation-only keys (e.g. Liar's Dice) override this.
    validation_snapshot_keys: frozenset[str] = frozenset()

    def display_name(self) -> str:
        # Default: humanize the game_type (e.g. "stub-game" -> "Stub Game").
        # Games with a stylized title (e.g. PD's "Hoard · Hurt · Help") override this.
        return humanize_game_type(self.game_type)

    def tagline(self) -> str:
        # Default: no tagline. Games override to describe themselves in the catalog.
        return ""

    def action_names(self) -> tuple[str, ...]:
        # No platform-wide default: a game's action vocabulary is game-specific, so
        # every module must declare its own (PD: HOARD/HELP/HURT; Liar's Dice:
        # BID/CHALLENGE). Failing loud here keeps a new game from silently
        # inheriting PD's move trio.
        raise NotImplementedError(
            "action_names is game-specific; each game module must override it."
        )

    async def record_message(
        self,
        db: AsyncSession,
        turn: Turn,
        player: Player,
        message: str,
        thinking: str,
        *,
        existing: TurnMessage | None,
        is_connector_fallback: bool = False,
    ) -> None:
        # Default: a game with no talk phase persists nothing. Games that have a
        # talk phase (e.g. PD) override this to store the message.
        return None

    def move_effect(self, action: str) -> tuple[int, int | None]:
        # Default: no per-move score effect to display in the viewer. Games whose
        # moves carry a nominal point value (e.g. PD) override this.
        return (0, None)

    def semantic_rules_text(
        self, total_rounds: int = 7, turns_per_round: int = 7
    ) -> str:
        # Default: no extra semantic rules block. Concrete games override this
        # to keep MCP instructions free of the connector's JSON protocol.
        return ""

    def mcp_setup_hint_lines(self) -> list[str]:
        # Deliberately empty: most games (e.g. PD, with no hidden per-player
        # state) need no extra setup hint in the MCP "## You" section, so an
        # empty list is the correct default — not a missing override. Games with
        # hidden state (e.g. Liar's Dice) override this to add their hint line.
        return []

    async def next_actor(self, db: AsyncSession, match: Match) -> str | None:
        # Simultaneous games resolve every player each turn — the scheduler does
        # not consult next_actor for them. Reaching here means a sequential loop
        # called a module that never declared one.
        raise NotImplementedError(
            "next_actor is only used by sequential games; override it."
        )

    async def active_actors(
        self, db: AsyncSession, matches: Sequence[Match]
    ) -> dict[str, str | None]:
        # The turn-serving fan-out consults this only for sequential games
        # (simultaneous games serve every seated player each turn). Reaching
        # here means a sequential module never declared who acts — fail loud
        # rather than serve a turn to a seat whose submit the game must reject.
        raise NotImplementedError(
            "active_actors is only used by sequential games; override it."
        )

    async def on_round_start(self, db: AsyncSession, match: Match, round_num: int) -> None:
        return None

    async def is_match_over(self, db: AsyncSession, match: Match) -> bool:
        return match.rounds_awarded >= match.total_rounds

    async def default_move(
        self, db: AsyncSession, match: Match, player: Player
    ) -> dict[str, Any]:
        # No platform-wide default: the move recorded when a player misses its
        # deadline is game-specific (PD records HOARD; Liar's Dice records the
        # minimal legal raise or a challenge). Failing loud here keeps a new game
        # from silently defaulting to a PD move it has no concept of.
        raise NotImplementedError(
            "default_move is game-specific; each game module must override it."
        )

    async def validation_snapshot(
        self,
        db: AsyncSession,
        match: Match,
        player: Player,
    ) -> dict[str, Any]:
        return {}

    async def private_state_for(
        self, db: AsyncSession, match: Match, player: Player
    ) -> dict[str, Any]:
        return {}

    async def public_state_for(
        self, db: AsyncSession, match: Match, viewer: Player | None
    ) -> dict[str, Any]:
        return {}

    async def build_replay_view(
        self,
        db: AsyncSession,
        match: Match,
        players: list[Player],
        scoreboard: list[dict[str, Any]],
        timeline: list[TimelineTurn],
        viewer_seat: str | None,
    ) -> dict[str, Any]:
        # No platform-wide default: the replay payload (history shape, any
        # narrative, the rc_data blob) is game-specific. Failing loud here keeps
        # a new game from silently inheriting PD's pact/betrayal story.
        raise NotImplementedError(
            "build_replay_view is game-specific; each game module must override it."
        )

    def viewer_fragment(self) -> str:
        # No platform-wide default: a game's live-region feed fragment is its
        # own. Failing loud keeps a new game from silently rendering PD's feed.
        raise NotImplementedError(
            "viewer_fragment is game-specific; each game module must override it."
        )

    async def bot_move(self, db: AsyncSession, match: Match, player: Player) -> dict[str, Any]:
        return await self.default_move(db, match, player)

    async def final_placement(self, db: AsyncSession, match: Match) -> list[int]:
        # PD's existing order: most round-wins, then highest total in-round
        # score — the same shared key finalize_game picks the winner with, so
        # placement and winner can't diverge.
        from sqlalchemy import select

        from app.engine.resolver import finish_order_sort_key
        from app.models.player import Player as PlayerModel

        players = list(
            (
                await db.execute(
                    select(PlayerModel).where(PlayerModel.match_id == match.id)
                )
            )
            .scalars()
            .all()
        )
        ranked = sorted(players, key=finish_order_sort_key)
        return [p.id for p in ranked]

    def match_placement_key(
        self, *, round_wins: float, total_score: int
    ) -> tuple[float, ...]:
        return (round_wins, float(total_score))

    # --- Spectator insights ---

    def board_signals(
        self,
        players: Sequence[PlayerRecord],
        actions: Sequence[ActionRecord],
        current_round: int,
    ) -> BoardSignals:
        # Default: a neutral, relationship-free signal — no cooperation
        # temperature and no alliances (those need a game's HELP/HURT model), but
        # the score-derived surge list still applies. PD overrides to add the rest.
        from app.engine.game_insights import detect_surging
        from app.schemas.agent import BoardSignals

        round_actions = [a for a in actions if a.round == current_round]
        return BoardSignals(
            alliances=[],
            cooperation_temperature=0.5,
            temperature_label="mixed",
            surging=detect_surging(players, round_actions),
        )

    def season_overview(
        self,
        players: Sequence[PlayerRecord],
        actions: Sequence[ActionRecord],
        total_rounds: int,
        current_round: int,
        game_active: bool,
    ) -> SeasonOverview:
        # Default: the relationship-free season skeleton. PD overrides to add
        # grudges/alliances.
        from app.engine.game_insights import default_season_overview

        return default_season_overview(
            players, actions, total_rounds, current_round, game_active
        )

    def round_detail(
        self,
        round_num: int,
        players: Sequence[PlayerRecord],
        actions: Sequence[ActionRecord],
    ) -> RoundDetail:
        # Default: the relationship-free per-round skeleton. PD overrides to add
        # mood/alliances/betrayals/pile-ons.
        from app.engine.game_insights import default_round_detail

        return default_round_detail(round_num, players, actions)
