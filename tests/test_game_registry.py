"""Tests for the game-module registry + the PD module's registration."""

import pytest

from app.games import get, known_types
from app.games.base import GameError


def test_pd_is_registered() -> None:
    assert "hoard-hurt-help" in known_types()
    module = get("hoard-hurt-help")
    assert module.game_type == "hoard-hurt-help"
    cfg = module.config_defaults()
    assert cfg.total_rounds == 7
    assert cfg.turns_per_round == 7
    assert cfg.simultaneous is True


def test_unknown_type_raises() -> None:
    with pytest.raises(GameError):
        get("does-not-exist")


def test_pd_rules_and_move_effect() -> None:
    module = get("hoard-hurt-help")
    assert "Hoard-Hurt-Help" in module.rules_text()
    assert module.move_effect("HOARD") == (2, None)
    assert module.move_effect("HELP") == (0, 4)
    assert module.move_effect("HURT") == (0, -4)


def test_validate_move_rules() -> None:
    module = get("hoard-hurt-help")
    agents = ["A", "B", "C"]
    # Valid moves don't raise.
    module.validate_move({"action": "HOARD"}, your_agent_id="A", all_agent_ids=agents)
    module.validate_move(
        {"action": "HELP", "target_id": "B"}, your_agent_id="A", all_agent_ids=agents
    )
    # HOARD with a target, missing target, self-target, unknown target all raise.
    for bad in (
        {"action": "HOARD", "target_id": "B"},
        {"action": "HELP"},
        {"action": "HELP", "target_id": "A"},
        {"action": "HURT", "target_id": "Z"},
        {"action": "NONSENSE"},
    ):
        with pytest.raises(GameError):
            module.validate_move(bad, your_agent_id="A", all_agent_ids=agents)
