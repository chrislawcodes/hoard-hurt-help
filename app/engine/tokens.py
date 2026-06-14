"""Connection key + turn token generation.

Connection keys are high-entropy random tokens (192 bits), so a single sha256
is the correct lookup primitive — argon2's slow KDF only protects *guessable*
secrets and buys nothing here. We store sha256(key) (unique, indexed) for O(1)
auth and never store the plaintext.

The `bot_key_lookup` / `bot_key_hint` helpers keep their legacy names (they
predate the connection/agent split and are referenced widely); they operate on
the connection key, not a bot.
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
