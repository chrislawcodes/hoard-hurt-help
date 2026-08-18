"""Pydantic schemas for the Agent API.

These shapes are documented in SPEC.md §1.1 and contracts/api.yaml.

The your-turn payload itself has no model here — `_build_turn_payload` in
`app/engine/agent_play_next_turn.py` assembles it as a plain dict and the route
serves it with `response_model=None`. What lives here are the pieces that dict is
built from (`ScoreboardRow`, `HistoryTurn`, `TalkMessage`, `CurrentTurn`), the
request bodies, and the opt-in pull responses at the bottom of the file. A model
that mirrors the turn payload but is never served silently rots: the last one
still demanded a `rules` key the builder had already stopped emitting.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent_prompt import MESSAGE_MAX_LENGTH, THINKING_MAX_LENGTH


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


# --- Scoreboard row (next-turn payload, spectator view, read models) ---


class ScoreboardRow(BaseModel):
    agent_id: str
    round_score: int
    round_wins: float


# --- Standings + board-signal shapes (standings pulls, game-module signals) ---


class StandingRow(BaseModel):
    agent_id: str
    round_score: int
    rank: int


class Alliance(BaseModel):
    members: list[str]
    strength: int


class BoardSignals(BaseModel):
    alliances: list[Alliance]
    cooperation_temperature: float
    temperature_label: Literal["hostile", "mixed", "cooperative"]
    surging: list[str]


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


class GameCompletedResponse(BaseModel):
    status: Literal["game_completed"] = "game_completed"
    winner_agent_id: str | None
    final_standings: list[dict]


# --- Next-turn (game-agnostic loop) response shapes ---
# A bot connects once and calls get_next_turn across ALL its games, so the loop
# answers with a wider set of waiting reasons than a single match would need.
# The "your_turn" answer has no model here: `_build_turn_payload` assembles it as
# a plain dict and the route serves it with `response_model=None`.


class NextTurnWaiting(BaseModel):
    status: Literal["waiting"] = "waiting"
    reason: Literal["no_open_turns", "no_active_games", "bot_paused"]
    next_poll_after_seconds: int = 5


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
