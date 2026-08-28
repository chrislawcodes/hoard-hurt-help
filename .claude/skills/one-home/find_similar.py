#!/usr/bin/env python3
"""Find functions that DO a similar thing, however differently they are written.

Matching on code structure is the obvious approach and it does not work here.
Backtested against the tree just before the C-series dedup (#559), a structural
matcher found 0 of the 8 clusters that survey found by hand — because the real
pairs were never copy-paste. `_has_moved` existed twice with the same join,
filter and `limit(1)` but a different parameter name and a different shape. A
structural hash sails straight past that.

So this ignores shape and asks what a function TOUCHES: which models and
columns, which helpers it calls, which named constants and literals it tests
against. Two functions that reach for the same handful of rare things are
answering the same question, whatever their control flow looks like.

Tokens are weighted by how rare they are across the repo (a function using
`db`, `select` and `await` tells you nothing; one using `PREGAME_STATES` and
`Match.state` tells you a lot), and pairs are scored by cosine similarity over
those weights. Read `.claude/skills/one-home/SKILL.md` for how to judge output.
"""

from __future__ import annotations

import argparse
import ast
import math
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

# Below this many tokens a function is too thin to say anything about. Kept low
# on purpose: the most-duplicated helpers in this repo are tiny (`_now`, an
# `is_bot` predicate, a liveness window), and a floor of 6 excluded all of them.
MIN_TOKENS = 4
# A token in more than this share of functions is plumbing, not meaning.
MAX_DOCUMENT_FREQUENCY = 0.10
# Pairs must share at least this many rare tokens before they are worth scoring.
MIN_SHARED_RARE = 2
DEFAULT_THRESHOLD = 0.50
# How much of the rank comes from "asks the same question" vs "does the same
# work". Tuned against the C-series backtest — see SKILL.md.
NAME_WEIGHT = 0.6
# A name claimed by more functions than this is a generic label, not a question.
MAX_NAME_HOMES = 4
# The smaller side of a pair needs this much rare vocabulary to be worth a look.
MIN_RARE_SMALL_SIDE = 3
# Long strings are prose (docstrings, prompts, error text), not identifiers.
MAX_LITERAL_LEN = 40

FuncDef = ast.FunctionDef | ast.AsyncFunctionDef

# Words that describe HOW a function is written, not WHAT it answers. Two
# functions both called `get_*` share nothing meaningful.
GENERIC_NAME_WORDS = frozenset(
    {
        "get", "load", "fetch", "build", "make", "read", "compute", "resolve",
        "to", "for", "of", "the", "a", "from", "with", "by", "and", "or",
    }
)


def name_key(name: str) -> frozenset[str]:
    """The name's words with plurals INTACT.

    `load_open_turn` and `load_open_turns` answer different questions — one row
    against many — so they must not collapse together. Stemming is right for
    ranking similarity and wrong for deciding two names are the same.
    """
    return frozenset(w for w in name.strip("_").lower().split("_") if w) - GENERIC_NAME_WORDS


def name_words(name: str) -> frozenset[str]:
    """The question a function's name asks, as a bag of words.

    `_active_player_count` and `count_players` ask the same thing. Leading
    underscores, word order and a trailing plural are all noise.
    """
    words = [w for w in name.strip("_").lower().split("_") if w]
    stemmed = [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words]
    meaningful = frozenset(stemmed) - GENERIC_NAME_WORDS
    # A name made entirely of generic words still has to compare as something.
    return meaningful or frozenset(stemmed)


def name_similarity(a: str, b: str) -> float:
    wa, wb = name_words(a), name_words(b)
    union = wa | wb
    return len(wa & wb) / len(union) if union else 0.0


def same_protocol_slot(a: Function, b: Function) -> bool:
    """Two game modules implementing the same interface method.

    `resolve_turn`, `tagline`, `build_replay_view` and friends appear once per
    game under app/games/<game>/. That is an interface with several
    implementations, not duplication, and it dominates the results otherwise.
    """
    if a.name != b.name:
        return False
    parts_a, parts_b = a.path.parts, b.path.parts
    if "games" not in parts_a or "games" not in parts_b:
        return False
    game_a = parts_a[parts_a.index("games") + 1 :][:1]
    game_b = parts_b[parts_b.index("games") + 1 :][:1]
    return bool(game_a) and bool(game_b) and game_a != game_b


def _dotted(node: ast.AST) -> str | None:
    """Render `Turn.match_id` / `Match.state` as a single token."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    if parts:
        # A method call on an expression: keep the method name alone.
        return f".{parts[0]}"
    return None


def tokens_of(fn: FuncDef) -> set[str]:
    """What this function reaches for, ignoring how it is arranged."""
    found: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute):
            dotted = _dotted(node)
            if dotted:
                found.add(dotted)
        elif isinstance(node, ast.Name):
            # Bare references to module-level names: models, constants, helpers.
            # Locals are noise, but they are cheap and the IDF weighting buries
            # them — a name used in one function only is rare but unshared.
            found.add(node.id)
        elif isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, bool) or value is None:
                found.add(f"const:{value}")
            elif isinstance(value, (int, float)):
                found.add(f"num:{value}")
            elif isinstance(value, str) and 0 < len(value) <= MAX_LITERAL_LEN:
                found.add(f"str:{value}")
        elif isinstance(node, ast.keyword) and node.arg:
            found.add(f"kw:{node.arg}")
        elif isinstance(node, ast.Compare):
            for op in node.ops:
                found.add(f"op:{type(op).__name__}")
    return found


class Scored(NamedTuple):
    """One candidate pair, with the two signals kept separate on purpose."""

    score: float
    name: float
    does: float
    a: Function
    b: Function


class Function:
    def __init__(self, path: Path, fn: FuncDef, tokens: set[str]) -> None:
        self.path = path
        self.name = fn.name
        self.lineno = fn.lineno
        self.tokens = tokens
        self.words = name_words(fn.name)
        self.key = name_key(fn.name)
        self.weights: dict[str, float] = {}
        self.mass: float = 0.0

    @property
    def location(self) -> str:
        return f"{self.path}:{self.lineno}"


# Literal-ish tokens. When two near-identical functions differ only in these,
# that difference IS the finding: `ai_only=True` against `ai_only=False`.
_LITERAL_PREFIXES = ("const:", "num:", "str:", "kw:")


def differing_literals(a: Function, b: Function) -> list[str]:
    """Literals one side has and the other does not, for near-identical pairs."""
    only_a = {t for t in a.tokens if t.startswith(_LITERAL_PREFIXES)} - b.tokens
    only_b = {t for t in b.tokens if t.startswith(_LITERAL_PREFIXES)} - a.tokens
    return sorted(only_a | only_b)


def load(roots: list[Path]) -> list[Function]:
    functions: list[Function] = []
    unreadable: list[str] = []
    for root in roots:
        if not root.exists():
            raise SystemExit(f"path does not exist: {root}")
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in files:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError) as exc:
                unreadable.append(f"{path}: {exc}")
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                # Dunders are protocol slots on unrelated classes. A hundred
                # `__init__` pairs are not a hundred duplications.
                if node.name.startswith("__") and node.name.endswith("__"):
                    continue
                toks = tokens_of(node)
                if len(toks) >= MIN_TOKENS:
                    functions.append(Function(path, node, toks))
    if unreadable:
        # Loud, not swallowed: an unparsed file is a hole in the sweep.
        print(f"WARNING: {len(unreadable)} file(s) could not be parsed:", file=sys.stderr)
        for line in unreadable:
            print(f"  {line}", file=sys.stderr)
    return functions


def weight(functions: list[Function]) -> dict[str, float]:
    """Rare tokens carry the meaning; ubiquitous plumbing carries none."""
    total = len(functions)
    seen: dict[str, int] = defaultdict(int)
    for fn in functions:
        for tok in fn.tokens:
            seen[tok] += 1
    idf: dict[str, float] = {}
    for tok, count in seen.items():
        if count / total > MAX_DOCUMENT_FREQUENCY or count < 2:
            continue
        idf[tok] = math.log(total / count)
    for fn in functions:
        # Raw IDF, not normalized: containment divides by the function's own
        # total, which is what makes "all of me is inside you" score 1.0.
        fn.weights = {t: idf[t] for t in fn.tokens if t in idf}
        fn.mass = sum(fn.weights.values())
    return idf


def score_pairs(
    functions: list[Function],
    threshold: float,
    focus: set[Path] | None,
    asymmetric: bool = False,
    include_protocol: bool = False,
) -> tuple[list[Scored], int]:
    """Pair up functions that ask the same question or do the same work.

    Two signals, deliberately independent:

    * **name** — do these ask the same question? `_is_bot` and `_is_bot` do,
      even though one takes a kind string and the other a DB row, so their
      vocabularies barely touch. Vocabulary alone never finds that pair.
    * **does** — do these touch the same models, columns and helpers? Catches
      a rule copied under two unrelated names.

    Either one alone makes a candidate; the rank combines them. This is the
    repo's own "the name is the index" rule, applied as a score.
    """
    candidates: set[tuple[int, int]] = set()

    # Candidates by shared rare vocabulary.
    vocab_index: dict[str, list[int]] = defaultdict(list)
    for i, fn in enumerate(functions):
        for tok in fn.weights:
            vocab_index[tok].append(i)
    shared: dict[tuple[int, int], int] = defaultdict(int)
    for holders in vocab_index.values():
        if len(holders) > 200:
            continue
        for pos, a in enumerate(holders):
            for b in holders[pos + 1 :]:
                shared[(a, b)] += 1
    candidates.update(pair for pair, n in shared.items() if n >= MIN_SHARED_RARE)

    # Candidates by shared name words — the signal vocabulary cannot supply.
    word_index: dict[str, list[int]] = defaultdict(list)
    for i, fn in enumerate(functions):
        for word in fn.words:
            word_index[word].append(i)
    for holders in word_index.values():
        if len(holders) > 60:
            continue
        for pos, a in enumerate(holders):
            for b in holders[pos + 1 :]:
                candidates.add((a, b))

    results: list[Scored] = []
    skipped_protocol = 0
    for a, b in candidates:
        fa, fb = functions[a], functions[b]
        if focus is not None and fa.path not in focus and fb.path not in focus:
            continue
        if not include_protocol and same_protocol_slot(fa, fb):
            skipped_protocol += 1
            continue
        name_score = name_similarity(fa.name, fb.name)
        does_score = 0.0
        if fa.mass and fb.mass and min(len(fa.weights), len(fb.weights)) >= MIN_RARE_SMALL_SIDE:
            overlap = sum(
                min(fa.weights[t], fb.weights[t]) for t in fa.weights.keys() & fb.weights.keys()
            )
            forward, backward = overlap / fa.mass, overlap / fb.mass
            does_score = max(forward, backward) if asymmetric else min(forward, backward)
        # Asking the same question is the stronger evidence that two functions
        # SHOULD be one. Doing similar work is often just shared domain
        # vocabulary, so it is weighted lower and cannot carry a pair alone.
        combined = NAME_WEIGHT * name_score + (1.0 - NAME_WEIGHT) * does_score
        if combined >= threshold:
            results.append(Scored(combined, name_score, does_score, fa, fb))
    results.sort(key=lambda r: -r.score)
    return results, skipped_protocol


def changed_files(base: str) -> set[Path]:
    merge_base = subprocess.run(
        ["git", "merge-base", "HEAD", base], capture_output=True, text=True, check=False
    )
    ref = merge_base.stdout.strip() if merge_base.returncode == 0 else base
    diff = subprocess.run(
        ["git", "diff", "--name-only", ref], capture_output=True, text=True, check=False
    )
    if diff.returncode != 0:
        raise SystemExit(f"git diff against {ref!r} failed: {diff.stderr.strip()}")
    # Untracked files are the whole point of a guard, and `git diff` omits them.
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=False,
    )
    if untracked.returncode != 0:
        raise SystemExit(f"git ls-files failed: {untracked.stderr.strip()}")
    lines = diff.stdout.splitlines() + untracked.stdout.splitlines()
    return {Path(line) for line in lines if line.endswith(".py")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", default=["app", "mcp_server"])
    parser.add_argument(
        "--changed", action="store_true", help="only pairs touching a changed file"
    )
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--top", type=int, default=40, help="max pairs to print")
    parser.add_argument(
        "--why", action="store_true", help="show the shared tokens behind each score"
    )
    parser.add_argument(
        "--include-protocol",
        action="store_true",
        help="keep per-game interface implementations (filtered out by default)",
    )
    parser.add_argument(
        "--contained",
        action="store_true",
        help="hunt small helpers reimplemented inside bigger functions (noisier)",
    )
    args = parser.parse_args(argv)

    functions = load([Path(p) for p in args.paths])
    if not functions:
        raise SystemExit("no functions found")
    weight(functions)
    focus = changed_files(args.base) if args.changed else None
    pairs, skipped = score_pairs(
        functions,
        args.threshold,
        focus,
        asymmetric=args.contained,
        include_protocol=args.include_protocol,
    )

    scope = f" (changed vs {args.base})" if args.changed else ""
    print(f"one-home: {len(functions)} functions{scope}, threshold {args.threshold}")
    mode = "one-way containment" if args.contained else "mutual overlap"
    # Three bands, not one ranked list. A same-name pair is near-certain to be
    # one question with two homes; a shared-vocabulary pair is often just two
    # functions about the same model. Ranking them together buries the first
    # band under the third — measured: the C-series true pairs sat at ranks
    # 224, 147 and 46 in a single list.
    # A name used by many functions is a generic label (`_build`, `_view`), not
    # a question with two homes. Count how many functions claim each name.
    homes: dict[frozenset[str], int] = defaultdict(int)
    for fn in functions:
        homes[fn.key] += 1

    def is_same_name(hit: Scored) -> bool:
        return (
            hit.a.key == hit.b.key
            and bool(hit.a.key)
            and homes[hit.a.key] <= MAX_NAME_HOMES
        )

    same_name = [h for h in pairs if is_same_name(h)]
    rest = [h for h in pairs if not is_same_name(h)]
    same_question = [h for h in rest if h.name >= 0.5]
    same_work = [h for h in rest if h.name < 0.5]

    print(f"\n({len(pairs)} candidate pairs, {mode}", end="")
    if skipped:
        print(f"; {skipped} per-game interface pairs filtered", end="")
    print(")")

    def band(title: str, blurb: str, hits: list[Scored], limit: int) -> None:
        print(f"\n=== {title} ({len(hits)}) ===")
        print(blurb)
        if not hits:
            print("  none\n")
            return
        print()
        for hit in hits[:limit]:
            fa, fb = hit.a, hit.b
            print(f"  {hit.score:.2f}  (name {hit.name:.2f} / does {hit.does:.2f})  {fa.name} / {fb.name}")
            print(f"        {fa.location}")
            print(f"        {fb.location}")
            if hit.score >= 0.80:
                differs = differing_literals(fa, fb)
                if differs:
                    shown = ", ".join(differs[:6])
                    more = f" (+{len(differs) - 6} more)" if len(differs) > 6 else ""
                    print(f"        differs: {shown}{more}")
            if args.why:
                common = sorted(
                    set(fa.weights) & set(fb.weights),
                    key=lambda t: -(fa.weights[t] * fb.weights[t]),
                )[:8]
                print(f"        shares: {', '.join(common)}")
            print()
        if len(hits) > limit:
            print(f"  ... {len(hits) - limit} more (raise --top)\n")

    band(
        "A. SAME NAME, TWO HOMES",
        "One question, two answers. Read all of these — highest hit rate by far.",
        same_name,
        args.top,
    )
    band(
        "B. SAME QUESTION, DIFFERENT NAMES",
        "Names ask the same thing in different words (count_players / _active_player_count).",
        same_question,
        args.top,
    )
    band(
        "C. SIMILAR WORK, UNRELATED NAMES",
        "Shared vocabulary only. Noisiest band — often just two functions about one model.",
        same_work,
        args.top // 2,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
