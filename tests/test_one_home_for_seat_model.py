"""Which model a seat plays must have exactly one answer.

Three places used to answer it and they disagreed. The server picks the model
(`resolve_seat_model`) and ships it in every turn payload; the always-on
connector reads it from there; and the match runner ignored all of that and
launched every agent on one shell variable. So a match whose agents were set to
Sonnet and Opus on the site could really have run entirely on Haiku — and the
export still said Sonnet and Opus, because it was reporting the *configured*
value rather than the resolved one. Nothing recorded the difference, which made
"does the model change who wins?" unanswerable from the data we already had.

Now one function answers, and everything that needs to know calls it. These
tests are the guard: they fail if a second answer grows back.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.engine.model_provider_match import resolve_seat_model

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER = REPO_ROOT / "scripts" / "match_runner"


def test_the_match_runner_takes_the_model_from_the_server():
    """The runner must not decide a seat's model for itself.

    It cannot read the turn payload — it picks a model to launch the CLI with
    before the agent exists — so it reads the same resolved answer out of the
    export instead. What it must never do is go back to launching the whole
    field on one value while the site says otherwise.
    """
    run_all = (RUNNER / "run_all.sh").read_text()

    assert "seat_models.py" in run_all, "the runner is no longer asking the server"
    assert "SEAT_MODEL[$id]" in run_all, "the runner is not launching seats per-model"
    assert "WARNING: could not read seat models" in run_all, (
        "the fallback went quiet — a silent fallback to one model is the exact "
        "failure this is here to prevent"
    )


def test_seat_models_reads_an_export_and_refuses_a_bad_one():
    """The extractor prints what the server said, and fails loudly otherwise."""
    export = (
        '{"players": ['
        '{"agent_id": "11", "model": "claude-opus-5"},'
        '{"agent_id": "12", "model": "claude-sonnet-5"},'
        '{"agent_id": "13", "model": null}]}'
    )
    ok = subprocess.run(
        [sys.executable, str(RUNNER / "seat_models.py")],
        input=export, capture_output=True, text=True, timeout=60,
    )
    assert ok.returncode == 0, ok.stderr
    assert ok.stdout.splitlines() == ["11\tclaude-opus-5", "12\tclaude-sonnet-5", "13\t"]

    # An export with no players is a failure, not an empty roster: silently
    # returning nothing would send every seat to the fallback model.
    empty = subprocess.run(
        [sys.executable, str(RUNNER / "seat_models.py")],
        input='{"players": []}', capture_output=True, text=True, timeout=60,
    )
    assert empty.returncode == 1
    assert "no players" in empty.stderr


def test_a_seat_with_no_preference_still_resolves_to_its_providers_default():
    """The case that made the two answers differ, pinned directly."""
    resolved = resolve_seat_model("claude", None)
    assert resolved is not None, (
        "a seat with no configured model must still resolve to a real model — "
        "this is the case where reporting the raw preference showed null"
    )


async def test_editing_an_agent_does_not_change_what_a_finished_match_played(client, reset_db):
    """The whole point: a match's record must survive its agent being edited.

    Before the stamp, the export read the agent's CURRENT `preferred_model`. So
    switching an agent from Sonnet to Opus today silently rewrote every past
    match to claim it had played Opus — with nothing to notice it had happened.
    Two matches on different models is exactly what these runs compare, so the
    record has to be frozen.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.models.agent import Agent, AgentKind
    from app.models.match import GameState, Match
    from app.models.player import Player
    from app.models.user import User, UserRole
    from tests.test_admin import _cookies

    async with reset_db() as db:
        admin = User(
            google_sub="sub-admin", email="admin@test.com", name="admin",
            role=UserRole.ADMIN,
        )
        db.add(admin)
        await db.flush()
        db.add(
            Match(
                id="G_MODEL",
                name="model export",
                state=GameState.COMPLETED,
                scheduled_start=datetime.now(timezone.utc) - timedelta(hours=1),
            )
        )
        agent = Agent(
            user_id=admin.id, name="a-1", kind=AgentKind.HUMAN,
            game="hoard-hurt-help", preferred_model="claude-sonnet-5",
        )
        db.add(agent)
        await db.flush()
        db.add(
            Player(
                match_id="G_MODEL", user_id=admin.id, agent_id=agent.id,
                seat_name="Seat", chosen_provider="claude",
                joined_at=datetime.now(timezone.utc),
                # What it really played, stamped when the seat was claimed.
                played_provider="claude", played_model="claude-sonnet-5",
            )
        )
        await db.commit()
        agent_id = agent.id

    first = await client.get(
        "/api/admin/matches/G_MODEL/export.json", cookies=_cookies(admin.id)
    )
    assert first.status_code == 200, first.text
    assert first.json()["players"][0]["model"] == "claude-sonnet-5"

    # Now change the agent, the way anyone would between two experiments.
    async with reset_db() as db:
        edited = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one()
        edited.preferred_model = "claude-opus-5"
        await db.commit()

    again = await client.get(
        "/api/admin/matches/G_MODEL/export.json", cookies=_cookies(admin.id)
    )
    assert again.json()["players"][0]["model"] == "claude-sonnet-5", (
        "editing the agent rewrote what a finished match says it played"
    )


async def test_a_seat_that_was_never_served_reports_no_model(client, reset_db):
    """An unrecorded model must read as null, not as a guess.

    Filling it in from the agent's current setting would put the same lie in a
    new place: an old export would look authoritative while being invented.
    """
    from datetime import datetime, timedelta, timezone

    from app.models.agent import Agent, AgentKind
    from app.models.match import GameState, Match
    from app.models.player import Player
    from app.models.user import User, UserRole
    from tests.test_admin import _cookies

    async with reset_db() as db:
        admin = User(
            google_sub="sub-admin", email="admin@test.com", name="admin",
            role=UserRole.ADMIN,
        )
        db.add(admin)
        await db.flush()
        db.add(
            Match(
                id="G_UNSERVED", name="never played", state=GameState.COMPLETED,
                scheduled_start=datetime.now(timezone.utc) - timedelta(hours=1),
            )
        )
        agent = Agent(
            user_id=admin.id, name="a-2", kind=AgentKind.HUMAN,
            game="hoard-hurt-help", preferred_model="claude-opus-5",
        )
        db.add(agent)
        await db.flush()
        db.add(
            Player(
                match_id="G_UNSERVED", user_id=admin.id, agent_id=agent.id,
                seat_name="Seat", chosen_provider="claude",
                joined_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    r = await client.get(
        "/api/admin/matches/G_UNSERVED/export.json", cookies=_cookies(admin.id)
    )
    assert r.json()["players"][0]["model"] is None, (
        "a seat that never played reported a model — that is a guess, not a record"
    )


def test_the_runner_can_still_read_a_model_before_the_match_has_run():
    """The case that broke the runner: a match with nothing played yet.

    Two different questions live in the export. `model` is what a seat PLAYED,
    frozen when it was first served — empty until then. `model_to_play` is what
    the server WILL send it. The runner has to pick a model to launch each agent
    with before any turn exists, so it needs the second; collapsing them into
    one field sent every agent to a single default, which is the bug the stamp
    was added to stop.
    """
    import json
    import subprocess
    import sys

    def read(export: dict) -> list[str]:
        proc = subprocess.run(
            [sys.executable, str(RUNNER / "seat_models.py")],
            input=json.dumps(export), capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.splitlines()

    # Not yet played: fall back to what the server intends to send.
    assert read({"players": [
        {"agent_id": "1", "model": None, "model_to_play": "claude-opus-5"},
    ]}) == ["1\tclaude-opus-5"]

    # Already played: the record beats the intention, even when they differ.
    assert read({"players": [
        {"agent_id": "1", "model": "claude-haiku-4-5", "model_to_play": "claude-opus-5"},
    ]}) == ["1\tclaude-haiku-4-5"]

    # Neither known: blank, so the caller's loud fallback fires rather than a guess.
    assert read({"players": [
        {"agent_id": "1", "model": None, "model_to_play": None},
    ]}) == ["1\t"]


async def test_the_export_carries_both_questions_separately(client, reset_db):
    """`model` must stay the frozen record while `model_to_play` reads live.

    If `model_to_play` ever leaked into `model`, editing an agent would once
    again rewrite what a past match claimed it played.
    """
    from datetime import datetime, timedelta, timezone

    from app.models.agent import Agent, AgentKind
    from app.models.match import GameState, Match
    from app.models.player import Player
    from app.models.user import User, UserRole
    from tests.test_admin import _cookies

    async with reset_db() as db:
        admin = User(
            google_sub="sub-admin", email="admin@test.com", name="admin",
            role=UserRole.ADMIN,
        )
        db.add(admin)
        await db.flush()
        db.add(
            Match(
                id="G_BOTH", name="both", state=GameState.REGISTERING,
                scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        agent = Agent(
            user_id=admin.id, name="a-both", kind=AgentKind.HUMAN,
            game="hoard-hurt-help", preferred_model="claude-opus-5",
        )
        db.add(agent)
        await db.flush()
        db.add(
            Player(
                match_id="G_BOTH", user_id=admin.id, agent_id=agent.id,
                seat_name="Seat", chosen_provider="claude",
                joined_at=datetime.now(timezone.utc),
                # Never served, so no stamp — the shape of a match about to run.
            )
        )
        await db.commit()

    seat = (
        await client.get("/api/admin/matches/G_BOTH/export.json", cookies=_cookies(admin.id))
    ).json()["players"][0]

    assert seat["model"] is None, "an unplayed seat must not claim to have played a model"
    assert seat["model_to_play"] == "claude-opus-5", (
        "the runner has nothing to launch this seat with"
    )
