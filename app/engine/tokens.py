"""Connection key + turn token generation.

Connection keys are high-entropy random tokens (192 bits), so a single sha256
is the correct lookup primitive — argon2's slow KDF only protects *guessable*
secrets and buys nothing here. We store sha256(key) (unique, indexed) for O(1)
auth and never store the plaintext.

The `bot_key_lookup` / `bot_key_hint` helpers keep their legacy names (they
predate the connection/agent split and are referenced widely); they operate on
the connection key, not a bot. `connection_key_log_hint` is the one redaction
every auth path routes a rejected key through before logging it.
"""

import hashlib
import secrets


def generate_connection_key() -> str:
    """Issue a stable per-connection credential. Format: sk_conn_<48 hex>."""
    return "sk_conn_" + secrets.token_hex(24)


def bot_key_lookup(key: str) -> str:
    """Indexed lookup handle for a bot key: sha256 hex. Store this; never the key."""
    return hashlib.sha256(key.encode()).hexdigest()


def bot_key_hint(key: str) -> str:
    """Last 4 chars of a key, for non-secret display in the UI."""
    return key[-4:]


def connection_key_log_hint(key: str) -> str:
    """Log-safe stand-in for a key someone presented: its last 4 chars, nothing else.

    Every auth path that rejects a key logs it through here, so there is one place
    that decides how much of a presented credential a log line may carry. Those 4
    characters are the same ones stored as ``Connection.key_hint`` and shown to
    the owner on their connections page, so a failed-auth line can be matched to a
    key a person can actually see — which is the only reason the line is worth
    writing.

    Never log a leading slice instead. ``sk_conn_`` is a fixed 8 characters, so
    ``key[:11]`` publishes three characters of the live secret on every rejected
    attempt while telling the reader nothing the fixed prefix didn't already.
    """
    return bot_key_hint(key)


def generate_turn_token() -> str:
    """Opaque turn token; tk_<24 hex>."""
    return "tk_" + secrets.token_hex(12)


def generate_match_id(n: int) -> str:
    """Match IDs are M_0001, M_0002, ... assigned by the server.

    Renamed from generate_game_id (feature 009): a single play is a "match";
    "game" now means the title/module in app/games/. Migration 0018 rewrote the
    historical G_ prefix to M_.
    """
    return f"M_{n:04d}"
