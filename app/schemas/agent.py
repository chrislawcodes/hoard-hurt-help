"""Pydantic schemas for the Agent API.

These shapes are documented in SPEC.md §1.1 and contracts/api.yaml.

Feature 002 (bot-state-summary): the `get_turn` payload now returns a bounded
`summary` (TurnSummary) instead of the full per-turn history. The heavy detail
moved behind the pull endpoints, whose response shapes live at the bottom of
this file.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# PD's (game #1, "hoard-hurt-help") move vocabulary. The platform does NOT
# interpret these — POST /submit packs the request into a generic `move` dict and
# routes validation/recording through that game's module (app/games/). A second
# game ships its own move shape; full free-form move JSON on the wire is deferred
# to game #2 (see specs/004-game-framework, plan Decision: storage/wire
# generalization rides with the second game).
Action = Literal["HOARD", "HELP", "HURT"]


# --- Join ---


class JoinRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_]+$")
    strategy_prompt: str = Field(min_length=1, max_length=2000)
    model_self_report: str | None = Field(default=None, max_length=200)


class JoinResponse(BaseModel):
    match_id: str
    agent_id: str
    agent_key: str
    poll_url: str
    submit_url: str
    scheduled_start: datetime
    per_turn_deadline_seconds: int


# --- Poll response shapes ---


class WaitingResponse(BaseModel):
    status: Literal["waiting"] = "waiting"
    reason: Literal["turn_not_open", "already_submitted", "game_not_started", "game_over"]
    game_state: str
    current_round: int = 0
    current_turn: int = 0
    next_poll_after_seconds: int = 2


class ScoreboardRow(BaseModel):
    agent_id: str
    round_score: int
    round_wins: float


class TurnStatic(BaseModel):
    match_id: str
    rules_version: str
    rules: str
    total_rounds: int
    turns_per_round: int
    your_agent_id: str
    all_agent_ids: list[str]
    your_strategy: str | None = None


# --- Free summary (the bounded push payload) ---


class YourSituation(BaseModel):
    round_score: int
    total_score: int
    round_wins: float
    rank: int
    current_round: int
    current_turn: int
    deadline: datetime
    turn_token: str


class StandingRow(BaseModel):
    agent_id: str
    round_score: int
    rank: int


class StandingsView(BaseModel):
    leaders: list[StandingRow]
    your_rank: int
    neighbors: list[StandingRow]
    total_players: int


class DeltaAction(BaseModel):
    actor_id: str
    action: Action
    target_id: str | None
    points_delta: int


class TurnDelta(BaseModel):
    round: int
    turn: int
    involving_you: list[DeltaAction]
    others_summary: str


class StyleMix(BaseModel):
    hoard_pct: int
    help_pct: int
    hurt_pct: int


class OpponentStat(BaseModel):
    agent_id: str
    round_score: int
    helped_you: int
    hurt_you: int
    returned_help: bool
    returned_hurt: bool
    style: StyleMix
    reason: Literal["interacted", "threat", "neighbor", "flagged"]


class OpponentsAggregate(BaseModel):
    count: int
    hoard: int
    help: int
    hurt: int


class Alliance(BaseModel):
    members: list[str]
    strength: int


class BoardSignals(BaseModel):
    alliances: list[Alliance]
    cooperation_temperature: float
    temperature_label: Literal["hostile", "mixed", "cooperative"]
    surging: list[str]


class SummaryFlags(BaseModel):
    pattern_breaks: list[str]
    new_alliance: bool
    messages_for_you_count: int


class DirectedMessage(BaseModel):
    from_agent_id: str
    message: str
    on_action: str | None
    public: bool


class TurnSummary(BaseModel):
    your_situation: YourSituation
    standings_view: StandingsView
    # None only on the very first turn of the game (no prior resolved turn).
    turn_delta: TurnDelta | None
    opponents: list[OpponentStat]
    opponents_aggregate: OpponentsAggregate | None
    board_signals: BoardSignals
    flags: SummaryFlags
    messages_for_you: list[DirectedMessage]


# --- Shared history shapes (used by the bot payload, spectator view, and pulls) ---


class HistoryAction(BaseModel):
    agent_id: str
    action: Action
    target_id: str | None
    message: str
    points_delta: int


class HistoryTurn(BaseModel):
    round: int
    turn: int
    actions: list[HistoryAction]


class TalkMessage(BaseModel):
    agent_id: str
    message: str


class CurrentTurn(BaseModel):
    """Per-poll volatile fields. Kept last so everything before it is a stable,
    append-only prefix an agent's client can prompt-cache."""

    round: int
    turn: int
    deadline: datetime
    turn_token: str
    phase: Literal["talk", "act"] = "act"
    talk_messages: list[TalkMessage] = Field(default_factory=list)


class YourTurnResponse(BaseModel):
    # Field order is intentional and cache-friendly: `static` (rules — constant)
    # and `history` (append-only) form a stable prefix; only `scoreboard` and
    # `current` change each turn, so they come last. Nothing is pre-digested —
    # the agent reads the raw moves and messages and does its own analysis.
    status: Literal["your_turn"] = "your_turn"
    static: TurnStatic
    history: list[HistoryTurn]
    scoreboard: list[ScoreboardRow]
    current: CurrentTurn


class GameCompletedResponse(BaseModel):
    status: Literal["game_completed"] = "game_completed"
    winner_agent_id: str | None
    final_standings: list[dict]


# --- Next-turn (game-agnostic loop) response shapes ---
# A bot connects once and calls get_next_turn across ALL its games, so these
# carry a top-level match_id and a wider set of waiting reasons than the
# per-game poll above.


class NextTurnWaiting(BaseModel):
    status: Literal["waiting"] = "waiting"
    reason: Literal["no_open_turns", "no_active_games", "bot_paused"]
    next_poll_after_seconds: int = 5


class NextTurnYourTurn(BaseModel):
    # Same raw payload as YourTurnResponse, plus the match_id (a bot using the
    # loop isn't tracking which game it's in).
    status: Literal["your_turn"] = "your_turn"
    match_id: str
    static: TurnStatic
    history: list[HistoryTurn]
    scoreboard: list[ScoreboardRow]
    current: CurrentTurn
    # The owner's configured provider and model. The runner uses these to pick the
    # right CLI when no --model flag was passed. NULL = not configured by the owner.
    preferred_provider: str | None = None
    preferred_model: str | None = None


# --- Submit ---


class SubmitRequest(BaseModel):
    turn_token: str
    action: Action
    target_id: str | None = None
    message: str = Field(default="", max_length=200)
    thinking: str = Field(default="", max_length=200)


class MessageRequest(BaseModel):
    turn_token: str
    message: str = Field(default="", max_length=200)
    thinking: str = Field(default="", max_length=200)


class MessageResponse(BaseModel):
    status: Literal["accepted"] = "accepted"
    received_at: datetime
    phase_resolves_at: datetime


class SubmitResponse(BaseModel):
    status: Literal["accepted"] = "accepted"
    received_at: datetime
    turn_will_resolve_at: datetime


# --- State (agent-flavored) ---


class AgentStateResponse(BaseModel):
    match_id: str
    game_state: str
    current_round: int
    current_turn: int
    deadline: datetime | None
    you_have_submitted_current_turn: bool
    scoreboard: list[ScoreboardRow]
    all_agent_ids: list[str]


# --- Leave ---


class LeaveResponse(BaseModel):
    status: Literal["left"] = "left"
    game_state: str
    effective_at: datetime


# --- Pull detail shapes (opt-in; fetched only on demand) ---


class OpponentHistoryResponse(BaseModel):
    opponent_id: str
    turns: list[HistoryTurn]


class ChatLine(BaseModel):
    round: int
    turn: int
    from_agent_id: str
    target_id: str | None
    message: str


class ChatTranscriptResponse(BaseModel):
    since: str | None
    messages: list[ChatLine]
    next_cursor: str | None


class TurnDetailResponse(BaseModel):
    round: int
    turn: int
    actions: list[HistoryAction]


class FullStandingsResponse(BaseModel):
    rows: list[StandingRow]
    total_players: int
