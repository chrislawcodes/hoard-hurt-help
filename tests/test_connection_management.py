from __future__ import annotations

import base64
import json
import re
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from itsdangerous import TimestampSigner
from starlette.middleware.sessions import SessionMiddleware

from app.db import make_engine
from app.engine.connection_health import ConnectionHealth, compute_connection_health
from app.engine.pending_connection_gc import gc_pending_connections
from app.engine.tokens import bot_key_lookup, generate_connection_key, generate_turn_token
from app.models import Base
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_setup import ConnectionSetup
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import Turn, TurnSubmission
from app.models.user import User
from app.routes.agent_next_turn import router as agent_next_turn_router
from app.routes.connections_credentials import router as connections_credentials_router
from app.routes.connections_lifecycle import router as connections_lifecycle_router
from app.routes.connections_setup import router as connections_setup_router

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = make_engine("sqlite+aiosqlite:///:memory:")
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def app(
    session_factory: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    monkeypatch.setattr("app.db.engine", engine)
    test_app = FastAPI()
    test_app.add_middleware(
        SessionMiddleware,
        secret_key="dev-only-do-not-use-in-prod-" + "x" * 40,
        same_site="lax",
        https_only=False,
        session_cookie="hhh_session",
    )
    test_app.include_router(agent_next_turn_router)
    test_app.include_router(connections_setup_router, prefix="/me/connections")
    test_app.include_router(connections_credentials_router, prefix="/me/connections")
    test_app.include_router(connections_lifecycle_router, prefix="/me/connections")
    return test_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _signed_in_cookies(user_id: int) -> dict[str, str]:
    signer = TimestampSigner("dev-only-do-not-use-in-prod-" + "x" * 40)
    payload = base64.b64encode(
        json.dumps({"user_id": user_id, "next_after_login": None}).encode()
    ).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _make_user(db: AsyncSession, *, handle: str = "agent0", i: int = 0) -> User:
    user = User(
        google_sub=f"sub-{i}",
        email=f"u{i}@t.com",
        handle=handle,
        handle_key=handle,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_connection(
    db: AsyncSession,
    user: User,
    *,
    provider: ConnectionProvider = ConnectionProvider.CLAUDE,
    status: ConnectionStatus = ConnectionStatus.ACTIVE,
    key: str | None = None,
    nickname: str | None = None,
) -> tuple[Connection, str]:
    plain_key = key or generate_connection_key()
    connection = Connection(
        user_id=user.id,
        provider=provider,
        nickname=nickname,
        key_lookup=bot_key_lookup(plain_key),
        key_hint=plain_key[-4:],
        status=status,
    )
    db.add(connection)
    await db.flush()
    from app.models.connection_provider import ConnectionProvider as _CPRow

    db.add(_CPRow(connection_id=connection.id, provider=provider, enabled=True, detected=False))
    await db.flush()
    return connection, plain_key


async def _make_connection_setup(
    db: AsyncSession,
    user: User,
    *,
    provider: ConnectionProvider = ConnectionProvider.CLAUDE,
    key: str | None = None,
    nickname: str | None = None,
) -> tuple[ConnectionSetup, str]:
    plain_key = key or generate_connection_key()
    setup = ConnectionSetup(
        user_id=user.id,
        provider=provider,
        nickname=nickname,
        key_lookup=bot_key_lookup(plain_key),
        key_hint=plain_key[-4:],
    )
    db.add(setup)
    await db.flush()
    return setup, plain_key


async def _make_agent(
    db: AsyncSession,
    user: User,
    *,
    connection: Connection | None,
    name: str,
    model: str,
    kind: AgentKind = AgentKind.AI,
) -> tuple[Agent, AgentVersion | None]:
    agent_provider = (
        (connection.provider if connection is not None else ConnectionProvider.CLAUDE)
        if kind == AgentKind.AI
        else None
    )
    agent = Agent(
        user_id=user.id,
        provider=agent_provider,
        kind=kind,
        name=name,
        game="hoard-hurt-help",
        status=AgentStatus.ACTIVE if connection is not None else AgentStatus.PAUSED,
    )
    db.add(agent)
    await db.flush()
    version = None
    if kind == AgentKind.AI:
        version = AgentVersion(
            agent_id=agent.id,
            version_no=1,
            model=model,
            strategy_text="Play to win.",
        )
        db.add(version)
        await db.flush()
        agent.current_version_id = version.id
        await db.flush()
    return agent, version


async def _make_match(db: AsyncSession, match_id: str, *, state: GameState) -> Match:
    match = Match(
        id=match_id,
        name=f"Match {match_id}",
        game="hoard-hurt-help",
        state=state,
        scheduled_start=NOW - timedelta(hours=1),
        started_at=NOW - timedelta(hours=1) if state != GameState.SCHEDULED else None,
        per_turn_deadline_seconds=60,
    )
    db.add(match)
    await db.flush()
    return match


async def _seat_player(
    db: AsyncSession,
    *,
    match: Match,
    user: User,
    agent: Agent,
    version: AgentVersion,
    seat_name: str,
) -> Player:
    player = Player(
        match_id=match.id,
        user_id=user.id,
        agent_id=agent.id,
        agent_version_id=version.id,
        seat_name=seat_name,
        model_self_report=version.model,
    )
    db.add(player)
    await db.flush()
    return player


async def _make_turn(
    db: AsyncSession,
    *,
    match: Match,
    player: Player,
    turn_no: int,
    defaulted: bool = False,
) -> None:
    turn = Turn(
        match_id=match.id,
        round=1,
        turn=turn_no,
        turn_token=generate_turn_token(),
        opened_at=NOW,
        deadline_at=NOW + timedelta(minutes=1),
    )
    db.add(turn)
    await db.flush()
    db.add(
        TurnSubmission(
            turn_id=turn.id,
            player_id=player.id,
            action="HOARD",
            was_defaulted=defaulted,
            submitted_at=NOW,
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_create_machine_connection_shows_setup_page_before_connect(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await _make_user(db)
        await db.commit()

    resp = await client.get(
        "/me/connections",
        cookies=_signed_in_cookies(user.id),
    )
    assert resp.status_code == 200
    # The connections page shows the ready-to-run connector command inline (now the
    # secondary "always-on" option) and mints the pending machine setup on load.
    assert "always-on connector" in resp.text
    assert "Name this machine" in resp.text
    assert "Paste this to your AI assistant:" in resp.text
    assert "agentludum_connector.py" in resp.text
    assert "setup-files/agentludum_connector.py" in resp.text
    assert "--install" in resp.text
    # The old per-provider agent header is gone (machines are provider-agnostic).
    assert "X-Agent-Key" not in resp.text

    async with session_factory() as db:
        setup = (
            await db.execute(select(ConnectionSetup).where(ConnectionSetup.user_id == user.id))
        ).scalar_one()
        assert setup.completed_at is None
        assert setup.connection_id is None
        assert setup.provider is None
        connection = (
            await db.execute(select(Connection).where(Connection.user_id == user.id))
        ).scalar_one_or_none()
        assert connection is None

    key_match = re.search(r"--key (sk_conn_[a-f0-9]+) --url", resp.text)
    assert key_match is not None
    key = key_match.group(1)

    auth = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert auth.status_code == 200

    async with session_factory() as db:
        setup = (
            await db.execute(select(ConnectionSetup).where(ConnectionSetup.user_id == user.id))
        ).scalar_one()
        assert setup.completed_at is not None
        connection = (
            await db.execute(select(Connection).where(Connection.user_id == user.id))
        ).scalar_one()
        assert connection.provider is None
        rows = (
            await db.execute(
                select(ConnectionProviderRow).where(
                    ConnectionProviderRow.connection_id == connection.id
                )
            )
        ).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_save_machine_name_rejects_overlong_nickname(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """nickname is VARCHAR(60); a longer value must 400, not 500 in prod."""
    async with session_factory() as db:
        user = await _make_user(db)
        await db.commit()

    resp = await client.post(
        "/me/connections/name",
        cookies=_signed_in_cookies(user.id),
        data={"nickname": "n" * 61},
        follow_redirects=False,
    )
    assert resp.status_code == 400, resp.text
    # The length check runs before any setup row is minted.
    async with session_factory() as db:
        setup = (
            await db.execute(select(ConnectionSetup).where(ConnectionSetup.user_id == user.id))
        ).scalar_one_or_none()
        assert setup is None


@pytest.mark.asyncio
async def test_connections_list_shows_inline_setup_and_no_provider_picker(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await _make_user(db)
        await db.commit()

    resp = await client.get("/me/connections", cookies=_signed_in_cookies(user.id))
    assert resp.status_code == 200
    # One unified setup prompt, inline, using the single connector download. The
    # connector is now the secondary "always-on" option below the Mode A flow.
    assert "always-on connector" in resp.text
    assert "Name this machine" in resp.text
    assert "Paste this to your AI assistant:" in resp.text
    assert "setup-files/agentludum_connector.py" in resp.text
    # The old two-group, per-provider picker is gone.
    assert "Hermes / OpenClaw" not in resp.text
    assert 'name="provider"' not in resp.text
    assert "agentludum_setup_hermes.py" not in resp.text
    assert "agentludum_setup_openclaw.py" not in resp.text


async def _set_live(db: AsyncSession, connection: Connection) -> None:
    """Mark a connection live now (last_seen within the live window) → READY health."""
    connection.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    await db.flush()


@pytest.mark.asyncio
async def test_connections_list_new_state_shows_connect_command_and_listening(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """NEW user (no connections): lead with the OAuth-shaped, header-less connect
    command and the pulsing 'Listening for your AI to connect…' region."""
    async with session_factory() as db:
        user = await _make_user(db)
        await db.commit()

    client.cookies.update(_signed_in_cookies(user.id))
    resp = await client.get("/me/connections")
    assert resp.status_code == 200
    text = resp.text

    assert "Play with your own AI" in text
    # The connect step reads as numbered sub-steps (paste in terminal / sign in).
    assert "Connect your AI provider" in text
    assert "Paste this in your terminal" in text
    assert "Sign in with Google" in text
    # Hero "add the server" command for Claude Code, OAuth-shaped (no key, no
    # chained play one-liner — the real flow is add → sign in → reload → paste).
    assert "claude mcp add --transport http agentludum" in text
    # Claude Code's sign-in note points at the interactive /mcp Authenticate step.
    assert "choose Authenticate" in text
    # The "add the server" instruction itself is header-less / key-less (the
    # always-on connector below still uses a key, which is out of scope here).
    connect_block = text.split("byo-panel-claude-code", 1)[1].split("</section>", 1)[0]
    assert "X-Connection-Key" not in connect_block
    assert "sk_conn_" not in connect_block
    assert "--header" not in connect_block
    # Codex renders as one copyable terminal command (add + login in a single
    # paste), not a file edit and not two blocks — its sign-in is bundled in.
    codex_block = text.split("byo-panel-codex", 1)[1].split("</section>", 1)[0]
    assert "codex mcp add agentludum --url" in codex_block
    assert "codex mcp login agentludum" in codex_block
    assert "config.toml" not in codex_block
    assert "X-Connection-Key" not in codex_block
    assert "sk_conn_" not in codex_block
    assert "byo-signin-codex" not in codex_block  # no separate sign-in block
    # The Copy button sits to the RIGHT of the command text (text before button).
    assert codex_block.index("byo-cmd-text") < codex_block.index("byo-cmd-btn")
    # Claude Code is the default (first) tab — the audience default. Codex carries
    # an "Easiest" badge as the zero-/mcp path.
    assert text.index('for="byo-tab-claude-code"') < text.index('for="byo-tab-codex"')
    assert "byo-easiest-badge" in text
    # Claude Code needs a second paste to sign in: the /mcp chip. Its step-2
    # heading names the real action (paste /mcp into Claude Code), not the effect.
    assert "byo-signin-claude-code" in connect_block
    assert "/mcp" in connect_block
    assert "In Claude Code, paste /mcp" in connect_block
    # All four clients are offered; Cursor dropped.
    assert 'for="byo-tab-claude-code"' in text
    assert 'for="byo-tab-codex"' in text
    assert 'for="byo-tab-gemini"' in text
    assert 'for="byo-tab-claude-desktop"' in text
    assert 'for="byo-tab-cursor"' not in text
    # The self-advancing waiting region with its 4s poll.
    assert "Waiting for your AI to connect…" in text
    assert 'hx-get="/me/connections/live-status"' in text
    assert 'hx-trigger="every 4s"' in text
    # Always-on connector kept as the collapsed secondary option.
    assert "always-on connector" in text
    assert "setup-files/agentludum_connector.py" in text


@pytest.mark.asyncio
async def test_connections_list_returning_state_shows_play_prompt(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """RETURNING user (connected before, nothing live now): lead with the Mode A
    play-prompt (the recurring action); the full add-server setup is collapsed."""
    async with session_factory() as db:
        user = await _make_user(db)
        # A connection that has never checked in → DISCONNECTED, not live now.
        await _make_connection(db, user, nickname="My Mac")
        await db.commit()

    client.cookies.update(_signed_in_cookies(user.id))
    resp = await client.get("/me/connections")
    assert resp.status_code == 200
    text = resp.text

    assert "Start playing" in text
    # The Mode A play-prompt leads the returning state.
    assert "You are playing Hoard Hurt Help through the agentludum MCP tools." in text
    assert "never ask me for a key or token" in text
    # The recovery nudge for returning users whose connection went to sleep:
    # if the AI can't find the tools, reconnect below.
    assert "can't find the game tools" in text
    assert "Reconnect below" in text
    # Full setup collapsed behind the "✓ Set up" disclosure.
    assert "✓ Set up" in text
    # Not live → still waiting; not the live block.
    assert "Waiting for your AI to connect…" in text
    assert "Join a game →" not in text
    # The user's machine still shows below.
    assert "My Mac" in text


@pytest.mark.asyncio
async def test_connections_list_connected_with_agent_leads_with_play_prompt(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """CONNECTED (a connection live now) + has an agent, but the AI has not made a
    game call yet (api_call_count == 0): lead with the play-prompt code block and
    do NOT show a 'Join a game' button — pasting the play-prompt is what starts play."""
    async with session_factory() as db:
        user = await _make_user(db)
        connection, _ = await _make_connection(db, user)
        await _set_live(db, connection)
        await _make_agent(
            db, user, connection=connection, name="Negotiator", model="claude-haiku-4-5"
        )
        await db.commit()

    client.cookies.update(_signed_in_cookies(user.id))
    resp = await client.get("/me/connections")
    assert resp.status_code == 200
    text = resp.text

    # Clear "Connected" success banner.
    assert "Connected" in text
    assert "This AI is linked and signed in" in text
    # Leads with the play-prompt code block — the one thing to do now.
    assert "Tell your AI to play" in text
    assert "You are playing Hoard Hurt Help through the agentludum MCP tools." in text
    assert "Negotiator · claude-haiku-4-5" in text
    # No "Join a game" CTA — pasting the play-prompt is what starts play.
    assert "Join a game →" not in text
    # Not yet playing → not the success box, and not nudging to create an agent.
    assert "Your AI is playing" not in text
    assert "Create your agent →" not in text


@pytest.mark.asyncio
async def test_connections_list_playing_state_shows_success(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """PLAYING (a live connection that has made a real game call, api_call_count >
    0): show the 'Your AI is playing' success box so the user knows the play-prompt
    took — not the play-prompt block and not a Join button."""
    async with session_factory() as db:
        user = await _make_user(db)
        connection, _ = await _make_connection(db, user)
        await _set_live(db, connection)
        connection.api_call_count = 1  # the AI has called the game tools
        await _make_agent(
            db, user, connection=connection, name="Negotiator", model="claude-haiku-4-5"
        )
        await db.commit()

    client.cookies.update(_signed_in_cookies(user.id))
    resp = await client.get("/me/connections")
    assert resp.status_code == 200
    text = resp.text

    # Success confirmation that the play-prompt took.
    assert "Your AI is playing" in text
    assert "You can close this page" in text
    assert "byo-playing" in text
    assert "Watch your games →" in text
    # The connect/play-prompt step is gone — and so is any Join button. (The
    # always-on connector below has its own "Paste this…" copy, so check for the
    # play-prompt block specifically, not that phrase.)
    assert "Tell your AI to play" not in text
    assert "byo-play-prompt-live" not in text
    assert "You are playing Hoard Hurt Help through the agentludum MCP tools." not in text
    assert "Join a game →" not in text


@pytest.mark.asyncio
async def test_connections_list_live_state_without_agent_nudges_create(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """ALREADY PLAYING but no agent yet: lead with the Create-an-agent nudge."""
    async with session_factory() as db:
        user = await _make_user(db)
        connection, _ = await _make_connection(db, user)
        await _set_live(db, connection)
        await db.commit()

    client.cookies.update(_signed_in_cookies(user.id))
    resp = await client.get("/me/connections")
    assert resp.status_code == 200
    text = resp.text

    assert "Connected" in text
    assert "This AI is linked and signed in" in text
    assert "Create your agent →" in text
    assert "/me/agents/new" in text
    assert "Join a game →" not in text
    # No agent yet → the play-prompt is deferred until they have one.
    assert "Paste this to your AI to start playing:" not in text


@pytest.mark.asyncio
async def test_live_status_fragment_not_live_shows_listening(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await _make_user(db)
        await db.commit()

    resp = await client.get(
        "/me/connections/live-status", cookies=_signed_in_cookies(user.id)
    )
    assert resp.status_code == 200
    assert "Waiting for your AI to connect…" in resp.text
    assert "Join a game →" not in resp.text
    assert "Create your agent →" not in resp.text


@pytest.mark.asyncio
async def test_live_status_fragment_live_shows_post_connect_block(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await _make_user(db)
        connection, _ = await _make_connection(db, user)
        await _set_live(db, connection)
        await db.commit()

    resp = await client.get(
        "/me/connections/live-status", cookies=_signed_in_cookies(user.id)
    )
    assert resp.status_code == 200
    # Clear "Connected" banner in the poll fragment too.
    assert "Connected" in resp.text
    assert "This AI is linked and signed in" in resp.text
    # No agent yet → the create-an-agent nudge; the play-prompt is deferred until
    # they have an agent (it isn't useful before then).
    assert "Create your agent →" in resp.text
    assert "Paste this to your AI to start playing:" not in resp.text
    assert "Waiting for your AI to connect…" not in resp.text


@pytest.mark.asyncio
async def test_connections_list_renders_existing_connection(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await _make_user(db)
        connection, _ = await _make_connection(db, user, nickname="My Claude")
        await db.commit()

    resp = await client.get("/me/connections", cookies=_signed_in_cookies(user.id))
    assert resp.status_code == 200
    assert "Your connections" in resp.text
    # No mode_a_at on this connection, so it reads as the always-on connector kind.
    assert "Machine connection" in resp.text
    assert "My Claude" in resp.text
    assert "Manage →" in resp.text
    assert "Disconnected" in resp.text


@pytest.mark.asyncio
async def test_naming_machine_autosaves_into_one_setup_and_keeps_a_stable_key(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await _make_user(db)
        await db.commit()

    # Set the auth cookie on the client jar (like a browser) so the server's
    # session updates — which carry the one-time key — persist across requests.
    client.cookies.update(_signed_in_cookies(user.id))
    page = await client.get("/me/connections")
    first_match = re.search(r"--key (sk_conn_[a-f0-9]+) --url", page.text)
    assert first_match is not None
    first_key = first_match.group(1)

    # The name field auto-saves via the dedicated endpoint (no button, no reload)
    # and returns a tiny "Saved" tick.
    saved = await client.post("/me/connections/name", data={"nickname": "My Machine"})
    assert saved.status_code == 200
    assert "Saved" in saved.text

    renamed = await client.post(
        "/me/connections/name", data={"nickname": "My Machine (renamed)"}
    )
    assert renamed.status_code == 200

    page2 = await client.get("/me/connections")
    second_match = re.search(r"--key (sk_conn_[a-f0-9]+) --url", page2.text)
    assert second_match is not None
    # Auto-saving the name reuses the one open setup and never rotates the key the
    # user may have already copied.
    assert second_match.group(1) == first_key

    async with session_factory() as db:
        setups = (
            await db.execute(
                select(ConnectionSetup).where(ConnectionSetup.user_id == user.id).order_by(ConnectionSetup.id)
            )
        ).scalars().all()
        assert len(setups) == 1
        setup = setups[0]
        assert setup.nickname == "My Machine (renamed)"
        assert setup.provider is None
        assert setup.completed_at is None
        assert setup.connection_id is None

    # Clearing the name blanks it (so the hostname default can take over) and the
    # tick goes away.
    cleared = await client.post("/me/connections/name", data={"nickname": "  "})
    assert cleared.status_code == 200
    assert cleared.text.strip() == ""
    async with session_factory() as db:
        setup = (
            await db.execute(select(ConnectionSetup).where(ConnectionSetup.user_id == user.id))
        ).scalar_one()
        assert setup.nickname is None


@pytest.mark.asyncio
async def test_first_authenticated_call_creates_real_connection_from_setup(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await _make_user(db)
        setup, plain_key = await _make_connection_setup(
            db,
            user,
            provider=ConnectionProvider.CLAUDE,
            nickname="My Claude",
        )
        await db.commit()

    resp = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": plain_key})
    assert resp.status_code == 200
    # No game seated yet, so the call succeeds with the idle "no_game" status.
    assert resp.json()["status"] == "no_game"

    banner = await client.get(
        f"/me/connections/setup/{setup.id}/status",
        cookies=_signed_in_cookies(user.id),
    )
    assert banner.status_code == 200
    assert "This machine is connected" in banner.text

    async with session_factory() as db:
        setup_row = (
            await db.execute(select(ConnectionSetup).where(ConnectionSetup.id == setup.id))
        ).scalar_one()
        connection_id = setup_row.connection_id
        assert connection_id is not None
        connection = (
            await db.execute(select(Connection).where(Connection.id == connection_id))
        ).scalar_one()
        assert connection.user_id == user.id
        assert connection.nickname == "My Claude"
        assert connection.provider is ConnectionProvider.CLAUDE
        assert connection.first_connected_at is not None
        assert connection.status is ConnectionStatus.ACTIVE

    delete_resp = await client.post(
        f"/me/connections/{connection_id}/delete",
        cookies=_signed_in_cookies(user.id),
    )
    assert delete_resp.status_code == 303

    stop_resp = await client.get(
        "/api/agent/next-turn",
        headers={"X-Connection-Key": plain_key},
    )
    assert stop_resp.status_code == 410
    assert stop_resp.json()["detail"]["error"]["code"] == "CONNECTION_DELETED"

    async with session_factory() as db:
        setup_row = (
            await db.execute(select(ConnectionSetup).where(ConnectionSetup.id == setup.id))
        ).scalar_one()
        assert setup_row.connection_id is None
        assert setup_row.completed_at is not None
        connection = (
            await db.execute(select(Connection).where(Connection.id == connection_id))
        ).scalar_one()
        assert connection.deleted_at is not None
        assert connection.status is ConnectionStatus.PAUSED


@pytest.mark.asyncio
async def test_rotate_overlap_keeps_old_key_until_new_key_used(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await _make_user(db)
        connection, old_key = await _make_connection(db, user)
        await db.commit()

    rotated = await client.post(
        f"/me/connections/{connection.id}/rotate",
        cookies=_signed_in_cookies(user.id),
        follow_redirects=True,
    )
    assert rotated.status_code == 200
    match = re.search(r"--key (sk_conn_[a-f0-9]+) --url", rotated.text)
    assert match is not None
    new_key = match.group(1)

    async with session_factory() as db:
        stored = (
            await db.execute(select(Connection).where(Connection.id == connection.id))
        ).scalar_one()
        assert stored.prev_key_lookup == bot_key_lookup(old_key)

    old_ok = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": old_key})
    assert old_ok.status_code == 200
    # No game seated, so both keys succeed with the idle "no_game" status.
    assert old_ok.json()["status"] == "no_game"

    new_ok = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": new_key})
    assert new_ok.status_code == 200
    assert new_ok.json()["status"] == "no_game"

    async with session_factory() as db:
        stored = (
            await db.execute(select(Connection).where(Connection.id == connection.id))
        ).scalar_one()
        assert stored.prev_key_lookup is None

    old_dead = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": old_key})
    assert old_dead.status_code == 401


@pytest.mark.asyncio
async def test_delete_stops_runner_but_leaves_agents_active(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Coverage-aware delete: the machine's runner is stopped, but agents stay
    ACTIVE — they are no longer pinned to a connection, so they keep playing on
    any other live connection covering their provider (or wait)."""
    async with session_factory() as db:
        user = await _make_user(db)
        connection, old_key = await _make_connection(
            db, user, provider=ConnectionProvider.CLAUDE
        )
        agent, version = await _make_agent(
            db,
            user,
            connection=connection,
            name="Alpha",
            model="claude-sonnet-4-6",
        )
        match = await _make_match(db, "M_detach", state=GameState.ACTIVE)
        player = await _seat_player(
            db,
            match=match,
            user=user,
            agent=agent,
            version=version,
            seat_name=f"{user.handle}/Alpha",
        )
        await _make_turn(db, match=match, player=player, turn_no=1)
        await db.commit()

    delete_resp = await client.post(
        f"/me/connections/{connection.id}/delete",
        cookies=_signed_in_cookies(user.id),
    )
    assert delete_resp.status_code == 303

    stop_resp = await client.get(
        "/api/agent/next-turn",
        headers={"X-Connection-Key": old_key},
    )
    assert stop_resp.status_code == 410
    assert stop_resp.json()["detail"]["error"]["code"] == "CONNECTION_DELETED"

    detail_resp = await client.get(
        f"/me/connections/{connection.id}",
        cookies=_signed_in_cookies(user.id),
    )
    assert detail_resp.status_code == 404

    async with session_factory() as db:
        stored = (
            await db.execute(select(Connection).where(Connection.id == connection.id))
        ).scalar_one()
        assert stored.deleted_at is not None
        assert stored.status is ConnectionStatus.PAUSED
        # The agent is NOT paused — it survives, ACTIVE, keeping its provider.
        stored_agent = (await db.execute(select(Agent).where(Agent.id == agent.id))).scalar_one()
        assert stored_agent.status is AgentStatus.ACTIVE
        assert stored_agent.provider is ConnectionProvider.CLAUDE
        stored_player = (
            await db.execute(select(Player).where(Player.id == player.id))
        ).scalar_one()
        assert stored_player.id == player.id


@pytest.mark.asyncio
async def test_toggle_provider_enables_and_strand_guard(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow

    async with session_factory() as db:
        user = await _make_user(db)
        connection, _ = await _make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        # An active AI agent depending on claude (the only covering connection).
        await _make_agent(db, user, connection=connection, name="Solo", model="claude-sonnet-4-6")
        await db.commit()
        conn_id = connection.id

    # Enable openai (was off) — succeeds, detected stays informational.
    r = await client.post(
        f"/me/connections/{conn_id}/providers/openai?enabled=true",
        cookies=_signed_in_cookies(user.id),
    )
    assert r.status_code == 303
    async with session_factory() as db:
        row = (
            await db.execute(
                select(ConnectionProviderRow).where(
                    ConnectionProviderRow.connection_id == conn_id,
                    ConnectionProviderRow.provider == ConnectionProvider.OPENAI,
                )
            )
        ).scalar_one()
        assert row.enabled is True

    # Disabling claude would strand the Solo agent (no other live connection) →
    # without confirm, it redirects to the warning and does NOT disable.
    r = await client.post(
        f"/me/connections/{conn_id}/providers/claude?enabled=false",
        cookies=_signed_in_cookies(user.id),
    )
    assert r.status_code == 303
    assert "strand_provider=claude" in r.headers["location"]
    async with session_factory() as db:
        claude_row = (
            await db.execute(
                select(ConnectionProviderRow).where(
                    ConnectionProviderRow.connection_id == conn_id,
                    ConnectionProviderRow.provider == ConnectionProvider.CLAUDE,
                )
            )
        ).scalar_one()
        assert claude_row.enabled is True  # still enabled — strand guard held

    # With confirm=true it goes through.
    r = await client.post(
        f"/me/connections/{conn_id}/providers/claude?enabled=false&confirm=true",
        cookies=_signed_in_cookies(user.id),
    )
    assert r.status_code == 303
    async with session_factory() as db:
        claude_row = (
            await db.execute(
                select(ConnectionProviderRow).where(
                    ConnectionProviderRow.connection_id == conn_id,
                    ConnectionProviderRow.provider == ConnectionProvider.CLAUDE,
                )
            )
        ).scalar_one()
        assert claude_row.enabled is False


@pytest.mark.asyncio
async def test_detail_renders_provider_toggles_and_install_hint(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The detail page shows providers as switches and, when a provider is on
    but its CLI was not detected, prompts the operator to install it."""
    async with session_factory() as db:
        user = await _make_user(db)
        # _make_connection seeds the claude provider row enabled=True, detected=False.
        connection, _ = await _make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        await db.commit()
        conn_id = connection.id

    r = await client.get(
        f"/me/connections/{conn_id}", cookies=_signed_in_cookies(user.id)
    )
    assert r.status_code == 200
    # Providers render as toggle switches, not plain buttons.
    assert "toggle-switch" in r.text
    assert "provider-row" in r.text
    # claude is on but undetected → the install hint names the CLI binary.
    assert "provider-install-hint" in r.text
    assert "<code>claude</code>" in r.text


@pytest.mark.asyncio
async def test_mode_a_detail_shows_read_only_provider_not_machine_toggles(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """An MCP (Mode A) connection plays one provider via the AI client you signed
    in with — so its detail page shows that provider read-only, NOT the machine
    multi-provider toggle box with CLI-detection language."""
    async with session_factory() as db:
        user = await _make_user(db)
        connection, _ = await _make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        connection.mode_a_at = datetime.now(timezone.utc)
        await db.commit()
        conn_id = connection.id

    r = await client.get(
        f"/me/connections/{conn_id}", cookies=_signed_in_cookies(user.id)
    )
    assert r.status_code == 200
    # The provider it plays is shown read-only…
    assert "This is an MCP connection." in r.text
    assert "Playing" in r.text
    assert "Claude" in r.text
    # …and none of the machine-connector toggle/CLI machinery appears.
    assert "toggle-switch" not in r.text
    assert "provider-install-hint" not in r.text
    assert "this machine should run" not in r.text


@pytest.mark.asyncio
async def test_connection_controls_live_in_status_card(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Pause/Rotate/Delete moved into the connection status card, so they also
    survive the status fragment's 5s poll instead of sitting in a separate box."""
    async with session_factory() as db:
        user = await _make_user(db)
        connection, _ = await _make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        await db.commit()
        conn_id = connection.id

    # The polled status fragment carries the controls.
    r = await client.get(
        f"/me/connections/{conn_id}/status", cookies=_signed_in_cookies(user.id)
    )
    assert r.status_code == 200
    assert "connection-controls" in r.text
    assert f"/me/connections/{conn_id}/rotate" in r.text

    # The detail page no longer has a standalone "Connection controls" card.
    r = await client.get(
        f"/me/connections/{conn_id}", cookies=_signed_in_cookies(user.id)
    )
    assert r.status_code == 200
    assert "connection-controls" in r.text  # present via the included status card
    assert "<h3 style=\"margin-top:0;\">Connection controls</h3>" not in r.text


@pytest.mark.asyncio
async def test_mode_a_status_card_hides_rotate_key(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Rotate Key issues a fresh paste-in key — a machine idea. An MCP connection
    signs in over OAuth, so the control is hidden and the copy talks reconnect, not
    'rotate the setup message' or 'this machine'."""
    async with session_factory() as db:
        user = await _make_user(db)
        connection, _ = await _make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        connection.mode_a_at = datetime.now(timezone.utc)
        await db.commit()
        conn_id = connection.id

    r = await client.get(
        f"/me/connections/{conn_id}/status", cookies=_signed_in_cookies(user.id)
    )
    assert r.status_code == 200
    assert "connection-controls" in r.text
    # Pause + Delete stay; Rotate Key is gone.
    assert "Rotate Key" not in r.text
    assert f"/me/connections/{conn_id}/rotate" not in r.text
    assert f"/me/connections/{conn_id}/delete" in r.text
    # Machine-flavored delete copy is replaced with sign-in language.
    assert "Delete this machine?" not in r.text
    assert "Sign in again from your AI client" in r.text


@pytest.mark.asyncio
async def test_detail_shows_when_connection_last_connected(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The detail page shows when the client last checked in — a relative
    'last seen 12m ago' in the badge plus a precise timestamp line. The wording
    says "last seen" (an honest read of the heartbeat) rather than "connected",
    which would overclaim a live link from a possibly-stale heartbeat."""
    async with session_factory() as db:
        user = await _make_user(db)
        connection, _ = await _make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        connection.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=12)
        await db.commit()
        conn_id = connection.id

    r = await client.get(
        f"/me/connections/{conn_id}", cookies=_signed_in_cookies(user.id)
    )
    assert r.status_code == 200
    assert "last seen" in r.text  # badge meta
    assert "12m ago" in r.text  # relative, human-readable
    assert "Last seen" in r.text  # precise line in the status card


@pytest.mark.asyncio
async def test_never_connected_shows_no_last_connected_time(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A connection that has never checked in reads 'never connected', not a
    bogus timestamp."""
    async with session_factory() as db:
        user = await _make_user(db)
        connection, _ = await _make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        connection.last_seen_at = None
        connection.first_connected_at = None
        await db.commit()
        conn_id = connection.id

    r = await client.get(
        f"/me/connections/{conn_id}", cookies=_signed_in_cookies(user.id)
    )
    assert r.status_code == 200
    assert "never connected" in r.text
    assert "Last seen" not in r.text  # the precise status-card line is hidden


@pytest.mark.asyncio
async def test_ready_status_copy_is_honest_about_staleness(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A live-but-idle connection (READY) must not claim a guaranteed live link.

    The heartbeat can be up to 90s stale, so the copy leads with the relative
    'last seen' time and hedges ('should be ready', 'can't confirm') instead of
    the old 'The client is connected and idle, ready for the next turn.'."""
    async with session_factory() as db:
        user = await _make_user(db)
        connection, _ = await _make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        await _set_live(db, connection)  # last_seen 5s ago, no active matches → READY
        await db.commit()
        conn_id = connection.id

    r = await client.get(
        f"/me/connections/{conn_id}/status", cookies=_signed_in_cookies(user.id)
    )
    assert r.status_code == 200
    # The honest, hedged wording is present.
    assert "should be ready for the next turn" in r.text
    assert "can't confirm" in r.text
    # The relative last-seen time is surfaced next to the status copy.
    assert "Last seen" in r.text
    # The old over-claiming line is gone.
    assert "connected and idle, ready for the next turn" not in r.text


@pytest.mark.asyncio
async def test_pending_connections_gc_after_24h(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as db:
        user = await _make_user(db)
        stale = ConnectionSetup(
            user_id=user.id,
            provider=ConnectionProvider.CLAUDE,
            key_lookup="lookup-stale",
            key_hint="stale",
            created_at=NOW - timedelta(hours=25),
        )
        fresh = ConnectionSetup(
            user_id=user.id,
            provider=ConnectionProvider.CLAUDE,
            key_lookup="lookup-fresh",
            key_hint="fresh",
            created_at=NOW - timedelta(hours=1),
        )
        db.add_all([stale, fresh])
        await db.commit()

        removed = await gc_pending_connections(db, now=NOW)
        assert removed == 1

        remaining = (
            await db.execute(select(ConnectionSetup).order_by(ConnectionSetup.created_at))
        ).scalars().all()
        assert [setup.id for setup in remaining] == [fresh.id]


@pytest.mark.asyncio
async def test_gc_raises_when_connection_setups_table_missing(
    engine: AsyncEngine,
) -> None:
    """gc_pending_connections must propagate OperationalError when the table is missing.

    The old shim silently returned 0 so a missing migration was invisible.
    After the shim was removed, the DB error surfaces immediately.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Drop the connection_setups table to simulate a pre-migration deployment.
        await conn.execute(text("DROP TABLE connection_setups"))

    bare_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with bare_factory() as db:
        with pytest.raises(OperationalError):
            await gc_pending_connections(db, now=NOW)


@pytest.mark.asyncio
async def test_connection_health_across_multiple_agents_tracks_the_active_game(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        user = await _make_user(db)
        connection, _ = await _make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        warm_agent, warm_version = await _make_agent(
            db,
            user,
            connection=connection,
            name="Warm",
            model="claude-sonnet-4-6",
        )
        cold_agent, cold_version = await _make_agent(
            db,
            user,
            connection=connection,
            name="Cold",
            model="claude-haiku-4-5",
        )
        warm_match = await _make_match(db, "M_live", state=GameState.ACTIVE)
        cold_match = await _make_match(db, "M_stalled", state=GameState.ACTIVE)
        warm_player = await _seat_player(
            db,
            match=warm_match,
            user=user,
            agent=warm_agent,
            version=warm_version,
            seat_name=f"{user.handle}/Warm",
        )
        cold_player = await _seat_player(
            db,
            match=cold_match,
            user=user,
            agent=cold_agent,
            version=cold_version,
            seat_name=f"{user.handle}/Cold",
        )
        # Health now tracks the matches this connection is SERVING (the sticky
        # pin), not agent attachment — pin both players to this connection.
        warm_player.served_by_connection_id = connection.id
        warm_player.served_pinned_at = NOW
        cold_player.served_by_connection_id = connection.id
        cold_player.served_pinned_at = NOW
        connection.last_seen_at = NOW - timedelta(seconds=20)
        for turn_no in (1, 2, 3):
            await _make_turn(db, match=cold_match, player=cold_player, turn_no=turn_no, defaulted=True)
        await _make_turn(db, match=warm_match, player=warm_player, turn_no=1, defaulted=False)
        await db.commit()

        health = await compute_connection_health(db, connection, now=NOW)

    assert health.state is ConnectionHealth.STALLED
    assert health.needs_reconnect is True
    assert health.game_name == "Match M_stalled"
