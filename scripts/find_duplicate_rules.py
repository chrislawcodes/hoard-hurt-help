#!/usr/bin/env python3
"""Find rules that live in more than one place.

WHY THIS EXISTS. This repo's expensive bugs share one shape: a single rule
implemented twice, slightly differently, by two sessions that each had a
reasonable definition and no way to see the other's. A paused agent could take a
seat it would never play; a rotated key died on one door and lived on the other;
"a match that hasn't started" was spelled out twelve times.

None of that is copy-pasted text, so the usual tools are blind to it:

- Duplicate-code detectors compare tokens. `or_(key_lookup == h, prev_key_lookup
  == h)` and `key_lookup == h` share almost none, yet they are the same rule
  disagreeing.
- Change-coupling (files that co-change in git) is blind by construction: the two
  key-auth doors co-changed exactly ONCE in their history. Never moving together
  IS the bug.

The three checks below each caught a real, shipped bug on 2026-08-17, which is
the only reason they are here rather than in a blog post.

THESE ARE CANDIDATES, NOT VERDICTS. Every hit needs a human to ask one question:

    does this caller re-derive the rule, or delegate it to one shared function?

Delegation looks identical from the outside and is the goal, not the bug. When
this flagged `deps.py` for not filtering `Connection.deleted_at`, that was
correct code — it calls the shared `assert_connection_usable()` instead. Expect
false positives and read them; that is the job. Do NOT wire this into CI as a
gate, or the noise will train everyone to ignore it.
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import re
import sys

# Fields that express the "...unless" half of a rule. A caller that queries the
# model but never mentions the guard either forgot it or delegates it.
GUARD_FIELDS = {
    "deleted_at",
    "archived_at",
    "left_at",
    "prev_key_lookup",
    "revoked_at",
    "completed_at",
    "paused_at",
    "superseded_at",
}

SOURCE_ROOTS = ("app", "mcp_server")


def _source_files(root: pathlib.Path) -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for top in SOURCE_ROOTS:
        out.extend(
            p
            for p in (root / top).rglob("*.py")
            if "__pycache__" not in str(p)
        )
    return sorted(out)


def _model_fields(root: pathlib.Path) -> dict[str, set[str]]:
    """Every mapped column, per model class."""
    fields: dict[str, set[str]] = collections.defaultdict(set)
    for path in (root / "app" / "models").glob("*.py"):
        model: str | None = None
        for line in path.read_text().splitlines():
            klass = re.match(r"class (\w+)\(", line)
            if klass:
                model = klass.group(1)
            column = re.match(r"\s+(\w+):\s*Mapped\[", line)
            if column and model:
                fields[model].add(column.group(1))
    return fields


def check_guard_gaps(root: pathlib.Path) -> list[str]:
    """Guard fields that only SOME callers of a model apply.

    Caught the original: `Connection.key_lookup` was read by both auth doors but
    `prev_key_lookup` by only one, so a rotated key was dead on /mcp and alive on
    the HTTP API.
    """
    findings: list[str] = []
    fields = _model_fields(root)
    text = {p: p.read_text() for p in _source_files(root) if "app/models/" not in str(p)}
    for model, columns in sorted(fields.items()):
        for guard in sorted(GUARD_FIELDS & columns):
            users = {p for p, t in text.items() if re.search(rf"\b{model}\.\w+", t)}
            guarders = {p for p, t in text.items() if re.search(rf"\b{model}\.{guard}\b", t)}
            missing = users - guarders
            # A guard almost nobody applies is probably niche, not forgotten.
            if not guarders or not missing or len(guarders) / len(users) < 0.34:
                continue
            findings.append(f"{model}.{guard}: applied by {len(guarders)} of {len(users)} callers")
            for p in sorted(missing)[:4]:
                findings.append(f"      no guard: {p.relative_to(root)}")
            if len(missing) > 4:
                findings.append(f"      ...and {len(missing) - 4} more")
    return findings


def check_repeated_predicates(root: pathlib.Path) -> list[str]:
    """The same set of filtered fields written out in 2+ different files.

    Caught the 12 hand-written GameState literals and the user-scoped agent
    queries that disagreed about whether a PAUSED agent counts.
    """
    where_block = re.compile(r"\.where\((.*?)\)\s*\n", re.DOTALL)
    seen: dict[tuple[tuple[str, str], ...], set[str]] = collections.defaultdict(set)
    sites: dict[tuple[tuple[str, str], ...], list[str]] = collections.defaultdict(list)
    for path in _source_files(root):
        src = path.read_text()
        for match in where_block.finditer(src):
            fields = tuple(sorted(set(re.findall(r"\b([A-Z]\w+)\.(\w+)\b", match.group(1)))))
            # Fewer than 3 fields is usually a plain join, not a rule.
            if len(fields) < 3:
                continue
            # More than two models is a big report query, not one rule.
            if len({model for model, _ in fields}) > 2:
                continue
            line = src[: match.start()].count("\n") + 1
            seen[fields].add(str(path))
            sites[fields].append(f"{path.relative_to(root)}:{line}")
    findings: list[str] = []
    for fields, files in sorted(seen.items(), key=lambda kv: -len(kv[1])):
        if len(files) < 2:
            continue
        pretty = ", ".join(f"{a}.{b}" for a, b in fields)
        findings.append(f"[{pretty}] in {len(files)} files")
        for site in sorted(set(sites[fields]))[:5]:
            findings.append(f"      {site}")
    return findings


def check_duplicate_function_names(root: pathlib.Path) -> list[str]:
    """The same function name defined in three or more files.

    Caught `_load_user_agents`, defined three times with three behaviours — one
    of which carried a comment explaining how it differed from the other two.
    """
    defs: dict[str, list[tuple[pathlib.Path, int]]] = collections.defaultdict(list)
    imports: dict[pathlib.Path, str] = {}
    for path in _source_files(root):
        src = path.read_text()
        imports[path] = src
        for i, line in enumerate(src.splitlines(), start=1):
            match = re.match(r"(?:async )?def (\w+)\(", line)
            if match:
                defs[match.group(1)].append((path, i))


    findings: list[str] = []
    for name, entries in sorted(defs.items()):
        places = [f"{p.relative_to(root)}:{i}" for p, i in entries]
        # Threshold of THREE, not two. Two files sharing a name is usually fine and
        # often deliberate — the MCP tools re-expose the engine's play functions under
        # the same names, and each game has its own `_now`. Trying to detect those
        # wrapper chains by chasing imports was fragile (re-exports defeat it) and
        # left a 90% false-positive rate. Three independent definitions is the point
        # where it stops being a pattern and starts being a smell: on 2026-08-17 this
        # threshold left exactly one hit, `_load_user_agents`, which was real.
        if len(places) < 3 or name.startswith("__"):
            continue
        findings.append(f"{name}() defined in {len(places)} files")
        for place in places[:5]:
            findings.append(f"      {place}")
    return findings


CHECKS = (
    ("Guard applied by some callers but not others", check_guard_gaps),
    ("Same predicate set written out in several files", check_repeated_predicates),
    ("Same function name defined in several files", check_duplicate_function_names),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[1],
        help="Repo root to scan (defaults to this script's repo).",
    )
    args = parser.parse_args()
    if not (args.root / "app").is_dir():
        print(f"error: {args.root} does not look like this repo (no app/)", file=sys.stderr)
        return 2

    total = 0
    for title, check in CHECKS:
        findings = check(args.root)
        headers = [f for f in findings if not f.startswith("      ")]
        total += len(headers)
        print(f"\n=== {title} — {len(headers)} candidate(s) ===")
        print("\n".join(findings) if findings else "  (none)")

    print(
        f"\n{total} candidates. These are questions, not verdicts: for each, ask whether "
        "the caller re-derives the rule or delegates it to one shared function. "
        "Delegation is the goal and looks the same from here."
    )
    # Always exit 0 — this reports, it does not gate. See the module docstring.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
