Review this spec artifact using a completeness-adversarial lens.
Stay scoped to that lens.
Trace every consumer and render path of each value the artifact changes — every place that reads, displays, stores, or recomputes it (server code, templates, client scripts, JSON payloads, docs, tests). Confirm each one is updated or explicitly out of scope. Report any consumer the change misses as a finding; a value changed in one place and stale in another is exactly the defect this lens exists to catch.
Approach the artifact adversarially: look for hidden flaws, omitted cases, and weak assumptions before giving credit.
Code context files are provided above. Before asserting any finding, check whether it is confirmed or refuted by the provided code. Each finding must include an evidence tag:
  [CODE-CONFIRMED] — the code directly supports this finding
  [CODE-REFUTED] — the code contradicts this finding (do not include as a finding)
  [UNVERIFIED] — relevant code was not provided; treat as lower confidence
Only assign HIGH severity to CODE-CONFIRMED findings.
The full review artifact text is included below in this prompt.
Output length is limited and a response can be cut off before it finishes. Emit the required structured output first — the "## Findings" section, the "## Residual Risks" section, and the fenced findings JSON block — before any exploratory narration or extended analysis. Do not spend your early output investigating the artifact in prose and save the required format for last: a response cut off mid-investigation must still contain parseable findings. Any deeper supporting analysis you want to add can follow after the required sections and JSON block are complete.
Return markdown using exactly these sections:
## Findings
## Residual Risks
Keep the response concrete and ordered by severity.
End your review with exactly one fenced JSON block — the machine-readable findings summary:
```json
{"reviewed": true, "findings": [{"severity": "HIGH", "title": "<short title>", "detail": "<one-sentence detail>"}]}
```
Severity must be one of: CRITICAL, HIGH, MEDIUM, LOW. Include one entry per finding in your "## Findings" section.
If you found no issues, the block must be the affirmative clean bill exactly: {"reviewed": true, "findings": []}
This JSON block is required, is machine-parsed, and must be the last thing in your response.

Context: user.py
"""User table — one row per Google identity."""

import enum
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enum_types import FlexibleEnumType


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    given_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    family_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        FlexibleEnumType(UserRole, length=16),
        nullable=False,
        default=UserRole.USER,
        server_default=UserRole.USER.value,
    )
    # Public, chosen display name shown as "by @handle" on the leaderboard.
    # `handle` keeps the case the user typed; `handle_key` is its lowercased form
    # and carries the unique index, so uniqueness is case-insensitive while the
    # displayed capitalization is preserved. Both are NULL until the user picks
    # one (required before owning an agent). Google identity stays the auth layer;
    # the handle is display only and never used for authentication.
    handle: Mapped[str | None] = mapped_column(String(20), nullable=True)
    handle_key: Mapped[str | None] = mapped_column(
        String(20), unique=True, index=True, nullable=True
    )
    # When the handle was last set or changed — powers the 30-day change cooldown.
    handle_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


Context: admin_user_actions.py
"""Admin user-action helpers: disable, enable, promote, demote, handle_reset.

Each helper:
  1. Loads the target user with an optional row-lock (Postgres only).
  2. Applies a no-op guard - returns without writing an audit row if the
     target is already in the requested state.
  3. Refuses floor-admin targets for demote and disable (case-insensitive
     match against settings.platform_admin_emails_set).
  4. Mutates the user and writes exactly one AdminAuditLog row in the
     same transaction.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.admin_audit_log import AdminAction, AdminAuditLog
from app.models.user import User, UserRole


def _is_floor_admin(user: User) -> bool:
    return user.email.lower() in settings.platform_admin_emails_set


async def _load_target(db: AsyncSession, target_id: int) -> User:
    """Load user by id with an optional write-lock (skipped on SQLite)."""
    use_lock = db.sync_session.get_bind().dialect.name != "sqlite"
    stmt = select(User).where(User.id == target_id)
    if use_lock:
        stmt = stmt.with_for_update()
    target = (await db.execute(stmt)).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return target


def _write_audit(
    db: AsyncSession,
    *,
    actor: User,
    target: User,
    action: AdminAction,
    reason: str | None = None,
) -> None:
    db.add(
        AdminAuditLog(
            actor_user_id=actor.id,
            target_user_id=target.id,
            action=action,
            reason=reason,
        )
    )


async def disable_user(
    db: AsyncSession,
    *,
    actor: User,
    target_id: int,
    reason: str | None = None,
) -> None:
    target = await _load_target(db, target_id)
    if target.disabled_at is not None:
        return
    if _is_floor_admin(target):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot disable a platform-admin-floor user.",
        )
    target.disabled_at = datetime.now(timezone.utc)
    _write_audit(db, actor=actor, target=target, action=AdminAction.disable, reason=reason)


async def enable_user(
    db: AsyncSession,
    *,
    actor: User,
    target_id: int,
    reason: str | None = None,
) -> None:
    target = await _load_target(db, target_id)
    if target.disabled_at is None:
        return
    target.disabled_at = None
    _write_audit(db, actor=actor, target=target, action=AdminAction.enable, reason=reason)


async def promote_user(
    db: AsyncSession,
    *,
    actor: User,
    target_id: int,
    reason: str | None = None,
) -> None:
    target = await _load_target(db, target_id)
    if target.role == UserRole.ADMIN:
        return
    target.role = UserRole.ADMIN
    _write_audit(db, actor=actor, target=target, action=AdminAction.promote, reason=reason)


async def demote_user(
    db: AsyncSession,
    *,
    actor: User,
    target_id: int,
    reason: str | None = None,
) -> None:
    target = await _load_target(db, target_id)
    if target.role == UserRole.USER:
        return
    if _is_floor_admin(target):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot demote a platform-admin-floor user.",
        )
    target.role = UserRole.USER
    _write_audit(db, actor=actor, target=target, action=AdminAction.demote, reason=reason)


async def reset_handle(
    db: AsyncSession,
    *,
    actor: User,
    target_id: int,
    reason: str | None = None,
) -> None:
    target = await _load_target(db, target_id)
    if target.handle is None:
        return
    target.handle = None
    target.handle_key = None
    target.handle_changed_at = None
    _write_audit(db, actor=actor, target=target, action=AdminAction.handle_reset, reason=reason)


Context: test_mcp.py
"""MCP server smoke tests and OAuth bridge checks."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.main import app
from app.models.base import Base
from fastmcp.server.dependencies import AccessToken


def _token(*, sub: str = "sub-123", email: str = "agent@example.com") -> AccessToken:
    return AccessToken(
        token="access-token-1",
        client_id=sub,
        scopes=["openid", "email", "profile"],
        subject=sub,
        claims={
            "sub": sub,
            "email": email,
            "name": "Agent One",
            "given_name": "Agent",
            "family_name": "One",
            "email_verified": True,
        },
    )


@pytest.fixture
async def db_session_factory(
    engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)


async def test_mcp_tools_registered() -> None:
    """The MCP tool set matches the cleaned 7-tool surface."""
    from mcp_server.server import mcp_app

    tool_names = {tool.name for tool in await mcp_app.list_tools()}
    assert tool_names == {
        "get_instructions",
        "get_next_turn",
        "get_next_turns",
        "submit_talk",
        "submit_action",
        "get_chat",
        "get_game_state",
    }


async def test_mcp_tool_schema_fields_are_frozen() -> None:
    """Pin each tool's LLM-visible input fields.

    This is the behavior-preserving guard for the server.py module split: the
    7 tools must register with exactly these names and input-schema fields after
    being moved into mcp_tools.register_tools(). A drift here means a tool's
    public shape changed, which would break the AI clients calling it.
    """
    from mcp_server.server import mcp_app

    actual = {
        t.name: sorted((t.parameters or {}).get("properties", {}).keys())
        for t in await mcp_app.list_tools()
    }
    assert actual == {
        "get_chat": ["game_id", "match_id", "since"],
        "get_game_state": ["game_id", "match_id"],
        "get_instructions": ["agent_id", "match_id"],
        "get_next_turn": ["agent_id"],
        "get_next_turns": [],
        "submit_action": [
            "action",
            "agent_turn_token",
            "game_id",
            "match_id",
            "message",
            "target_id",
            "thinking",
            "turn_token",
        ],
        "submit_talk": [
            "agent_turn_token",
            "game_id",
            "match_id",
            "message",
            "thinking",
            "turn_token",
        ],
    }


async def test_get_next_turn_exposes_agent_id_for_parallel_play() -> None:
    """The agent_id selector is LLM-visible so a client can run one loop per agent."""
    from mcp_server.server import mcp_app

    schemas = {
        t.name: (t.parameters or {}).get("properties", {})
        for t in await mcp_app.list_tools()
    }
    assert "agent_id" in schemas["get_next_turn"]
    assert "agent_id" in schemas["get_instructions"]
    # The batch discovery tool takes no LLM-facing args beyond the hidden plumbing.
    assert "token" not in schemas["get_next_turns"]
    assert "db" not in schemas["get_next_turns"]


async def test_authed_tools_hide_token_and_db_from_schema() -> None:
    """OAuth plumbing stays hidden from the LLM-visible tool schema."""
    from mcp_server.server import mcp_app

    schemas = {
        t.name: (t.parameters or {}).get("properties", {})
        for t in await mcp_app.list_tools()
    }
    for name in (
        "get_instructions",
        "get_next_turn",
        "get_next_turns",
        "submit_talk",
        "submit_action",
        "get_game_state",
        "get_chat",
    ):
        assert "token" not in schemas[name]
        assert "db" not in schemas[name]


async def test_submit_action_forwards_agent_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The act tool carries the agent's private `thinking` to the play layer.

    It used to hardcode thinking="" so an MCP agent could never record the
    reasoning behind its move — the replay showed thinking for scripted bots but
    never for real agents. This pins that submit_action now forwards whatever
    reasoning the agent sends.
    """
    from mcp_server import connection_identity, mcp_tools, server

    captured: dict[str, object] = {}

    async def fake_resolve(
        db: object, token: object, *, match_id: str, agent_turn_token: str
    ) -> tuple[object, object, object, object]:
        return (
            object(),
            object(),
            SimpleNamespace(id=7),
            SimpleNamespace(id=99, seat_name="AI-17"),
        )

    async def fake_play_submit_action(db: object, **kwargs: object) -> dict[str, str]:
        captured.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(connection_identity, "_resolve_oauth_player", fake_resolve)
    monkeypatch.setattr(mcp_tools, "play_submit_action", fake_play_submit_action)

    await server.submit_action(
        match_id="M_001",
        action="HURT",
        target_id="AI-2",
        message="watch out",
        thinking="they are pulling ahead, so I strike the leader",
        turn_token="tt",
        agent_turn_token="att",
        token=_token(),
        db=object(),
    )

    assert captured["message"] == "watch out"
    assert captured["thinking"] == "they are pulling ahead, so I strike the leader"


async def test_mcp_discovery_requires_bearer_token() -> None:
    """The MCP endpoint advertises OAuth discovery instead of a secret header.

    The server runs stateless (no in-memory session map, so a redeploy can't
    orphan a client). Stateless streamable-HTTP serves no server->client SSE
    stream, so an unauthenticated GET is 405, not the auth challenge. Real clients
    discover OAuth on the POST `initialize` path they actually use: an
    unauthenticated POST returns 401 with the Bearer challenge and the
    resource-metadata discovery URL.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # No server-push stream in stateless mode.
        assert (await client.get("/mcp")).status_code == 405

        # The OAuth challenge rides the POST initialize path real clients use.
        init = await client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "gemini-cli-mcp-client", "version": "1"},
                },
            },
        )
        assert init.status_code == 401
        challenge = init.headers["www-authenticate"]
        assert "Bearer" in challenge
        assert "/.well-known/oauth-protected-resource/mcp" in challenge

        prm = await client.get("/.well-known/oauth-protected-resource/mcp")
        assert prm.status_code == 200
        assert prm.json()["authorization_servers"]

        as_metadata = await client.get("/.well-known/oauth-authorization-server")
        assert as_metadata.status_code == 200
        assert as_metadata.json()["authorization_endpoint"].endswith("/authorize")
        assert as_metadata.json()["token_endpoint"].endswith("/token")
        assert as_metadata.json()["registration_endpoint"].endswith("/register")


async def test_get_next_turn_uses_google_identity_and_mcp_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bridge resolves the Google identity and builds the MCP connection."""
    from mcp_server import connection_identity, mcp_tools, server

    captured: dict[str, object] = {}

    async def fake_sync_google_user(db: object, userinfo: object) -> SimpleNamespace:
        captured["userinfo"] = userinfo
        return SimpleNamespace(id=42, google_sub=userinfo.sub, disabled_at=None)

    async def fake_mcp_connection_for(
        db: object, user: object, *, provider: object = None, oauth_client_id: object = None
    ) -> SimpleNamespace:
        captured["user"] = user
        return SimpleNamespace(id=7, key_lookup="lookup-7", user=user)

    def fake_assert_connection_usable(connection: object) -> None:
        captured["checked_connection"] = connection

    async def fake_mark_seen(db: object, connection: object, *, key_hash: str) -> None:
        captured["mark_seen_key_hash"] = key_hash
        captured["mark_seen_connection"] = connection

    async def fake_get_next_turn(
        db: object,
        connection: object,
        *,
        agent_id: int | None = None,
        max_hold_seconds: float | None = None,
    ) -> dict[str, object]:
        captured["service_connection"] = connection
        captured["max_hold_seconds"] = max_hold_seconds
        captured["agent_id"] = agent_id
        return {"status": "waiting", "next_poll_after_seconds": 2}

    monkeypatch.setattr(connection_identity, "sync_google_user", fake_sync_google_user)
    monkeypatch.setattr(connection_identity, "mcp_connection_for", fake_mcp_connection_for)
    monkeypatch.setattr(
        connection_identity, "assert_connection_usable", fake_assert_connection_usable
    )
    monkeypatch.setattr(connection_identity, "mark_seen", fake_mark_seen)
    monkeypatch.setattr(mcp_tools, "play_get_next_turn", fake_get_next_turn)

    result = await server.get_next_turn(token=_token(), db=object())

    assert result["status"] == "waiting"
    assert captured["userinfo"].sub == "sub-123"
    assert captured["userinfo"].email == "agent@example.com"
    assert captured["user"].google_sub == "sub-123"
    assert captured["checked_connection"].key_lookup == "lookup-7"
    assert captured["mark_seen_key_hash"] == "lookup-7"
    assert captured["service_connection"].id == 7
    # The MCP path adds NO cap of its own: hold length is decided in one place
    # (pace_idle / `agent_long_poll_hold_seconds`). A second number here is how a
    # hold change silently does nothing — it used to clamp every hold to 25s.
    assert captured["max_hold_seconds"] is None


async def test_get_next_turn_strips_duplicate_static_for_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_server import connection_identity, mcp_tools, server

    async def fake_sync_google_user(db: object, userinfo: object) -> SimpleNamespace:
        return SimpleNamespace(id=42, google_sub=userinfo.sub, disabled_at=None)

    async def fake_mcp_connection_for(
        db: object, user: object, *, provider: object = None, oauth_client_id: object = None
    ) -> SimpleNamespace:
        return SimpleNamespace(id=7, key_lookup="lookup-7", user=user)

    def fake_assert_connection_usable(connection: object) -> None:
        pass

    async def fake_mark_seen(db: object, connection: object, *, key_hash: str) -> None:
        pass

    async def fake_get_next_turn(
        db: object,
        connection: object,
        *,
        agent_id: int | None = None,
        max_hold_seconds: float | None = None,
    ) -> dict[str, object]:
        return {
            "status": "your_turn",
            "match_id": "M_001",
            "turn_token": "turn-1",
            "agent_turn_token": "turn-1:1:M_001",
            "strategy": "keep it short",
            "static": {
                "match_id": "M_001",
                "rules_version": "v1",
                "rules": "rules text",
                "base_prompt": "prompt text",
                "your_strategy": "keep it short",
                "total_rounds": 7,
                "turns_per_round": 7,
                "your_agent_id": "A",
                "all_agent_ids": ["A", "B"],
                "coach_note": "stay calm",
            },
            "history": [],
            "scoreboard": [],
            "current": {"phase": "act", "turn_token": "turn-1"},
            "your_private_state": {"dice": [1, 2, 3]},
            "public_state": {"board": 1},
        }

    monkeypatch.setattr(connection_identity, "sync_google_user", fake_sync_google_user)
    monkeypatch.setattr(connection_identity, "mcp_connection_for", fake_mcp_connection_for)
    monkeypatch.setattr(
        connection_identity, "assert_connection_usable", fake_assert_connection_usable
    )
    monkeypatch.setattr(connection_identity, "mark_seen", fake_mark_seen)
    monkeypatch.setattr(mcp_tools, "play_get_next_turn", fake_get_next_turn)

    result = await server.get_next_turn(token=_token(), db=object())

    assert result["status"] == "your_turn"
    assert "strategy" not in result
    assert "base_prompt" not in result["static"]
    assert "rules" not in result["static"]
    assert "your_strategy" not in result["static"]
    assert result["static"]["coach_note"] == "stay calm"
    assert result["your_private_state"] == {"dice": [1, 2, 3]}
    assert result["public_state"] == {"board": 1}
    # History is the server's rolling window — the lean wrapper preserves it as-is
    # (it only strips the duplicated static prompt text, never the live history).
    assert result["history"] == []
    assert set(result["static"]) == {
        "match_id",
        "rules_version",
        "total_rounds",
        "turns_per_round",
        "your_agent_id",
        "all_agent_ids",
        "coach_note",
    }


async def test_get_next_turns_strips_duplicate_static_for_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_server import connection_identity, mcp_tools, server

    async def fake_sync_google_user(db: object, userinfo: object) -> SimpleNamespace:
        return SimpleNamespace(id=42, google_sub=userinfo.sub, disabled_at=None)

    async def fake_mcp_connection_for(
        db: object, user: object, *, provider: object = None, oauth_client_id: object = None
    ) -> SimpleNamespace:
        return SimpleNamespace(id=7, key_lookup="lookup-7", user=user)

    def fake_assert_connection_usable(connection: object) -> None:
        pass

    async def fake_mark_seen(db: object, connection: object, *, key_hash: str) -> None:
        pass

    async def fake_get_next_turns(db: object, connection: object) -> dict[str, object]:
        return {
            "status": "your_turn",
            "turns": [
                {
                    "status": "your_turn",
                    "match_id": "M_001",
                    "turn_token": "turn-1",
                    "agent_turn_token": "turn-1:1:M_001",
                    "strategy": "keep it short",
                    "static": {
                        "match_id": "M_001",
                        "rules_version": "v1",
                        "rules": "rules text",
                        "base_prompt": "prompt text",
                        "your_strategy": "keep it short",
                        "total_rounds": 7,
                        "turns_per_round": 7,
                        "your_agent_id": "A",
                        "all_agent_ids": ["A", "B"],
                    },
                    "history": [],
                    "scoreboard": [],
                    "current": {"phase": "act", "turn_token": "turn-1"},
                    "your_private_state": {"dice": [1, 2, 3]},
                    "public_state": {"board": 1},
                }
            ],
        }

    monkeypatch.setattr(connection_identity, "sync_google_user", fake_sync_google_user)
    monkeypatch.setattr(connection_identity, "mcp_connection_for", fake_mcp_connection_for)
    monkeypatch.setattr(
        connection_identity, "assert_connection_usable", fake_assert_connection_usable
    )
    monkeypatch.setattr(connection_identity, "mark_seen", fake_mark_seen)
    monkeypatch.setattr(mcp_tools, "play_get_next_turns", fake_get_next_turns)

    result = await server.get_next_turns(token=_token(), db=object())

    assert result["status"] == "your_turn"
    turn = result["turns"][0]
    assert "strategy" not in turn
    assert "base_prompt" not in turn["static"]
    assert "rules" not in turn["static"]
    assert "your_strategy" not in turn["static"]
    assert turn["static"]["match_id"] == "M_001"
    assert turn["your_private_state"] == {"dice": [1, 2, 3]}
    assert turn["public_state"] == {"board": 1}
    assert turn["history"] == []  # rolling window preserved, not stripped


async def test_get_game_state_requires_auth() -> None:
    """Anonymous MCP calls are rejected before any game state is read."""
    from mcp_server import server

    with pytest.raises(RuntimeError, match="verified access token"):
        await server.get_game_state(match_id="M_001", db=object())


def test_mcp_asgi_app_constructed() -> None:
    """The MCP HTTP app is built and importable."""
    from mcp_server.server import asgi_app

    assert asgi_app is not None


def test_mcp_root_mount_and_public_mcp_route() -> None:
    """The FastAPI app mounts the MCP app at the root, and the MCP route exists."""
    from app.main import app as fastapi_app
    from mcp_server.server import asgi_app

    # Use the public url_path_for() rather than scanning app.routes for a flat
    # "/" entry: FastAPI >=0.137 registers sub-routers lazily (_IncludedRouter),
    # so the home route is not a flat list entry until the app is built.
    # url_path_for() resolves through lazy includes and works on both behaviours.
    assert fastapi_app.url_path_for("home") == "/"
    assert any(getattr(route, "path", None) == "/mcp" for route in asgi_app.routes)


async def test_pull_tools_use_shared_oauth_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pull tools all resolve through the OAuth identity path."""
    from mcp_server import connection_identity, mcp_tools, server

    captured: dict[str, object] = {}

    async def fake_sync_google_user(db: object, userinfo: object) -> SimpleNamespace:
        captured["userinfo"] = userinfo
        return SimpleNamespace(id=42, google_sub=userinfo.sub, disabled_at=None)

    async def fake_mcp_connection_for(
        db: object, user: object, *, provider: object = None, oauth_client_id: object = None
    ) -> SimpleNamespace:
        return SimpleNamespace(id=7, key_lookup="lookup-7", user=user)

    def fake_assert_connection_usable(connection: object) -> None:
        pass

    async def fake_mark_seen(db: object, connection: object, *, key_hash: str) -> None:
        pass

    async def fake_require_agent_player(
        *,
        match_id: str,
        db: object,
        connection: object,
        agent_id: int | None = None,
        agent_turn_token: str | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(id=99, agent_id=17, seat_name="AI-17")

    async def fake_pull(
        db: object,
        *,
        match_id: str,
        player: object,
        rate_state: dict[tuple[int, str], float],
        **kwargs: object,
    ) -> dict[str, object]:
        captured["match_id"] = match_id
        captured["player"] = player
        captured["kwargs"] = kwargs
        captured["rate_state"] = rate_state
        return {"status": "ok"}

    monkeypatch.setattr(connection_identity, "sync_google_user", fake_sync_google_user)
    monkeypatch.setattr(connection_identity, "mcp_connection_for", fake_mcp_connection_for)
    monkeypatch.setattr(
        connection_identity, "assert_connection_usable", fake_assert_connection_usable
    )
    monkeypatch.setattr(connection_identity, "mark_seen", fake_mark_seen)
    monkeypatch.setattr(
        connection_identity, "require_agent_player", fake_require_agent_player
    )
    monkeypatch.setattr(mcp_tools, "chat_transcript", fake_pull)

    token = _token()
    assert await server.get_chat(match_id="M_001", token=token, db=object()) == {
        "status": "ok"
    }


# ---------------------------------------------------------------------------
# Stateless-mode client identity — regression tests for spec 016
# ---------------------------------------------------------------------------


def test_dcr_client_id_from_request_decodes_bearer_jwt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_dcr_client_id_from_request() reads the per-client DCR id from the bearer JWT.

    The reference JWT FastMCP issues to a client carries its DCR client_id (a UUID)
    in the payload. We decode that payload to get a stable per-client key — unlike
    the validated AccessToken, whose client_id is the shared Google subject.
    """
    import base64
    import json

    from mcp_server import connection_identity, server

    payload = {"iss": "x", "client_id": "dcr-uuid-codex", "jti": "abc"}
    seg = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    jwt = f"HEADER.{seg}.SIG"
    fake_request = SimpleNamespace(headers={"authorization": f"Bearer {jwt}"})
    monkeypatch.setattr(connection_identity, "get_http_request", lambda: fake_request)

    assert server._dcr_client_id_from_request() == "dcr-uuid-codex"


def test_dcr_client_id_from_request_fails_open_without_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No/!bearer/opaque token → None, so the caller falls back to provider lookup."""
    from mcp_server import connection_identity, server

    for header in ({}, {"authorization": "Basic abc"}, {"authorization": "Bearer opaque"}):
        monkeypatch.setattr(
            connection_identity,
            "get_http_request",
            lambda h=header: SimpleNamespace(headers=h),
        )
        assert server._dcr_client_id_from_request() is None


async def test_multi_connection_user_resolves_via_oauth_client_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user with 2+ MCP connections resolves correctly via the per-client id.

    Full chain after the fix:
      1. the DCR client_id (from the bearer JWT) is passed as oauth_client_id
      2. mcp_connection_for(oauth_client_id=...) finds the matching connection
      3. tool call succeeds — no UNKNOWN_MCP_CLIENT
    """
    from app.models.connection import ConnectionStatus

    from mcp_server import connection_identity, server

    EXPECTED_CLIENT_ID = "dcr-uuid-client"  # the per-client DCR id used for routing

    async def fake_sync_google_user(db: object, userinfo: object) -> SimpleNamespace:
        return SimpleNamespace(id=42, google_sub=userinfo.sub, disabled_at=None)

    async def fake_mcp_connection_for(
        db: object,
        user: object,
        *,
        provider: object = None,
        oauth_client_id: object = None,
    ) -> SimpleNamespace | None:
        # Simulates a user with 2 connections: oauth_client_id picks the right one
        if oauth_client_id == EXPECTED_CLIENT_ID:
            return SimpleNamespace(
                deleted_at=None,
                status=ConnectionStatus.ACTIVE,
                key_lookup="some-key-hash",
            )
        return None

    monkeypatch.setattr(connection_identity, "sync_google_user", fake_sync_google_user)
    monkeypatch.setattr(connection_identity, "mcp_connection_for", fake_mcp_connection_for)
    monkeypatch.setattr(connection_identity, "assert_connection_usable", lambda conn: None)

    access_token, _userinfo, connection = await server._connection_from_token(
        object(), _token(), provider=None, oauth_client_id=EXPECTED_CLIENT_ID
    )
    assert connection is not None
    assert connection.key_lookup == "some-key-hash"


Artifact: spec.md
# Admin Engagement Dashboard + Signup-Source Capture: Spec

**Revision 3** — redesigned. Two review rounds, three reviewers each, produced 57
findings (24 HIGH). Revision 2 patched them one at a time; revision 3 accepts that
the patching was treating symptoms and changes the design.

**Feature:** an admin-only page (`/admin/engagement`) answering *where do new users
fall out on the way to playing?* and *which traffic source produced the people who
played?*, plus the durable records that make both answerable at all.

**Delivery path:** full Feature Factory. `silent-risk=yes`, `design-settled=no`,
`completeness-risk=yes`.

---

## Why revision 3 exists — the root cause

Revisions 1 and 2 modelled engagement as a **strict funnel**: one ordered path,
where a user counts at step N only if they cleared steps 1..N-1. Every review round
broke it, and the breakages had one shared cause.

**1. There is no single path. There are at least four.**

| Path | Shape |
|---|---|
| AI agent, web setup | handle → agent → connection → join → play |
| AI agent, MCP first | connection stamped with **no agent and no handle** |
| **Human manual play** | agent `kind='human'`, **no connection, ever** |
| Bots | house-owned, no real user journey |

Human play is not an edge case — it is **the default for every brand-new user**.
`_default_human_choice` ([web_join.py:113](../../../app/routes/web_join.py)) returns
`True` when a user has no history, and its own comment calls this "the no-setup
path". Under strict nesting, a person who signs up, plays manually every day, and
loves the game renders as *stuck at picked a handle*.

Strict nesting converts every unmodelled path into a fake drop. Round 1 found two
unmodelled paths; round 2 found three more. That is a design failing, not a
detail gap.

**2. The app deletes the evidence a drop-off report needs.** Four confirmed sites:

| What is deleted | Where | Cohort erased |
|---|---|---|
| Incomplete connection setups | `pending_connection_gc.py` (runs on ordinary page loads, not scheduled) | "started hookup, gave up" |
| Held seats, ~15 min | `seat_hold.py:87`, `:119` | "joined, AI never came online" |
| Agents with no game history | `agents_lifecycle.py:158` — archives only if `Player` rows exist | "built an agent, never played" |
| Matches, players, turns | admin `delete_match` | any past funnel |

Those are precisely the abandonment cohorts the feature exists to surface.

**Chris's decision, 2026-08-13:** record milestones durably as they happen, and
present them as independent counts rather than a strict waterfall.

---

## Design decisions

### D1 — Durable milestone records replace derived funnel steps

New append-only table `user_milestones`: one row the **first** time a user reaches
a milestone. `UNIQUE(user_id, milestone)` makes recording idempotent — a second
attempt is a no-op, not an error.

| Milestone | Recorded when |
|---|---|
| `signed_up` | a `User` row is created |
| `picked_handle` | `handle_key` first set |
| `set_up_a_way_to_play` | first `Agent` of kind `ai` **or** `human` |
| `ai_connected` | `first_connected_at` first stamped on a connection |
| `joined_match` | first `Player` row |
| `played_turn` | first genuine submission (D5) |
| `returned` | a genuine submission on a second distinct local day |

Why this fixes both root causes:
- **Deletion-proof.** The milestone row survives the connection setup being cleaned
  up, the seat being released, the agent being hard-deleted, the match being
  deleted. Nothing downstream removes it.
- **Path-agnostic.** No ordering is assumed. An MCP user who connects before
  building an agent records both, in whatever order they happen. A human player
  records `set_up_a_way_to_play` and never records `ai_connected` — and that is
  correct, not a drop.

### D2 — Independent counts, not strict nesting

Each milestone shows: how many users **in the signup cohort** ever reached it.
No truncation, no "must have cleared the step above".

The page presents them in the usual order with counts and the difference between
neighbours, but the difference is labelled **"fewer than the step above"**, not
"lost here" — because with multiple paths those are different claims.

`ai_connected` is additionally reported **as a share of users who chose an AI
agent**, not of all signups, so the human-play default does not read as an AI
setup failure.

### D3 — One recorder module, called from the event sites

`app/identity/milestones.py` exposes `record_milestone(db, user_id, milestone)`.
Every site calls that one function; no site writes the table directly.

Call sites: user creation (3 places, D8), handle set, agent create, connection
first-connect (web **and** MCP), player create, submission write.

**Advisory, never blocking.** A milestone write must never break the request that
triggered it — it is reporting, not game state. Wrapped and commented
`# fail-open: advisory only`, the one exception the repo's fail-loud rule allows.

### D4 — Backfill is partial, and says so

The migration backfills milestones from what today's tables can still prove.
It cannot recover deleted rows, so historical numbers are **floors, not totals**.

The page carries a dated line: *"Milestones before <deploy date> are
reconstructed from surviving records and undercount abandonment."* Without it the
first weeks would silently read as unusually healthy.

### D5 — "Played a turn" means a genuine human-or-agent move

Three kinds of row must not count:

1. **Missed deadlines.** A missed turn still writes a submission marked
   `was_defaulted=True` ([scoring.py:196](../../../app/games/hoard_hurt_help/scoring.py)) —
   4,614 of 20,276 rows in the dev DB.
2. **Null timestamps.** `/admin/reports` already treats a NULL `submitted_at` as
   defaulted ([admin_reports.py:182](../../../app/read_models/admin_reports.py)). Matching
   it keeps the two admin pages from reporting different numbers for one week.
3. **Autopilot.** `bots/service.py` auto-plays for a player who left, and those
   rows are not marked defaulted. An abandoner must not clear "played a turn".

Because the milestone is recorded at write time (D3), the recorder simply is not
called on those paths — no read-time filter to get wrong.

`connections.turns_played` is not used: it is per-connection, not per-user.

### D6 — First touch is captured in middleware, on the first page view

A visitor lands on `/?utm_source=hermesagent`, browses, then signs in. By then the
parameter is gone from the URL, so capture must happen on the **first request of a
session** and ride in the session through the OAuth round trip.

`FirstTouchMiddleware` records `{utm_source, utm_medium, utm_campaign,
referrer_host, landing_path, at}` once and never overwrites.

### D7 — Middleware ordering: added BEFORE `SessionMiddleware`

`add_middleware` **inserts at position 0**, so the last-added middleware is the
outermost — `main.py:259` documents this on `CanonicalHostMiddleware`.
`FirstTouchMiddleware` must therefore be added **before** the `SessionMiddleware`
call at [main.py:240](../../../app/main.py) so `request.session` exists when it runs.

Placed after, capture silently records nothing forever. Confirmed correct by three
independent reviewers.

Skip prefixes, verified against the real routes: `/static`, `/healthz`, `/api`,
`/mcp`, `/openapi.json`, `/.well-known`, `/auth`. (Revision 1's `/sse` does not
exist; `/.well-known` and the OAuth surface are reachable through the catch-all
root mount at `main.py:314`.)

### D8 — This sets a cookie for anonymous visitors, and that has an open obligation

**Chris's decision, 2026-08-13**, superseding the discovery non-goal.

Today a visitor who only browses gets no cookie; one who clicks sign in already
does, because `google_login` writes to the session ([auth.py:85](../../../app/routes/auth.py)).
The change is that **everyone** gets one.

Facts that matter, all confirmed:
- `SessionMiddleware` sets **no `max_age`** ([main.py:240](../../../app/main.py)), so
  Starlette's default applies: a **persistent 14-day cookie**, not a session
  cookie. Attribution therefore cannot survive longer than 14 days, and
  persistent-vs-session is the distinction consent rules turn on.
- The session is a **signed cookie**, not server storage, with a ~4KB browser
  limit. Existing `fresh_connection_key_setup_{id}` entries are written and never
  popped, so the budget is already leaking; first touch must be length-capped **at
  capture time**, before it enters the cookie.
- **The site has no privacy policy and no cookie notice.** Nothing in `app/`.

> **OPEN OBLIGATION — must be resolved before this reaches production.** This
> feature adds a persistent tracking cookie to a site with no disclosure surface.
> Building it is fine; shipping it to real users without a privacy note is a
> decision Chris has to make deliberately. Raised again at the PR.

### D9 — Source is derived, stored raw, length-capped

Stored on `users`: `first_utm_source`, `first_utm_medium`, `first_utm_campaign`,
`first_referrer_host`, `first_landing_path`.

Label precedence: `utm_source` → `first_referrer_host` → `"direct"`.

- Referrer **host** only, never the full URL (query strings carry personal data).
- **NULL renders `"unknown"`, never `"direct"`** — otherwise the largest row in the
  source table silently means "we failed to capture this".
- MCP-created users record `"mcp"` in a **dedicated `first_source_channel` column**,
  not in `first_utm_source`, so a real `?utm_source=mcp` cannot collide with it.
- Known limit, stated on the page: a visitor who arrives from Reddit and whose
  *first completed sign-in* is the MCP OAuth flow is attributed to `mcp`, losing
  the campaign. Fixing it needs first-touch plumbed through MCP OAuth and is out
  of scope.

### D10 — Internal users are marked by a stored flag

Excluding by email is unstable: `sync_google_user` **rewrites `users.email`** on
later logins. Excluding by role is equally unstable: `promote_user` / `demote_user`
change it after the fact.

So: a stored `users.is_internal` boolean, set at creation, **never recomputed** —
not from email, not from role.

Set at every site a user row is born:

| Site | Rule |
|---|---|
| `auth.py:41` (Google sign-in) | configured internal-domain list |
| `app/engine/bots/seating.py:51` | always `True` |
| `app/routes/dev_login.py:51` | always `True` |

**Why the flag carries the weight.** `agents.kind != 'ai'` is nowhere near
sufficient — measured in the dev DB, `ludumlabs@house.local` (264 player rows),
two sibling ludumlabs accounts (120 each), `sims@agentludum.local` (40) and
`harness-A/B/C@local.test` all run agents marked **real AI**. Only
`bots@agentludum.local` is marked a bot. Over 500 of 646 player rows are internal.

Backfill seed domains: `agentludum.local`, `house.local`, `local.test`,
`dev@localhost`. **The backfill and the creation rule must use one shared
predicate** so they cannot disagree.

Those `.local` accounts cannot have come through Google sign-in (Google issues no
token for a `.local` address), so a fourth creation path exists outside the app —
almost certainly `scripts/`. The backfill is therefore the only thing that will
ever flag them, which is why it must be verified after deploy by reading back the
flagged row count against the known internal accounts.

**Correctable and findable:** a two-way toggle on `/admin/users/{id}` rendered
**outside** the `{% if not floor_admin %}` gate ([user_detail.html:16](../../../app/templates/admin/user_detail.html))
— floor admins are exactly the accounts the backfill flags. Plus an `internal`
column on `/admin/users`. `AdminAction` gains **two** values, `mark_internal` and
`unmark_internal` (both under the 16-char limit), matching how every other
reversible admin action is paired.

### D11 — Distinct users, one timezone, smoke tests excluded

- Every people-count is `COUNT(DISTINCT users.id)`.
- "Second distinct day" uses **the page window's timezone**, and the window
  control **defaults to the browser's timezone, not UTC** — a US evening session
  spans two UTC days and would otherwise read as a return that never happened.
  A session crossing local midnight is still a known false positive, accepted and
  noted on the page.
- Smoke-test matches (`TEST_NAME_PREFIX`) excluded, matching
  [admin_reports.py:109](../../../app/read_models/admin_reports.py).

### D12 — No visitor count, no live "incomplete setups" tile

Anonymous visitor counts need a third-party analytics tool — Chris's separate
decision. The milestone list starts at `signed_up`.

Revision 2's "incomplete setups right now" tile is **dropped**: its cleanup job is
not scheduled (it runs only when someone loads `/me/connections` or
`/me/agents/new`), so the number means "however many have accumulated since
someone last visited those pages" — not a snapshot of anything. It also cannot
have a period-over-period comparison, since the rows are hard-deleted.

---

## What we are building

1. `app/models/user_milestone.py` — the append-only table.
2. `app/identity/milestones.py` — `record_milestone`, the single writer (D3).
3. `app/identity/first_touch.py` — `FirstTouchMiddleware` (D6, D7), plus clearing
   `first_touch` in `clear_session` so a second user in the same browser does not
   inherit the first user's source.
4. `app/identity/internal_accounts.py` — the one shared internal predicate used by
   both the creation rule and the migration backfill (D10).
5. `app/models/user.py` + migration — source columns, `first_source_channel`,
   `is_internal`, the new table, and the two backfills (milestones D4,
   internal flag D10). Additive, reversible, `op.batch_alter_table` for constraint
   operations (SQLite dev DB).
6. Recorder calls at the event sites: `auth.py` (+ the two MCP callers in
   `mcp_server/`), `handle_web.py`, `agents_create.py`, `mcp_connection.py`,
   `web_join.py`, and the submission path.
7. `app/read_models/engagement_milestones.py` — counts per milestone over a signup
   cohort, plus the stuck list (users and their furthest milestone; handle-less
   users shown by email; capped at 50 with a remainder count).
8. `app/read_models/signup_sources.py` — source → signups → played, distinct users.
9. `app/routes/admin_engagement.py` + `app/templates/admin/engagement.html`.
10. `app/services/admin_user_actions.py` — the internal toggle, following the
    existing lock / no-op-guard / audit contract that every other admin action
    uses.
11. Nav and admin surfaces: `base.html` (Platform admin submenu),
    `admin/dashboard.html` (the second admin nav surface), `users_list.html`
    (new column — its hardcoded `colspan="7"` becomes 8), `user_detail.html`.
12. `tests/test_mcp.py` — five monkeypatched two-parameter `sync_google_user`
    fakes break when the signature gains an argument.

**The three summary numbers** (revision 2's fourth is dropped, D12): new signups in
the window; users who played a genuine turn in the window; genuine turns in the
window. Each shows a comparison with the preceding window of equal length; when
the window is unbounded (the default) no comparison is shown, because "previous
period" is undefined there.

---

## Acceptance criteria

Authoritative list lives in `state.json`, kept in lockstep with this revision.
Every criterion has a matching test in the plan below.

1. `/admin/engagement` exists, is in the Platform admin submenu, 403s for a
   non-admin.
2. A milestone row is written exactly once per user per milestone; a second
   attempt is a silent no-op.
3. A milestone survives deletion of the row that caused it — setup GC, seat
   release, agent hard-delete, match delete.
4. **A human player (agent kind `human`, no connection) is counted at
   `set_up_a_way_to_play`, `joined_match`, `played_turn` and `returned`.**
5. **An MCP user who connects before building an agent is counted at both, in
   either order, with no invented drop.**
6. Milestone counts are independent — no count is suppressed because an earlier
   milestone is missing.
7. `ai_connected` is also reported as a share of AI-agent users, not of all
   signups.
8. `played_turn` is not recorded for a defaulted submission, a NULL-timestamp
   submission, or an autopilot submission.
9. Bots and internal users excluded everywhere, via the stored flag.
10. `is_internal` set at all three in-app creation sites; backfill and creation
    rule share one predicate.
11. `is_internal` survives an email rewrite **and** a role promote/demote.
12. First touch survives navigation and the OAuth round trip; cleared on sign-out.
13. MCP-created users record channel `"mcp"` in its own column; a real
    `?utm_source=mcp` does not collide.
14. Uncaptured source renders `"unknown"`, never `"direct"`.
15. Every people-count is distinct users.
16. Return detection uses the window timezone, defaulting to the browser's.
17. Smoke-test matches excluded.
18. Stuck list labels handle-less users, caps at 50, shows a remainder count.
19. The page carries the reconstructed-history note (D4) and the MCP-attribution
    limit (D9).
20. Migration additive and reversible; `alembic upgrade head` then `downgrade`
    clean on SQLite.
21. `/admin/users/{id}` toggles `is_internal` both ways, renders for floor-admin
    targets, and audit-logs via the two new `AdminAction` values;
    `/admin/users` shows the column.
22. Milestone recording and first-touch capture are advisory: a failure logs and
    never breaks the request that triggered it.

## Non-goals

1. No third-party analytics service.
2. No privacy policy or cookie notice — **but see D8's open obligation**; this is
   deferred, not dismissed.
3. No first-touch plumbing through the MCP OAuth flow (D9's stated limit).
4. No recovery of already-deleted history — backfill is a floor (D4).
5. No cohort retention grid.
6. No changes to `/admin/reports`.

---

## Test plan

**Milestones**
- Idempotent: recording twice writes one row.
- Survives each of the four deletions in the root-cause table.
- **Human player reaches play and return milestones** (AC4).
- **MCP user connects before agent; both recorded; no invented drop** (AC5).
- Counts are independent: a user missing `picked_handle` still counts at
  `played_turn`.
- Not recorded for defaulted, NULL-timestamp, or autopilot submissions.
- Recorder raising does not fail the triggering request.

**Exclusion**
- Internal user excluded from every number on the page.
- Flag survives `sync_google_user` rewriting the email.
- Flag survives promote then demote.
- Backfill and creation rule agree on the same fixture set.
- Bots-user and dev-login users created internal.
- Toggle moves a user in and out, writes an audit row, renders for a floor admin.

**Capture**
- Land with UTM, navigate, sign in → recorded.
- External referrer stored as host; internal referrer treated as direct.
- No overwrite on a second visit or a returning user.
- Over-long values capped at capture, before the cookie.
- Sign out, sign in as someone else → no inheritance.
- MCP sign-in records channel `mcp`, and `?utm_source=mcp` stays distinct.
- Capture raising does not fail the page.

**Page**
- 403 non-admin, 200 admin.
- Empty database renders with no divide-by-zero.
- Distinct users: one user, three agents, twelve player rows → counts once.
- US evening session spanning two UTC days is not a return.
- Smoke-test matches contribute nothing.
- Both explanatory notes render.

**Migration**
- `upgrade` then `downgrade` clean on SQLite.
- Milestone backfill produces floors consistent with surviving rows.

---

## Review outcomes

57 findings across two rounds and three reviewers (24 HIGH). Every HIGH was
verified against the real code by the orchestrator before being accepted — several
reviewer claims were checked and one was found overstated (revision 2's "anonymous
visitors get no cookie at all"; `google_login` already writes a session).

**Round 1 (30 findings)** produced revision 2: stable internal flag, distinct-user
counting, gate-ladder ordering, dropped setup step, three `sync_google_user`
callers, no-show filtering, timezone and smoke-test consistency.

**Round 2 (27 findings)** showed revision 2's patches were symptom-level, and
produced this redesign. Decisive findings: `AgentKind.HUMAN` is the **default**
new-user path and was invisible to the funnel; held seats and history-less agents
are hard-deleted; the cleanup job is unscheduled so the "24-hour snapshot" tile was
meaningless; the session cookie is persistent for 14 days; the site has no privacy
disclosure at all.

**Escalated to Chris and decided by him:** (1) the anonymous-visitor cookie — full
attribution chosen, D8; (2) the funnel shape — durable milestones with independent
counts chosen over patching strict nesting, D1/D2.

**Verified correct across all three reviewers, not findings:** D7's middleware
ordering; `was_defaulted` being NOT NULL with a `false` server default;
`AdminAction` having room for the new values; the `floor_admin` template gate.

**Record-keeping note.** The round-2 requirements review's machine-readable JSON
block was truncated by one closing brace, so assembly rejected it. The orchestrator
repaired that single character rather than re-running a 130k-token review; the
repaired block parses to 14 findings, matching the count the reviewer reported in
prose. No finding text was added, removed, or altered.

**Reviewer independence caveat.** Round 1 had a cross-vendor lens (Codex) plus two
Claude lenses. Round 2 was three Claude lenses reviewing a Claude-authored spec,
because the Gemini CLI is dead on this machine (`IneligibleTierError` — Google
ended the individual tier). Round 2's agreement is therefore weaker evidence than
round 1's. Codex re-enters at the plan checkpoint.


Return only markdown with exactly these sections:
## Findings
## Residual Risks
Do not include any other sections. After the Residual Risks section, end with the required fenced findings JSON block described above.