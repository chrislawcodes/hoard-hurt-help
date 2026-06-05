"""Unit tests for the shared bad-words / reserved-name filter."""

from __future__ import annotations

from app.identity import word_filter


def test_contains_blocked_catches_plain_slur() -> None:
    assert word_filter.contains_blocked("you are a bitch") is True


def test_contains_blocked_catches_leet_and_spacing_dodges() -> None:
    assert word_filter.contains_blocked("sh1t") is True
    assert word_filter.contains_blocked("f u c k") is True
    assert word_filter.contains_blocked("f-u-c-k") is True


def test_contains_blocked_passes_clean_text() -> None:
    assert word_filter.contains_blocked("coin goblin") is False
    assert word_filter.contains_blocked("ZeusMaster") is False


def test_is_reserved_matches_normalized_reserved_names() -> None:
    assert word_filter.is_reserved("admin") is True
    assert word_filter.is_reserved("Admin") is True
    assert word_filter.is_reserved("a-d-m-i-n") is True
    assert word_filter.is_reserved("coin_goblin") is False


def test_mask_replaces_blocked_word_with_four_asterisks() -> None:
    masked = word_filter.mask("you absolute bitch")
    assert masked == "you absolute ****"


def test_mask_uses_fixed_length_regardless_of_word_length() -> None:
    # Two different-length words both become exactly four asterisks.
    assert word_filter.mask("dick") == "****"
    assert word_filter.mask("asshole") == "****"


def test_mask_leaves_clean_text_untouched() -> None:
    assert word_filter.mask("let's form a mutual pact") == "let's form a mutual pact"
