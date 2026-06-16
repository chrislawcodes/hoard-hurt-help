"""Guard against the platform architecture doc going stale.

The doc names many repo files and directories in backticks. When code moves or
gets renamed, those references rot silently. This test reads the doc and asserts
that every *clear* repo path it names actually exists on disk.

It is deliberately conservative: it only checks tokens that start with a known
repo source prefix, skips anything with a glob/placeholder character, and skips
ambiguous tokens that are neither clearly a directory nor clearly a file. The
goal is zero false failures on prose or illustrative paths.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
ARCH_DOC: Path = REPO_ROOT / "docs" / "platform" / "AGENT_LUDUM_ARCHITECTURE.md"

# Only tokens beginning with one of these are treated as real repo paths.
PATH_PREFIXES: tuple[str, ...] = (
    "app/",
    "mcp_server/",
    "tests/",
    "migrations/",
    "docs/",
    "scripts/",
    "specs/",
)

# Tokens containing any of these are globs/templates, not literal paths.
PLACEHOLDER_CHARS: tuple[str, ...] = ("*", "<", ">", "{", "}")

# A token ending in one of these is treated as a file.
FILE_EXTENSIONS: tuple[str, ...] = (
    ".py",
    ".md",
    ".css",
    ".html",
    ".toml",
    ".txt",
    ".json",
)

# Anything inside single backticks.
_BACKTICK_RE: re.Pattern[str] = re.compile(r"`([^`]+)`")


def _backtick_tokens(text: str) -> list[str]:
    """Return the raw contents of every single-backtick span in the text."""
    return [match.group(1).strip() for match in _BACKTICK_RE.finditer(text)]


def _is_candidate_path(token: str) -> bool:
    """True if the token looks like a literal repo path we should verify."""
    if not token.startswith(PATH_PREFIXES):
        return False
    if any(ch in token for ch in PLACEHOLDER_CHARS):
        return False
    return True


def _missing_paths(tokens: list[str]) -> list[str]:
    """Return the subset of candidate tokens that do not exist on disk.

    Directories (trailing ``/``) must resolve to a real directory. Tokens with a
    known file extension must resolve to a real file. Everything else is too
    ambiguous to check and is skipped.
    """
    missing: list[str] = []
    for token in tokens:
        if not _is_candidate_path(token):
            continue
        if token.endswith("/"):
            if not (REPO_ROOT / token).is_dir():
                missing.append(token)
        elif token.endswith(FILE_EXTENSIONS):
            if not (REPO_ROOT / token).is_file():
                missing.append(token)
        # Neither a trailing slash nor a known extension: too ambiguous, skip.
    return missing


def test_arch_doc_exists() -> None:
    """The architecture doc must be present where we expect it."""
    assert ARCH_DOC.is_file(), f"Architecture doc not found at {ARCH_DOC}"


def test_arch_doc_paths_exist() -> None:
    """Every clear repo path named in the arch doc must exist on disk."""
    text = ARCH_DOC.read_text(encoding="utf-8")
    tokens = _backtick_tokens(text)
    missing = _missing_paths(tokens)
    assert not missing, (
        "The architecture doc names repo paths that no longer exist. "
        "Update the doc reference (or fix this check if it is a false positive):\n"
        + "\n".join(f"  - {path}" for path in sorted(set(missing)))
    )
