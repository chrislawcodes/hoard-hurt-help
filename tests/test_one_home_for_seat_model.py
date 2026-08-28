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


def test_the_export_reports_the_same_model_the_turn_payload_sends():
    """Both readers must go through `resolve_seat_model`, not around it.

    The export used to report `Agent.preferred_model` raw. The payload layers in
    the provider's default when an agent has no preference, so a seat with none
    showed as null in the export while really playing the default — two answers
    to one question, and the export is what every measurement is read from.
    """
    export_src = (REPO_ROOT / "app" / "read_models" / "match_export.py").read_text()
    payload_src = (REPO_ROOT / "app" / "engine" / "agent_play_next_turn.py").read_text()

    assert "resolve_seat_model(" in export_src, (
        "the export no longer calls resolve_seat_model — it is answering "
        "'which model does this seat play' on its own again"
    )
    assert "resolve_seat_model(" in payload_src
    assert "agent.preferred_model if agent is not None else None" not in export_src, (
        "the export is reporting the configured model rather than the resolved one"
    )


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


async def test_the_export_shows_the_model_a_seat_will_really_play(client, reset_db):
    """The behavioural version of the first test, on a real export.

    An agent with NO configured model still plays its provider's default. The
    export used to print null for that seat, so a run could look modelless in
    the record while every turn was really being played on the default. This
    asserts the export prints what the server would actually send.
    """
    from datetime import datetime, timedelta, timezone

    from app.engine.model_provider_match import resolve_seat_model
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
        match = Match(
            id="G_MODEL",
            name="model export",
            state=GameState.COMPLETED,
            scheduled_start=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db.add(match)
        # One seat with a model set, one with none — the case that used to differ.
        for seat, preferred in (("Chose", "claude-opus-5"), ("Unset", None)):
            agent = Agent(
                user_id=admin.id, name=f"a-{seat}", kind=AgentKind.HUMAN,
                game="hoard-hurt-help", preferred_model=preferred,
            )
            db.add(agent)
            await db.flush()
            db.add(
                Player(
                    match_id=match.id, user_id=admin.id, agent_id=agent.id,
                    seat_name=seat, chosen_provider="claude",
                    joined_at=datetime.now(timezone.utc),
                )
            )
        await db.commit()

    r = await client.get(
        "/api/admin/matches/G_MODEL/export.json", cookies=_cookies(admin.id)
    )
    assert r.status_code == 200, r.text
    models = [p["model"] for p in r.json()["players"]]

    assert "claude-opus-5" in models, "a configured model must survive to the export"
    assert None not in models, (
        "a seat with no configured model exported as null — it will really play "
        "its provider's default, so the record disagrees with what runs"
    )
    assert set(models) == {"claude-opus-5", resolve_seat_model("claude", None)}
