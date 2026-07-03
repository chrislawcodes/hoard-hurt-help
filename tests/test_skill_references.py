"""Skill files must cite real repo paths — catch skill drift in the PR that causes it.

The `.claude/skills/*/SKILL.md` files ground agent sessions in specific repo
files. When a cited file moves or is renamed, the skill silently rots — e.g.
the `robot_circle.html` split (#571) invalidated the game-art skill's core
grounding and nothing noticed for weeks. This test walks every inline-code
span in every skill and asserts that anything that looks like a repo path
still exists, so the PR that moves a file is forced to update the skills that
cite it.

Scope: inline backtick spans only. Fenced code blocks are stripped first —
they legitimately mention generated artifacts (e.g. `data/baseline.sqlite`)
and command output that need not exist in the repo.
"""

from __future__ import annotations

import re
from glob import glob
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"

# Only tokens whose first path segment is one of these repo roots are treated
# as path claims. Everything else (`origin/main`, `factory/<slug>`, URLs,
# route paths like `/me/agents/new`) is ignored.
PATH_ROOTS = {
    ".claude",
    ".github",
    "app",
    "data",
    "docs",
    "mcp_server",
    "migrations",
    "scripts",
    "specs",
    "tests",
}

FENCED_BLOCK = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE = re.compile(r"`([^`\n]+)`")


def _candidate_paths(span: str) -> list[str]:
    """Extract repo-path claims from one inline-code span.

    A span may be a bare path or a whole command (`ls app/routes/web_*.py`),
    so tokenize on whitespace and filter each token independently.
    """
    candidates = []
    for raw in span.split():
        token = raw.split("::")[0].rstrip(".,;:)")
        if "/" not in token:
            continue
        # Placeholders and shell syntax are not path claims.
        if any(ch in token for ch in "<>{}$'\"\\|…"):
            continue
        root = token.split("/", 1)[0]
        if root not in PATH_ROOTS:
            continue
        candidates.append(token)
    return candidates


def test_skill_cited_paths_exist() -> None:
    skill_files = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    assert skill_files, "no skills found — did .claude/skills/ move?"

    missing: list[str] = []
    for skill in skill_files:
        text = FENCED_BLOCK.sub("", skill.read_text(encoding="utf-8"))
        for span in INLINE_CODE.findall(text):
            for token in _candidate_paths(span):
                if "*" in token:
                    if not glob(str(REPO_ROOT / token)):
                        missing.append(
                            f"{skill.parent.name}: `{token}` (glob matches nothing)"
                        )
                elif not (REPO_ROOT / token).exists():
                    missing.append(f"{skill.parent.name}: `{token}`")

    assert not missing, (
        "Skill(s) cite repo paths that don't exist. A cited file was moved, "
        "renamed, or never existed — update the SKILL.md in this same PR:\n  "
        + "\n  ".join(missing)
    )
