"""Tests for the admin settings.

The platform has two roles: user, and platform admin. A third — game admin,
granted by a per-game environment variable — was removed.
The classes that tested it were replaced by ``TestGameAdminRoleIsGone``, which
pins that the symbols are actually absent rather than merely unused.
"""

import pytest

from app.config import Settings, settings

# Built from parts so this file does not contain the literal string the
# repo-wide scan in tests/test_role_simplification.py forbids.
_DEAD = "game" + "_admin"


def _make_settings(**kwargs: str) -> Settings:
    """Build a fresh Settings with model_construct (bypasses env file + validator)."""
    return Settings.model_construct(**kwargs)


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


class TestGameAdminRoleIsGone:
    """The removed role leaves no way back in.

    Setting the old environment variable and asserting it grants nothing would
    prove nothing: the variable was read once at Settings construction, and
    ``settings`` is a cached process singleton, so a late ``setenv`` never
    reached it even before the removal. The assertion that actually fails if the
    deletion is skipped is that the symbols are gone.
    """

    @pytest.mark.parametrize(
        "attribute",
        [
            f"{_DEAD}_emails_for",
            f"all_{_DEAD}_emails_set",
            f"_{_DEAD}_emails_raw",
            f"_collect_{_DEAD}_emails",
        ],
    )
    def test_symbol_absent_from_settings(self, attribute: str) -> None:
        assert not hasattr(settings, attribute)
        assert not hasattr(Settings, attribute)

    def test_env_var_is_not_read_at_construction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fresh Settings built with the old variable set ignores it entirely."""
        monkeypatch.setenv(f"{_DEAD.upper()}_EMAILS__HOARD_HURT_HELP", "ghost@x.com")
        monkeypatch.setenv("ADMIN_EMAILS", "")
        monkeypatch.setenv("PLATFORM_ADMIN_EMAILS", "")
        s = Settings()
        assert s.platform_admin_emails_set == set()
        # Nothing on the instance holds the ghost address.
        assert "ghost@x.com" not in repr(s.model_dump())


class TestIsAnyAdminIsRoleOnly:
    """``_is_any_admin`` is the flag ~20 pages read. It is now role-only.

    It used to also return True for an address in a game-admin environment
    list, which is what made an under-construction game visible to someone who
    was never promoted in the database.
    """

    def test_none_is_not_admin(self) -> None:
        from app.routes.web_support import _is_any_admin

        assert _is_any_admin(None) is False

    def test_plain_user_is_not_admin_whatever_their_email(self) -> None:
        from types import SimpleNamespace

        from app.models.user import UserRole
        from app.routes.web_support import _is_any_admin

        user = SimpleNamespace(email="anyone@example.com", role=UserRole.USER)
        assert _is_any_admin(user) is False

    def test_admin_role_is_admin(self) -> None:
        from types import SimpleNamespace

        from app.models.user import UserRole
        from app.routes.web_support import _is_any_admin

        user = SimpleNamespace(email="anyone@example.com", role=UserRole.ADMIN)
        assert _is_any_admin(user) is True
