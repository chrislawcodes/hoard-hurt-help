"""The legacy G_/M_ redirect must keep picking the same candidate it always did.

``_redirect_to_match`` (``app/routes/web_support.py``) walks
``match_id_candidates`` — the input as-is, then the ``M_`` form, then the
``G_`` form — and used to issue one query per candidate, stopping at the first
row found. Batched into a single ``IN`` query, the database no longer returns
rows in candidate order, so the winner has to be picked in Python by walking
the candidates in their original order.

This matters only when more than one candidate resolves to a real match — a
straggler ``G_`` row alongside a real, unrelated ``M_`` row with the same
numeric suffix. These tests pin that the earlier candidate still wins, for
both directions of the rewrite.
"""

from __future__ import annotations

from app.models import GameState
from tests.factories import seed_match


async def test_legacy_g_id_prefers_the_g_match_when_both_exist(client, reset_db):
    """Both G_0777 and M_0777 exist; requesting G_0777 must still redirect to G_0777.

    match_id_candidates("G_0777") == ("G_0777", "M_0777") — G_0777 is first.
    """
    await seed_match(reset_db, "G_0777", state=GameState.REGISTERING)
    await seed_match(reset_db, "M_0777", state=GameState.REGISTERING)

    r = await client.get("/games/G_0777", follow_redirects=False)

    assert r.status_code == 301
    assert r.headers["location"] == "/games/hoard-hurt-help/matches/G_0777"


async def test_legacy_m_id_prefers_the_m_match_when_both_exist(client, reset_db):
    """Both G_0777 and M_0777 exist; requesting M_0777 must still redirect to M_0777.

    match_id_candidates("M_0777") == ("M_0777", "G_0777") — M_0777 is first.
    """
    await seed_match(reset_db, "G_0777", state=GameState.REGISTERING)
    await seed_match(reset_db, "M_0777", state=GameState.REGISTERING)

    r = await client.get("/games/M_0777", follow_redirects=False)

    assert r.status_code == 301
    assert r.headers["location"] == "/games/hoard-hurt-help/matches/M_0777"


async def test_legacy_id_with_no_match_still_404s(client, reset_db):
    r = await client.get("/games/G_9999_missing", follow_redirects=False)

    assert r.status_code == 404
