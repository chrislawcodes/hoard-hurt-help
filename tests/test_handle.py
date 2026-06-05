"""Unit tests for handle validation, normalization, and suggestions."""

from __future__ import annotations

import pytest

from app.identity import handle as handle_mod
from app.identity.handle import HandleError


def _never_taken(_key: str) -> bool:
    return False


def test_validate_accepts_and_preserves_case() -> None:
    assert handle_mod.validate("ZeusMaster") == "ZeusMaster"


def test_validate_strips_surrounding_whitespace() -> None:
    assert handle_mod.validate("  coin_goblin  ") == "coin_goblin"


def test_key_for_lowercases() -> None:
    assert handle_mod.key_for("ZeusMaster") == "zeusmaster"


def test_validate_rejects_too_short() -> None:
    with pytest.raises(HandleError, match="at least 3"):
        handle_mod.validate("ab")


def test_validate_rejects_too_long() -> None:
    with pytest.raises(HandleError, match="at most 20"):
        handle_mod.validate("a" * 21)


def test_validate_rejects_bad_characters() -> None:
    with pytest.raises(HandleError, match="letters, numbers"):
        handle_mod.validate("bad-handle!")


def test_validate_rejects_starting_with_non_letter() -> None:
    with pytest.raises(HandleError, match="Start with a letter"):
        handle_mod.validate("1coolguy")


def test_validate_rejects_reserved() -> None:
    with pytest.raises(HandleError, match="reserved"):
        handle_mod.validate("admin")


def test_validate_rejects_blocked_without_echoing_input() -> None:
    bad = "fuckface"
    with pytest.raises(HandleError) as exc:
        handle_mod.validate(bad)
    # The message must never reflect the offending text back.
    assert bad not in str(exc.value)
    assert "isn't allowed" in str(exc.value)


def test_suggest_from_given_name() -> None:
    assert handle_mod.suggest(given_name="Chris", email=None, taken=_never_taken) == "Chris"


def test_suggest_slugifies_accents_and_spaces() -> None:
    # "José Luis" → drop non-word chars, keep the stem.
    assert handle_mod.suggest(given_name="José Luis", email=None, taken=_never_taken) == "JosLuis"


def test_suggest_dedupes_with_numeric_suffix() -> None:
    taken_keys = {"chris"}
    result = handle_mod.suggest(
        given_name="Chris", email=None, taken=lambda key: key in taken_keys
    )
    assert result == "Chris2"


def test_suggest_falls_back_to_email_local_part() -> None:
    result = handle_mod.suggest(given_name=None, email="coolperson@example.com", taken=_never_taken)
    assert result == "coolperson"


def test_suggest_falls_back_to_player_handle() -> None:
    # No usable name or email → a player#### handle that still validates.
    result = handle_mod.suggest(given_name="123", email="456@x.com", taken=_never_taken)
    assert result.startswith("player")
    assert handle_mod.validate(result) == result
