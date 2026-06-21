"""US-10: cross-device dashboard access.

Two browser sessions with the same Google identity see the same games.
"""

import base64
import json

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner

from app.config import settings
from app.main import app
from app.models import Match, GameState, Player, User
from tests.factories import make_agent
from datetime import datetime, timedelta, timezone


def _cookies(user_id: int) -> dict:
    signer = TimestampSigner(settings.session_secret)
    payload = base64.b64encode(json.dumps({"user_id": user_id}).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


@pytest.mark.asyncio
async def test_two_sessions_same_user_see_same_games(reset_db):
    """Sign-in on device A and device B (same Google sub) both see /me/matches content."""
    async with reset_db() as db:
        u = User(google_sub="shared-sub", email="alice@test.com", name="Alice")
        db.add(u)
        await db.flush()
        g = Match(
            id="G_001",
            name="Cross-device",
            state=GameState.REGISTERING,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(g)
        await db.flush()
        agent, _ = await make_agent(db, u, name="AI_alice")
        db.add(
            Player(
                match_id="G_001",
                user_id=u.id,
                agent_id=agent.id,
                seat_name="AI_alice",
            )
        )
        await db.commit()
        user_id = u.id

    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as device_a:
        a = await device_a.get("/me/matches", cookies=_cookies(user_id))
        assert a.status_code == 200
        assert "Cross-device" in a.text
        assert "AI_alice" in a.text

    async with AsyncClient(transport=transport, base_url="http://test") as device_b:
        b = await device_b.get("/me/matches", cookies=_cookies(user_id))
        assert b.status_code == 200
        assert "Cross-device" in b.text
        assert "AI_alice" in b.text
