from __future__ import annotations

from typing import Final

# The platform's default game type. Equals HoardHurtHelp.game_type, which is the
# canonical declaration in app/games/hoard_hurt_help/game.py. Kept here as a leaf
# constant (no app imports) so low-level modules like app/models/* can use it
# without an import cycle through the game registry.
DEFAULT_GAME_TYPE: Final[str] = "hoard-hurt-help"
