"""Routing tests for canonical match URLs and legacy redirects."""

import pytest

from app.models import GameState
from tests.factories import seed_match


async def test_lobby_catalog_uses_canonical_games_path(client, reset_db):
    await seed_match(reset_db, "M_001", state=GameState.REGISTERING, name="Test Match")

    canonical = await client.get("/games/hoard-hurt-help")
    assert canonical.status_code == 200
    assert "Test Match" in canonical.text

    legacy = await client.get("/play/hoard-hurt-help", follow_redirects=False)
    assert legacy.status_code == 301
    assert legacy.headers["location"] == "/games/hoard-hurt-help"


@pytest.mark.parametrize(
    "legacy_path, expected_location",
    [
        ("/games/G_001", "/games/hoard-hurt-help/matches/M_001"),
        ("/games/G_001/live", "/games/hoard-hurt-help/matches/M_001/live"),
        ("/games/G_001/analysis", "/games/hoard-hurt-help/matches/M_001/analysis"),
        (
            "/games/G_001/analysis/rounds/1",
            "/games/hoard-hurt-help/matches/M_001/analysis/rounds/1",
        ),
        ("/games/G_001/join", "/games/hoard-hurt-help/matches/M_001/join"),
    ],
)
async def test_legacy_match_urls_redirect_to_nested_paths(
    client,
    reset_db,
    legacy_path: str,
    expected_location: str,
):
    await seed_match(reset_db, "M_001", state=GameState.ACTIVE, name="Test Match")

    r = await client.get(legacy_path, follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == expected_location

    if legacy_path.endswith("/join"):
        post = await client.post(legacy_path, follow_redirects=False)
        assert post.status_code == 308
        assert post.headers["location"] == expected_location
