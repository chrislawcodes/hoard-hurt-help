"""Skill files must cite real repo paths and real symbols — catch skill drift in
the PR that causes it.

The `.claude/skills/*/SKILL.md` files ground agent sessions in specific repo
files. When a cited file moves or is renamed, the skill silently rots — e.g.
the `robot_circle.html` split (#571) invalidated the game-art skill's core
grounding and nothing noticed for weeks.

Two checks, because a path can be right while the claim about it is wrong:

1. `test_skill_cited_paths_exist` — anything that looks like a repo path still
   resolves. Scope: inline backtick spans only. Fenced code blocks are stripped
   first — they legitimately mention generated artifacts (e.g. a tournament's
   output DB) and command output that need not exist in the repo.

2. `test_skill_cited_symbols_exist` — a function/class/constant named in a
   bullet that is *about* a Python file is actually defined in that file. The
   path check alone cannot see this: the game-design skill sent readers to
   `app/engine/resolver.py` for `resolve_turn` for months after it moved to
   `app/games/hoard_hurt_help/scoring.py`. The file still existed, so the path
   check passed while the skill pointed at the wrong place (#640).

**Check 2 is deliberately narrow, and the narrowness is measured, not assumed.**
It fires only on a bullet whose FIRST inline-code span is a Python file that
exists — the `- **\\`app/x.py\\`** — \\`foo\\` does the thing` shape. A looser rule
that associated every symbol with any nearby cited file was tried first and
flagged 16 of 32 claims across the skills: MCP tool names (`preview_screenshot`),
DB columns (`deadline_at`) and env vars (`DEV_LOGIN_ENABLED`) all blamed on
whatever `.py` happened to sit near them. A guard that noisy gets suppressed,
which is worse than no guard. The narrow rule flags 0 of 14 on a clean tree and
still fails the moment the `resolve_turn` regression is reintroduced.

So this does NOT prove every symbol in every skill is real. It proves the
"this bullet documents this file" claims are, which is where the rot happened.
"""

from __future__ import annotations

import ast
import re
from functools import lru_cache
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
# A list item that OPENS with a cited path, optionally bolded:
#     - **`app/games/hoard_hurt_help/rules.py`** — `HOARD_POINTS`, …
# The leading path is what makes the bullet *about* that file, which is the only
# context in which a bare symbol can be attributed to it without guessing.
BULLET_HEAD = re.compile(r"^\s*[-*]\s+\*{0,2}`([^`\n]+)`\*{0,2}\s*(.*)$", re.DOTALL)
# A bare Python name, with or without call parens: `resolve_turn`, `foo()`,
# `HOARD_POINTS`, `ProviderReadiness`. Anything with a dot, slash or space is
# not a symbol claim about one file, so it is left alone.
SYMBOL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\(\))?$")

# Floor for "did this guard actually look at anything" — see the assertion at the
# end of test_skill_cited_symbols_exist.
MIN_SYMBOL_CLAIMS = 5


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


@lru_cache(maxsize=None)
def _defined_names(py_path: Path) -> frozenset[str]:
    """Every name *defined* by one Python file: defs, classes, module constants.

    Definitions are collected with ``ast.walk`` so a method or a nested helper
    counts, but plain assignments are taken from the module body only. Both
    choices are the strictest that stay at zero false positives across the
    current skills — widening to imports, attribute names or string literals
    changed nothing on a clean tree and would only let real drift through, since
    a moved function is usually still *imported* by the file it left.

    An unparseable file yields an empty set rather than raising: that is a
    problem for the Python test suite to report, not for this guard to mask.
    """
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return frozenset()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    for stmt in tree.body:
        targets: list[ast.expr] = []
        if isinstance(stmt, ast.Assign):
            targets = list(stmt.targets)
        elif isinstance(stmt, ast.AnnAssign):
            targets = [stmt.target]
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return frozenset(names)


def _list_items(text: str) -> list[str]:
    """Split markdown into list items, each with its indented continuation lines.

    A bullet's description usually wraps across several lines, and the symbols it
    names are on those lines — so the item, not the line, is the unit. A blank
    line ends the item.
    """
    items: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if re.match(r"^\s*[-*]\s+", line):
            if current:
                items.append("\n".join(current))
            current = [line]
        elif not current:
            continue
        elif not line.strip():
            items.append("\n".join(current))
            current = []
        elif line.startswith("  "):
            current.append(line)
        else:
            items.append("\n".join(current))
            current = []
    if current:
        items.append("\n".join(current))
    return items


def _where_defined(name: str) -> list[str]:
    """Repo files that DO define *name*, so a failure names its own fix.

    Only called on a failure, so the repo-wide walk costs nothing on a green run.
    """
    hits: list[str] = []
    for root in sorted(PATH_ROOTS):
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for py_path in sorted(base.rglob("*.py")):
            if ".venv" in py_path.parts or "__pycache__" in py_path.parts:
                continue
            if name in _defined_names(py_path):
                hits.append(py_path.relative_to(REPO_ROOT).as_posix())
    return hits


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


def test_skill_cited_symbols_exist() -> None:
    """A bullet that documents a Python file must name symbols that file defines.

    See the module docstring for why this only fires on the "bullet opens with a
    path" shape — the looser rule was measured and was too noisy to keep.
    """
    skill_files = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    assert skill_files, "no skills found — did .claude/skills/ move?"

    wrong: list[str] = []
    examined = 0
    for skill in skill_files:
        text = FENCED_BLOCK.sub("", skill.read_text(encoding="utf-8"))
        for item in _list_items(text):
            head = BULLET_HEAD.match(item)
            if head is None:
                continue
            cited, description = head.group(1).strip(), head.group(2)
            if not cited.endswith(".py"):
                continue
            target = REPO_ROOT / cited
            if not target.is_file():
                continue  # the path check above owns the "file is gone" case
            defined = _defined_names(target)
            for span in INLINE_CODE.findall(description):
                match = SYMBOL.match(span.strip())
                if match is None:
                    continue
                name = match.group(1)
                examined += 1
                if name in defined:
                    continue
                elsewhere = _where_defined(name)
                found = (
                    " — it lives in " + ", ".join(elsewhere)
                    if elsewhere
                    else " — and it is not defined anywhere in the repo"
                )
                wrong.append(f"{skill.parent.name}: `{name}` is not in {cited}{found}")

    assert not wrong, (
        "Skill(s) attribute a symbol to a file that doesn't define it. The file "
        "still exists, so the path check passes — but the skill sends its reader "
        "to the wrong place. Update the SKILL.md in this same PR:\n  "
        + "\n  ".join(wrong)
    )
    # This check only sees one markdown shape, so a reformat of the skills could
    # silently reduce it to inspecting nothing and it would still pass. The floor
    # makes that failure loud instead. It sits well below the ~14 claims present
    # when this was written, so ordinary edits never trip it — only the format
    # changing under the regex does.
    assert examined >= MIN_SYMBOL_CLAIMS, (
        f"only {examined} symbol claims were checked (expected at least "
        f"{MIN_SYMBOL_CLAIMS}). The skills' bullet format likely changed, so this "
        "guard is no longer reading them — fix BULLET_HEAD/_list_items rather "
        "than lowering this floor."
    )
