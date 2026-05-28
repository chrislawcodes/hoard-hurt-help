"""Agent key + turn token generation, with argon2 hashing for keys."""

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_HASHER = PasswordHasher()


def generate_agent_key() -> str:
    """Issue a fresh per-game key. Format: sk_game_<48 hex>."""
    return "sk_game_" + secrets.token_hex(24)


def hash_agent_key(key: str) -> str:
    """argon2 hash. Store this in the DB; show the plaintext to the player once."""
    return _HASHER.hash(key)


def verify_agent_key(key: str, hashed: str) -> bool:
    """Constant-time verification."""
    try:
        _HASHER.verify(hashed, key)
        return True
    except VerifyMismatchError:
        return False


def generate_turn_token() -> str:
    """Opaque turn token; tk_<24 hex>."""
    return "tk_" + secrets.token_hex(12)


def generate_game_id(n: int) -> str:
    """Game IDs are G_001, G_002, ... assigned by the server."""
    return f"G_{n:04d}"
