"""The MCP tool definitions and the play-instruction text they serve.

This module owns concern #4 of the MCP layer: the 7 MCP tools the AI client
calls (get_next_turn, get_next_turns, get_instructions, submit_talk,
submit_action, get_game_state, get_chat), the per-call DB-session dependency the
tools use, and the pure helpers that build the static instruction text and trim
the turn payload for MCP.

The tools are registered onto the shared ``FastMCP`` app by ``register_tools``,
which ``server`` calls during assembly. The tools read their play-service
collaborators (``play_*``, ``chat_transcript``) as module globals here so tests
that monkeypatch them on ``mcp_tools`` take effect, and reach the identity
resolvers through the ``connection_identity`` module object for the same reason.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentAccessToken, Depends
from fastmcp.server.dependencies import AccessToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.engine.agent_idle import LONG_POLL_HOLD_SECONDS
from app.engine.agent_play import (
    agent_identity_for,
    chat_transcript,
    get_next_turn as play_get_next_turn,
    get_next_turns as play_get_next_turns,
    submit_action as play_submit_action,
    submit_talk as play_submit_talk,
)
from app.engine.agent_playability import playable_agent_filter
from app.games import get as get_game_module
from app.models.agent import Agent
from app.models.match import Match
from app.routes.spectator_api import public_state

from mcp_server import connection_identity

_LAST_PULL: dict[tuple[int, str], float] = {}


@asynccontextmanager
async def _session_scope() -> AsyncIterator[AsyncSession]:
    """Per-call DB session for MCP tools, as an async context manager.

    FastMCP's DI (uncalled_for) resolves a ``Depends()`` value by entering it as
    an async context manager; unlike FastAPI it does NOT iterate a bare async
    generator. The app's ``get_session`` is a generator, so passing it to
    ``Depends`` would leave each tool with the raw generator object instead of a
    session (``'async_generator' object has no attribute 'execute'``). Wrapping
    in ``@asynccontextmanager`` gives uncalled_for something it can enter.
    """
    async with SessionLocal() as session:
        yield session


def _lean_payload_for_mcp(payload: dict[str, object]) -> dict[str, object]:
    """Drop the MCP-only duplicated static prompt text from a turn payload."""
    lean = dict(payload)
    # Top-level `strategy` is popped defensively only: the builder no longer
    # emits it (the strategy ships once, as `static.your_strategy`). This stays a
    # sanitizer, not a list of today's keys — if a builder change ever re-adds
    # one, prompt text must still not reach an MCP client.
    lean.pop("strategy", None)
    static = lean.get("static")
    if isinstance(static, dict):
        lean_static = dict(static)
        # `rules` is popped defensively only, for the same reason: the builder no
        # longer emits it. An MCP client gets its rules from get_instructions,
        # not from the turn.
        lean_static.pop("rules", None)
        lean_static.pop("base_prompt", None)
        lean_static.pop("your_strategy", None)
        lean["static"] = lean_static
    turns = lean.get("turns")
    if isinstance(turns, list):
        lean["turns"] = [
            _lean_payload_for_mcp(turn) if isinstance(turn, dict) else turn
            for turn in turns
        ]
    return lean


def _resolve_match_id(match_id: str | None, game_id: str | None) -> str:
    if match_id and game_id and match_id != game_id:
        raise ValueError("match_id and game_id must match when both are provided")
    resolved = match_id or game_id
    if resolved is None:
        raise ValueError("match_id or game_id is required")
    return resolved


def _mcp_how_to_play_block() -> str:
    return (
        "## How to play\n\n"
        "Keep calling get_next_turn(agent_id=...) in a loop. Never stop on your own — "
        "stop only when get_next_turn says should_stop=true.\n\n"
        '- status "your_turn": check current.phase\n'
        '  - If static.coach_note is present, treat it as a one-round instruction from your coach '
        '— follow it for this turn instead of (or on top of) your strategy.\n'
        '  - "talk": call submit_talk(match_id, turn_token, agent_turn_token, message, thinking). '
        'One message per turn. After it is accepted, call get_next_turn again right away — the '
        'server serves the "act" phase when it opens.\n'
        '  - "act": call submit_action(match_id, turn_token, agent_turn_token, action, target_id, message, thinking). '
        "After it is accepted, call get_next_turn again right away.\n"
        '  - `thinking` is one private sentence — spectators see it, other players never do. '
        "Name the rule you're following. Explain your thinking.\n"
        '- status "waiting": a turn is coming. Wait next_poll_after_seconds, then call again. '
        "If next_game_starts_in_seconds is present, tell me when the game starts.\n"
        '- status "no_game" with should_stop=false: no game yet. '
        "Wait next_poll_after_seconds, then call again.\n"
        '- status "no_game" with should_stop=true: stop and tell me — I\'ll start a game when ready.\n'
        "- Error (5xx / timeout): wait 30 seconds and retry, up to 3 times.\n\n"
        "The history in each turn is only the last couple of resolved turns — enough "
        "to react to. You hold the rest in this conversation. If you ever need the whole "
        "game (you joined one already in progress, or lost the thread), call "
        "get_game_state(match_id) once to catch up.\n\n"
        "Never run a shell `sleep`, and never wait for a turn's deadline or resolve time. "
        "get_next_turn does the waiting for you — it holds the request open until there is "
        "something to do (or it tells you to wait). Just call it again. "
        "Pause only when a reply gives you next_poll_after_seconds, and wait exactly that long "
        "(0 means now). The server sets the right wait for you.\n\n"
        "Call the tools. Do not answer in plain text or prose.\n\n"
        "Before you start the loop, restate in your own words what you will do for each status."
    )


def _format_instruction_sections(
    *,
    match: Match,
    your_agent_id: str,
    all_agent_ids: list[object],
    strategy_text: str,
) -> str:
    module = get_game_module(match.game)
    other_agent_ids = [agent_id for agent_id in all_agent_ids if agent_id != your_agent_id]
    lines = [
        "## The rules",
        "",
        module.semantic_rules_text_for_match(match).rstrip(),
        "",
        "## You",
        "",
        f'You are "{your_agent_id}". You can target: {other_agent_ids}.',
    ]
    # A game with hidden per-player state contributes its own setup hint (plus a
    # trailing blank); games with none fall back to a single blank separator.
    lines.extend(module.mcp_setup_hint_lines() or [""])
    lines.extend(
        [
            "## Your strategy",
            "",
            strategy_text.rstrip(),
            "",
            _mcp_how_to_play_block(),
        ]
    )
    return "\n".join(lines).rstrip()


# The 7 MCP tools.
#
# These are plain module-level coroutine functions so the tests can both call
# them directly (``await mcp_tools.get_next_turn(...)``) and monkeypatch their
# play-service collaborators on this module. ``register_tools`` binds them to the
# shared ``FastMCP`` app during assembly. FastMCP's ``.tool()`` returns the
# function unchanged, so registration is a side effect only — the names below
# stay ordinary callables.
#
# The two poll tools pass an explicit ``description=`` rather than letting FastMCP
# lift the docstring, because these two strings are load-bearing UX: they are all
# a client has to tell the near-identical ``get_next_turn`` / ``get_next_turns``
# apart, and picking wrong is silently expensive.
#
# Measured 2026-08-11 on Codex 0.146.1 against prod: told to "call get_next_turn
# in a loop" but reading the OLD descriptions — which never said one blocks and
# invited "use this to discover how many agents you are running" on the other —
# it polled ``get_next_turns`` 30 times in 100 seconds with no sleeps. That is the
# fan-out lane, the one exempt from holding (``pace_idle(can_hold=False)``), so
# choosing it silently opts out of the long poll. Naming the blocking tool in the
# paste-in play prompt fixed that client (#649); saying it here fixes every client,
# including ones whose operator never pasted the prompt.
_GET_NEXT_TURN_DESCRIPTION = """Wait for your next turn. THIS is the tool to poll in a loop.

This is a blocking call: the server holds the request open until there is
something for you to do (up to about {hold_seconds:.0f} seconds), so the call
itself IS your wait. Do not sleep between calls and do not wait out a turn's
deadline — the moment it returns, call it again.

Do NOT poll get_next_turns instead. That one answers instantly, so looping on it
spins without waiting.

Pass agent_id to wait on ONE specific agent. Use this when you are running
several agents at once: run one loop per agent in parallel, and give each loop
its own agent_id so the agents' turns never wait on each other. Omit agent_id to
play all agents from a single loop (most urgent first)."""

_GET_NEXT_TURNS_DESCRIPTION = """List every turn claimable right now, one per agent. NOT a polling tool.

This answers immediately and never waits, so calling it in a loop spins: it
burns a model call every few seconds and tells you nothing new. Poll
get_next_turn instead — that one blocks until there is work.

Call this ONCE, to discover how many agents you are running before fanning out:
if it returns more than one turn, run one parallel loop per agent (call
get_next_turn(agent_id=...) in each), so two agents on the same provider can both
move inside the same turn window instead of waiting in line."""


async def get_next_turn(
    *,
    agent_id: int | None = None,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(_session_scope)),
) -> Any:
    """Wait for your next turn. THIS is the tool to poll in a loop.

    The description clients actually read is built at registration — see
    ``_GET_NEXT_TURN_DESCRIPTION``, which states the live hold length.

    Pass agent_id to wait on ONE specific agent. Use this when you are running
    several agents at once: run one loop per agent in parallel, and give each loop
    its own agent_id so the agents' turns never wait on each other. Omit agent_id
    to play all agents from a single loop (most urgent first).
    """
    _access_token, _userinfo, connection = await connection_identity._resolve_oauth_connection(
        db, token
    )
    # Hold length is decided server-side off the caller's soonest game — see
    # app.engine.agent_idle.pace_idle and `agent_long_poll_hold_seconds`. This
    # path deliberately adds NO cap of its own: a second, independently-drifting
    # number deciding the same thing is how a hold change silently does nothing.
    #
    # There used to be a 25s cap here, justified as "MCP clients cut requests
    # sooner than a plain HTTP curl (commonly ~30s)". That was measured on
    # 2026-08-10 and is false: Claude Code 2.1.220, Codex 0.146.1 and
    # Antigravity's agy 1.1.11 each held a silent 90s request and returned it
    # cleanly, and their real walls are 300s, 300s and 180s respectively.
    payload = await play_get_next_turn(db, connection, agent_id=agent_id)
    return _lean_payload_for_mcp(payload)


async def get_next_turns(
    *,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(_session_scope)),
) -> Any:
    """List every turn claimable right now, one per agent. NOT a polling tool.

    The description clients actually read is ``_GET_NEXT_TURNS_DESCRIPTION``,
    which says so in the first line — this endpoint answers instantly and never
    holds, so a client that polls it here spins instead of waiting.

    Call it once to discover how many agents you are running before fanning out:
    if it returns more than one turn, run one parallel loop per agent (call
    get_next_turn(agent_id=...) in each), so two agents on the same provider can
    both move inside the same turn window instead of waiting in line.
    """
    _access_token, _userinfo, connection = await connection_identity._resolve_oauth_connection(
        db, token
    )
    payload = await play_get_next_turns(db, connection)
    return _lean_payload_for_mcp(payload)


async def get_instructions(
    *,
    agent_id: int | None = None,
    match_id: str | None = None,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(_session_scope)),
) -> str:
    """Return the static play instructions for one agent, if one can be selected."""
    _access_token, _userinfo, connection = await connection_identity._resolve_oauth_connection(
        db, token
    )
    match, your_agent_id, all_agent_ids, strategy_text = await agent_identity_for(
        db,
        connection,
        agent_id=agent_id,
        match_id=match_id,
    )
    if match is None or your_agent_id is None or strategy_text is None:
        active_agent_ids = sorted(
            (
                await db.execute(
                    select(Agent.id).where(
                        Agent.user_id == connection.user_id,
                        *playable_agent_filter(),
                    )
                )
            )
            .scalars()
            .all()
        )
        if agent_id is None and len(active_agent_ids) > 1:
            return (
                "You have multiple agents. Call get_instructions(agent_id=...) for each "
                f"one's strategy: {active_agent_ids}.\n\n"
                f"{_mcp_how_to_play_block()}"
            )
        return (
            "No active game yet. Start one, then call get_instructions again for that "
            "game's rules and your strategy."
        )
    return _format_instruction_sections(
        match=match,
        your_agent_id=your_agent_id,
        all_agent_ids=all_agent_ids,
        strategy_text=strategy_text,
    )


async def submit_talk(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
    message: str,
    thinking: str = "",
    turn_token: str,
    agent_turn_token: str,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(_session_scope)),
) -> Any:
    """Submit the talk-phase message for the current turn.

    If the talk window has already closed (the turn moved on to the act phase),
    this returns status "talk_window_closed" instead of an error — that is normal,
    not a failure. Just submit your action next; the turn_token is unchanged.
    """
    resolved_match_id = _resolve_match_id(match_id, game_id)
    _access_token, _userinfo, connection, player = await connection_identity._resolve_oauth_player(
        db,
        token,
        match_id=resolved_match_id,
        agent_turn_token=agent_turn_token,
    )
    return await play_submit_talk(
        db,
        match_id=resolved_match_id,
        player=player,
        agent_turn_token=agent_turn_token,
        turn_token=turn_token,
        message=message,
        thinking=thinking,
        is_connector_fallback=False,
    )


async def submit_action(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
    action: str,
    target_id: str | None,
    message: str,
    thinking: str = "",
    turn_token: str,
    agent_turn_token: str,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(_session_scope)),
) -> Any:
    """Submit the act-phase move for the current turn.

    Everything a client needs about `thinking` — what it is and what to write
    in it — is said once in `_mcp_how_to_play_block`, which the same client
    reads via get_instructions. Deliberately not repeated here, not even in
    summary: a pointer that restates what it points at is how the drift starts.

    This docstring used to own the privacy half while the how-to-play block
    owned the what-to-write half. Splitting one field's description across two
    files had already let them drift once (this said to leave it empty when you
    have nothing to add; the other said to write why you are making the move),
    and a split is a slower version of the same drift. One home.
    """
    resolved_match_id = _resolve_match_id(match_id, game_id)
    _access_token, _userinfo, connection, player = await connection_identity._resolve_oauth_player(
        db,
        token,
        match_id=resolved_match_id,
        agent_turn_token=agent_turn_token,
    )
    return await play_submit_action(
        db,
        match_id=resolved_match_id,
        player=player,
        connection=connection,
        agent_turn_token=agent_turn_token,
        turn_token=turn_token,
        action=action,
        target_id=target_id,
        message=message,
        thinking=thinking,
        is_connector_fallback=False,
    )


async def get_game_state(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(_session_scope)),
) -> Any:
    """Get the public state of any game."""
    connection_identity._require_access_token(token)
    resolved_match_id = _resolve_match_id(match_id, game_id)
    return await public_state(match_id=resolved_match_id, db=db)


async def get_chat(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
    since: str | None = None,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(_session_scope)),
) -> Any:
    """Pull the full public chat transcript."""
    resolved_match_id = _resolve_match_id(match_id, game_id)
    _access_token, _userinfo, _connection, player = await connection_identity._resolve_oauth_player(
        db,
        token,
        match_id=resolved_match_id,
    )
    return await chat_transcript(
        db,
        match_id=resolved_match_id,
        player=player,
        rate_state=_LAST_PULL,
        since=since,
    )


def register_tools(mcp_app: FastMCP) -> None:
    """Register the 7 MCP tools onto ``mcp_app`` during server assembly.

    ``FastMCP.tool()`` returns the wrapped function unchanged and registers it as
    a side effect, so this binds exactly the same 7 tools (same names, same input
    schemas) the module-level decorators used to. Keeping the functions at module
    level — and registering here — lets ``server`` own the single ``FastMCP``
    instance while the tool callables stay directly importable and patchable.
    """
    # The hold length is a server setting. Reading it here — rather than typing
    # the number into the description — keeps the one warning a client gets about
    # how long the call blocks honest when that setting changes. A second,
    # independently-drifting copy of this number is exactly how the old 25s cap
    # made a hold change silently do nothing (see the note in get_next_turn).
    mcp_app.tool(
        description=_GET_NEXT_TURN_DESCRIPTION.format(hold_seconds=LONG_POLL_HOLD_SECONDS)
    )(get_next_turn)
    mcp_app.tool(description=_GET_NEXT_TURNS_DESCRIPTION)(get_next_turns)
    for tool_fn in (
        get_instructions,
        submit_talk,
        submit_action,
        get_game_state,
        get_chat,
    ):
        mcp_app.tool()(tool_fn)
