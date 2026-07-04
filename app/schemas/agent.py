"""Pydantic schemas for the Agent API.

These shapes are documented in SPEC.md §1.1 and contracts/api.yaml.

Feature 002 (bot-state-summary): the next-turn payload now returns a bounded
`summary` (TurnSummary) instead of the full per-turn history. The heavy detail
moved behind the pull endpoints, whose response shapes live at the bottom of
this file.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from app.agent_prompt import MESSAGE_MAX_LENGTH, THINKING_MAX_LENGTH


def _drop_none_keys(data: dict, keys: tuple[str, ...]) -> dict:
    """Omit the named keys when their value is None.

    The one helper behind every "this key must be absent, not null" wrap
    serializer in this module, keeping payloads byte-identical to before the
    optional fields existed.
    """
    for key in keys:
        if data.get(key) is None:
            data.pop(key, None)
    return data

# PD's (game #1, "hoard-hurt-help") move vocabulary. The platform does NOT
# interpret these — POST /submit packs the request into a generic `move` dict and
# routes validation/recording through that game's module (app/games/). A second
# game ships its own move shape; full free-form move JSON on the wire is deferred
# to game #2 (see specs/004-game-framework, plan Decision: storage/wire
# generalization rides with the second game).
Action = Literal["HOARD", "HELP", "HURT"]


class MatchIdEnvelope(BaseModel):
    """Canonical match_id plus legacy game_id compatibility."""

    model_config = ConfigDict(populate_by_name=True)

    match_id: str
    game_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_game_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and "match_id" not in data and "game_id" in data:
            data = dict(data)
            data["match_id"] = data["game_id"]
        return data

    @model_validator(mode="after")
    def _mirror_game_id(self) -> "MatchIdEnvelope":
        self.game_id = self.match_id
        return self


# --- Join ---


class JoinRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_]+$")
    strategy_prompt: str = Field(min_length=1, max_length=2000)
    model_self_report: str | None = Field(default=None, max_length=200)


class JoinResponse(MatchIdEnvelope):
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


class TurnStatic(MatchIdEnvelope):
    rules_version: str
    rules: str
    base_prompt: str | None = None
    total_rounds: int
    turns_per_round: int
    your_agent_id: str
    all_agent_ids: list[str]
    your_strategy: str | None = None
    # Fields the per-match poll gained when its static block was unified with the
    # next-turn fan-out's (see build_turn_static_dict): the game type, and the
    # sideline coach's one-round note (Player.coach_note, gated to the round it
    # targets). Both serialize only when set, mirroring the fan-out dict — which
    # includes coach_note conditionally — so the two paths emit the same shape.
    # Any future optional field must join this tuple, or the poll path will emit
    # `"field": null` where the fan-out omits the key (the drift-guard test in
    # test_agent_next_turn_fanout catches the divergence).
    game: str | None = None
    coach_note: str | None = None

    @model_serializer(mode="wrap")
    def _omit_unset_additions(self, handler: SerializerFunctionWrapHandler) -> dict:
        return _drop_none_keys(handler(self), ("game", "coach_note"))


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
    quantity: int | None = None
    face: int | None = None
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
    # Per-game state (omitted for games that supply none, e.g. PD — the payload
    # must stay byte-identical to before these keys existed; games that return
    # state, e.g. Liar's Dice, serialize them normally). Kept last so they don't
    # disturb the cache-friendly prefix.
    your_private_state: dict | None = None
    public_state: dict | None = None

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler) -> dict:
        return _drop_none_keys(handler(self), ("your_private_state", "public_state"))


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


class NextTurnYourTurn(MatchIdEnvelope):
    # Same raw payload as YourTurnResponse, plus the match_id (a bot using the
    # loop isn't tracking which game it's in).
    status: Literal["your_turn"] = "your_turn"
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
    # PD's move vocabulary. `action` is optional because a non-PD game submits a
    # free-form `move` instead; what's required is enforced by the game module's
    # validate_move, not the wire schema.
    action: Action | None = None
    target_id: str | None = None
    # Free-form move for games whose vocabulary isn't PD's HOARD/HELP/HURT (e.g.
    # Liar's Dice {"type":"BID","quantity":3,"face":5}). The platform passes it to
    # the game module untouched. PD bots omit it and use `action`.
    move: dict | None = None
    message: str = Field(default="", max_length=MESSAGE_MAX_LENGTH)
    thinking: str = Field(default="", max_length=THINKING_MAX_LENGTH)
    # Connector sets this True when the LLM failed and a default move is being
    # submitted on its behalf. The server stores it via the existing was_defaulted
    # column so fallback moves are identifiable in the DB without a migration.
    is_connector_fallback: bool = False


class MessageRequest(BaseModel):
    turn_token: str
    message: str = Field(default="", max_length=MESSAGE_MAX_LENGTH)
    thinking: str = Field(default="", max_length=THINKING_MAX_LENGTH)
    # Same flag as SubmitRequest — marks talk-phase messages sent as defaults
    # because the LLM process failed.
    is_connector_fallback: bool = False


class MessageResponse(BaseModel):
    status: Literal["accepted"] = "accepted"
    received_at: datetime
    # The agent's next step is always get_next_turn. We deliberately do NOT hand
    # back a far-future deadline here: a CLI agent reads such a timestamp as "wait
    # until then" and inserts its own shell `sleep`, defeating the server-side
    # long-poll. `next_poll_after_seconds=0` means "poll again now" — get_next_turn
    # holds the line open for you until the act phase opens.
    next_poll_after_seconds: int = 0


class TalkWindowClosedResponse(BaseModel):
    """submit_talk's answer when the talk window already closed and the turn has
    moved on to the act phase. This is NOT an error — the agent simply talked a
    beat too late. It should now submit its action; the turn_token is unchanged,
    so the same one works for the act submit. Carries no `thinking`, so a late
    talk's private reasoning is never echoed back."""

    status: Literal["talk_window_closed"] = "talk_window_closed"
    round: int
    turn: int
    phase: Literal["act"] = "act"
    turn_token: str
    # Poll again now (don't sleep on a deadline) — see MessageResponse.
    next_poll_after_seconds: int = 0
    detail: str = (
        "The talk window already closed; this turn is now in the act phase. "
        "Submit your action."
    )


class SubmitResponse(BaseModel):
    status: Literal["accepted"] = "accepted"
    received_at: datetime
    # Poll again now (don't sleep on a deadline) — see MessageResponse.
    next_poll_after_seconds: int = 0


# --- State (agent-flavored) ---


class AgentStateResponse(MatchIdEnvelope):
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
