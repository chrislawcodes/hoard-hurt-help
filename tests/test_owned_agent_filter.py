"""The ownership rule has one home, and nothing re-derives it.

"Is this the user's own, still-existing agent?" was written out by hand in seven
places — twice inside `agents_queries.py` itself, ten lines apart, and again in
`web_join._seat_user_agent`. That third copy is where the paused-agent bug lived:
a caller that re-derives half a rule is equally free to forget the other half, and
that one forgot to ask whether the agent could actually play.

Two tests here. The first pins what the rule MEANS. The second is the one that
matters over time: a structural guard that fails when a new hand-rolled copy
appears, because that is how the seventh copy got written and nobody noticed.
"""

from __future__ import annotations

import pathlib
import re

from sqlalchemy import select

from app.models.agent import Agent, AgentKind, AgentStatus
from app.routes.agents_queries import owned_agent_filter
from tests.factories import make_agent, make_user

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
# Where the rule is allowed to be spelled out: its own definition.
HOME = "app/routes/agents_queries.py"


async def test_the_filter_means_what_the_pages_assume(reset_db) -> None:
    """Own + not archived + (AI, unless asked otherwise).

    Paused agents are deliberately INCLUDED — you can still rename or inspect an
    agent that cannot play. Whether it can play is a separate question with its own
    home in `agent_playability.playable_agent_filter`.
    """
    async with reset_db() as db:
        owner = await make_user(db, 0)
        other = await make_user(db, 1)
        mine, _ = await make_agent(db, owner, name="mine", status=AgentStatus.ACTIVE)
        paused, _ = await make_agent(db, owner, name="paused", status=AgentStatus.PAUSED)
        archived, _ = await make_agent(db, owner, name="archived")
        bot, _ = await make_agent(db, owner, name="bot", kind=AgentKind.BOT)
        theirs, _ = await make_agent(db, other, name="theirs")
        from datetime import datetime, timezone

        archived.archived_at = datetime.now(timezone.utc)
        await db.commit()
        owner_id, mine_id, paused_id, bot_id = owner.id, mine.id, paused.id, bot.id
        archived_id, theirs_id = archived.id, theirs.id

        got = set(
            (await db.execute(select(Agent.id).where(*owned_agent_filter(owner_id))))
            .scalars()
            .all()
        )
        assert mine_id in got
        assert paused_id in got, "a paused agent is still yours — you can rename it"
        assert archived_id not in got
        assert bot_id not in got
        assert theirs_id not in got

        with_bots = set(
            (
                await db.execute(
                    select(Agent.id).where(*owned_agent_filter(owner_id, ai_only=False))
                )
            )
            .scalars()
            .all()
        )
        assert bot_id in with_bots


def test_nothing_re_derives_the_ownership_rule() -> None:
    """No `.where()` outside its home may spell out all three conditions.

    Deliberately structural rather than a list of known-bad files: the bug is a NEW
    copy getting written, not an old one recurring. Matching the shape catches the
    copy nobody has written yet.

    The agent-name-uniqueness queries in `agents_create` / `agents_lifecycle` do not
    trip this and must not — they intentionally omit the kind filter, so a name is
    reserved across every kind of agent. That is a different rule, and it still needs
    a home of its own.
    """
    where_block = re.compile(r"\.where\((.*?)\)\s*\n", re.DOTALL)
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        rel = str(path.relative_to(APP.parent))
        if rel == HOME:
            continue
        src = path.read_text()
        for match in where_block.finditer(src):
            body = match.group(1)
            if (
                "Agent.user_id ==" in body
                and "Agent.archived_at" in body
                and "Agent.kind == AgentKind.AI" in body
            ):
                offenders.append(f"{rel}:{src[: match.start()].count(chr(10)) + 1}")
    assert not offenders, (
        "these re-derive the ownership rule instead of calling "
        f"owned_agent_filter() from {HOME}: {offenders}"
    )
