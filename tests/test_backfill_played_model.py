"""Backfilling the model a match played: it must never invent or overwrite.

`Player.played_model` was added after eleven matches had already run, so the
export — which now reads that stamp — went blank for all of them. The models
were not unknown: seven were in local export files pulled before the change, and
the four earliest were confirmed by hand. The backfill writes those down.

The dangerous parts of a data script are what it does when it is WRONG, so those
are what these test: it must not overwrite a seat that recorded its own model,
must not invent one for a seat it has no answer for, and must be safe to run
twice.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from backfill_played_model import apply_writes, load_exports, planned_writes  # noqa: E402

from app.models.agent import Agent, AgentKind  # noqa: E402
from app.models.match import GameState, Match  # noqa: E402
from app.models.player import Player  # noqa: E402
from app.models.user import User  # noqa: E402


def _export(match_id: str, seats: list[tuple[int, str | None]]) -> dict:
    """An export shaped like a real one: `players` keyed by NUMERIC agent id."""
    return {
        "game": {"id": match_id, "name": f"{match_id} test"},
        "players": [{"agent_id": str(aid), "model": model} for aid, model in seats],
        "submissions": [],
    }


async def _seed(reset_db, match_id: str, seats: list[tuple[str, str | None]]) -> dict[str, int]:
    """A match with players. Returns seat name -> agent id."""
    ids: dict[str, int] = {}
    async with reset_db() as db:
        user = User(google_sub=f"sub-{match_id}", email=f"{match_id}@test.com", name="u")
        db.add(user)
        await db.flush()
        db.add(
            Match(
                id=match_id, name=match_id, state=GameState.COMPLETED,
                scheduled_start=datetime.now(timezone.utc),
            )
        )
        for seat, already in seats:
            agent = Agent(
                user_id=user.id, name=f"{match_id}-{seat}", kind=AgentKind.HUMAN,
                game="hoard-hurt-help",
            )
            db.add(agent)
            await db.flush()
            db.add(
                Player(
                    match_id=match_id, user_id=user.id, agent_id=agent.id,
                    seat_name=seat, joined_at=datetime.now(timezone.utc),
                    played_model=already,
                )
            )
            ids[seat] = agent.id
        await db.commit()
    return ids


async def _models(reset_db, match_id: str) -> dict[str, str | None]:
    async with reset_db() as db:
        rows = (
            await db.execute(
                select(Player.seat_name, Player.played_model).where(Player.match_id == match_id)
            )
        ).all()
    return dict(rows)


async def test_it_stamps_the_models_the_export_recorded(reset_db, monkeypatch):
    ids = await _seed(reset_db, "M_BF1", [("Alpha", None), ("Bravo", None)])
    monkeypatch.setattr("app.db.SessionLocal", reset_db)

    export = _export("M_BF1", [(ids["Alpha"], "claude-opus-5"), (ids["Bravo"], "claude-sonnet-5")])
    changed = await apply_writes([planned_writes(export, None)])

    assert changed == 2
    assert await _models(reset_db, "M_BF1") == {
        "Alpha": "claude-opus-5",
        "Bravo": "claude-sonnet-5",
    }


async def test_it_never_overwrites_a_seat_that_recorded_its_own_model(reset_db, monkeypatch):
    """A seat stamped at play time is a better source than anything asserted here."""
    ids = await _seed(reset_db, "M_BF2", [("Alpha", "claude-haiku-4-5"), ("Bravo", None)])
    monkeypatch.setattr("app.db.SessionLocal", reset_db)

    export = _export("M_BF2", [(ids["Alpha"], "claude-opus-5"), (ids["Bravo"], "claude-opus-5")])
    changed = await apply_writes([planned_writes(export, None)])

    assert changed == 1, "it wrote over a seat that already knew what it played"
    models = await _models(reset_db, "M_BF2")
    assert models["Alpha"] == "claude-haiku-4-5", "the recorded value was overwritten"
    assert models["Bravo"] == "claude-opus-5"


async def test_running_it_twice_changes_nothing_the_second_time(reset_db, monkeypatch):
    """A data script that is not safe to re-run cannot be run with confidence."""
    ids = await _seed(reset_db, "M_BF3", [("Alpha", None)])
    monkeypatch.setattr("app.db.SessionLocal", reset_db)
    export = _export("M_BF3", [(ids["Alpha"], "claude-sonnet-5")])

    first = await apply_writes([planned_writes(export, None)])
    second = await apply_writes([planned_writes(export, None)])

    assert (first, second) == (1, 0)
    assert await _models(reset_db, "M_BF3") == {"Alpha": "claude-sonnet-5"}


def test_a_seat_with_no_model_is_skipped_unless_one_is_named():
    """It must not invent a model. The assumption has to be typed on purpose."""
    export = _export("M_BF4", [(1, None), (2, "claude-sonnet-5")])

    _, writes, unknown = planned_writes(export, None)
    assert unknown == 1 and len(writes) == 1, "a seat with no model was given one anyway"

    _, writes, unknown = planned_writes(export, "claude-haiku-4-5")
    assert unknown == 0
    assert sorted(writes) == [(1, "claude-haiku-4-5"), (2, "claude-sonnet-5")], (
        "--default-model must fill only the seats that had no model of their own"
    )


def test_it_reads_only_the_matches_it_was_asked_for(tmp_path):
    """Naming matches must actually narrow it — this is the blast radius control."""
    for match_id in ("M_AAA", "M_BBB"):
        (tmp_path / f"{match_id}.json").write_text(json.dumps(_export(match_id, [(1, "m")])))

    assert len(load_exports(str(tmp_path), [])) == 2
    only = load_exports(str(tmp_path), ["M_BBB"])
    assert [e["game"]["id"] for e in only] == ["M_BBB"]


def test_it_refuses_an_export_with_no_match_id(tmp_path, capsys):
    """A malformed file is skipped loudly, not silently folded into the plan."""
    (tmp_path / "broken.json").write_text(json.dumps({"players": [], "submissions": []}))
    assert load_exports(str(tmp_path), []) == []
    assert "no match id" in capsys.readouterr().err
