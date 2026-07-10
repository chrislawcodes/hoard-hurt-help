"""The agent setup screen leads with setup instructions, and the setup file is served."""


from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.factories import make_user
from tests.conftest import session_cookie as _cookie


async def test_agent_runner_scripts_are_served() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # One canonical runner is served.
        r = await c.get("/runners/agentludum_connector.py")
        assert r.status_code == 200
        # It's the real runner file, not an HTML page. The runner polls the
        # agent API (the endpoint name is built dynamically, so match the path
        # prefix rather than a full literal route).
        assert "/api/agent/" in r.text
        # The retired runner aliases now 404 (no resurrection surface).
        for name in (
            "agentludum_agent.py",
            "agentludum_agent_codex.py",
            "agentludum_agent_gemini.py",
        ):
            gone = await c.get(f"/runners/{name}")
            assert gone.status_code == 404, name
        # Anything not on the allowlist is a 404 — no path-traversal surface.
        bad = await c.get("/runners/secrets.py")
        assert bad.status_code == 404


async def test_retired_provider_setup_scripts_are_not_served() -> None:
    # One connector now drives every provider — the old per-provider shim
    # downloads were removed, so their names must 404 (no resurrection surface).
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        for name in ("agentludum_setup_hermes.py", "agentludum_setup_openclaw.py"):
            r = await c.get(f"/setup-files/{name}")
            assert r.status_code == 404, name
        # The one canonical connector is still served.
        ok = await c.get("/setup-files/agentludum_connector.py")
        assert ok.status_code == 200
        bad = await c.get("/setup-files/secrets.py")
        assert bad.status_code == 404


async def test_connections_page_shows_inline_setup_instructions(reset_db) -> None:
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()
        uid = user.id

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"hhh_session": _cookie(uid)},
        follow_redirects=True,
    ) as c:
        # The connections page itself shows the ready-to-run setup command inline —
        # no provider picking, no second page.
        r = await c.get("/me/connections")
    assert r.status_code == 200, r.text
    body = r.text
    # The connector command is now the secondary "always-on" option below MCP setup.
    assert "always-on connector" in body
    assert "Name this machine" in body
    assert "Paste this to your AI assistant:" in body
    assert "curl -fsSL" in body
    assert "/setup-files/agentludum_connector.py" in body
    # The one-command service install is the recommended path.
    assert "--install" in body
    assert "single standalone script" in body
    assert "background service" in body
    # macOS shows a "Background Items Added" notice on install; warn the operator
    # it's expected so they don't think something went wrong.
    assert "Background Items Added" in body
