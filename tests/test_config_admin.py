"""Tests for the admin role split settings (platform_admin_emails, game_admin_emails_for)."""

import pytest

from app.config import Settings


def _make_settings(**kwargs: str) -> Settings:
    """Build a fresh Settings with model_construct (bypasses env file + validator)."""
    return Settings.model_construct(**kwargs)


def _fresh(**env_overrides: str) -> Settings:
    """Build Settings() from scratch in a clean env (clears ADMIN_EMAILS etc.)."""
    # Handled via monkeypatch in test; call as Settings() after patching.
    return Settings()


class TestPlatformAdminEmailsSet:
    def test_empty_by_default(self) -> None:
        s = _make_settings()
        assert s.platform_admin_emails_set == set()

    def test_single_email(self) -> None:
        s = _make_settings(platform_admin_emails="alice@example.com")
        assert s.platform_admin_emails_set == {"alice@example.com"}

    def test_multiple_emails(self) -> None:
        s = _make_settings(platform_admin_emails="alice@example.com, BOB@EXAMPLE.COM")
        assert s.platform_admin_emails_set == {"alice@example.com", "bob@example.com"}

    def test_falls_back_to_admin_emails(self) -> None:
        s = _make_settings(admin_emails="legacy@example.com")
        assert s.platform_admin_emails_set == {"legacy@example.com"}

    def test_platform_takes_precedence_over_admin_emails(self) -> None:
        s = _make_settings(
            platform_admin_emails="new@example.com",
            admin_emails="old@example.com",
        )
        assert s.platform_admin_emails_set == {"new@example.com"}


class TestGameAdminEmailsFor:
    def test_empty_when_no_env_vars(self) -> None:
        s = _make_settings()
        assert s.game_admin_emails_for("hoard-hurt-help") == set()

    def test_slug_normalization(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ADMIN_EMAILS", raising=False)
        monkeypatch.setenv("GAME_ADMIN_EMAILS__HOARD_HURT_HELP", "gamer@example.com")
        s = Settings()
        assert s.game_admin_emails_for("hoard-hurt-help") == {"gamer@example.com"}

    def test_multiple_emails_comma_separated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ADMIN_EMAILS", raising=False)
        monkeypatch.setenv(
            "GAME_ADMIN_EMAILS__HOARD_HURT_HELP", "alice@x.com,  bob@x.com"
        )
        s = Settings()
        assert s.game_admin_emails_for("hoard-hurt-help") == {"alice@x.com", "bob@x.com"}

    def test_unknown_game_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Explicitly blank ADMIN_EMAILS so the .env file fallback doesn't fire.
        monkeypatch.setenv("ADMIN_EMAILS", "")
        monkeypatch.setenv("GAME_ADMIN_EMAILS__HOARD_HURT_HELP", "admin@x.com")
        s = Settings()
        assert s.game_admin_emails_for("unknown-game") == set()

    def test_falls_back_to_admin_emails(self) -> None:
        # model_construct bypasses env-file and validator; set admin_emails manually.
        s = _make_settings(admin_emails="legacy@example.com")
        assert s.game_admin_emails_for("hoard-hurt-help") == {"legacy@example.com"}


class TestAllGameAdminEmailsSet:
    def test_empty_when_no_game_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GAME_ADMIN_EMAILS__HOARD_HURT_HELP", raising=False)
        monkeypatch.delenv("GAME_ADMIN_EMAILS__OTHER_GAME", raising=False)
        s = Settings()
        assert s.all_game_admin_emails_set == set()

    def test_union_across_games(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GAME_ADMIN_EMAILS__HOARD_HURT_HELP", "alice@x.com")
        monkeypatch.setenv("GAME_ADMIN_EMAILS__OTHER_GAME", "bob@x.com")
        s = Settings()
        assert {"alice@x.com", "bob@x.com"}.issubset(s.all_game_admin_emails_set)
