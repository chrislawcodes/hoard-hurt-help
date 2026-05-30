"""Bot key + turn token generation.

Bot keys are high-entropy random tokens (192 bits), so a single sha256 is the
correct lookup primitive — argon2's slow KDF only protects *guessable* secrets
and buys nothing here. We store sha256(key) (unique, indexed) for O(1) auth and
never store the plaintext.
"""

import hashlib
import secrets


def generate_bot_key() -> str:
    """Issue a stable per-bot credential. Format: sk_bot_<48 hex>."""
    return "sk_bot_" + secrets.token_hex(24)


def bot_key_lookup(key: str) -> str:
    """Indexed lookup handle for a bot key: sha256 hex. Store this; never the key."""
    return hashlib.sha256(key.encode()).hexdigest()


def bot_key_hint(key: str) -> str:
    """Last 4 chars of a key, for non-secret display in the UI."""
    return key[-4:]


def generate_turn_token() -> str:
    """Opaque turn token; tk_<24 hex>."""
    return "tk_" + secrets.token_hex(12)


def generate_game_id(n: int) -> str:
    """Game IDs are G_001, G_002, ... assigned by the server."""
    return f"G_{n:04d}"
