#!/usr/bin/env python3
import argparse
import os
import re
import tempfile
from pathlib import Path


VALID_STATUSES = {"open", "accepted", "rejected", "deferred", "failed", "insufficient"}


def split_frontmatter(text: str) -> tuple[list[str], str]:
    if not text.startswith("---\n"):
        raise ValueError("missing frontmatter")
    parts = text.split("\n---\n", 1)
    fm = parts[0].splitlines()[1:]
    body = parts[1]
    return fm, body


def update_frontmatter(lines: list[str], status: str, note: str) -> list[str]:
    updated: list[str] = []
    seen_status = False
    seen_note = False
    for line in lines:
        if line.startswith("resolution_status:"):
            updated.append(f'resolution_status: "{status}"')
            seen_status = True
        elif line.startswith("resolution_note:"):
            safe = note.replace('"', '\\"')
            updated.append(f'resolution_note: "{safe}"')
            seen_note = True
        else:
            updated.append(line)
    if not seen_status:
        updated.append(f'resolution_status: "{status}"')
    if not seen_note:
        safe = note.replace('"', '\\"')
        updated.append(f'resolution_note: "{safe}"')
    return updated


def update_resolution_block(body: str, status: str, note: str) -> str:
    block = "\n".join(
        [
            "## Resolution",
            f"- status: {status}",
            f"- note: {note}",
        ]
    )
    pattern = re.compile(r"^## Resolution\s*\n(?:- .*\n?)*", re.MULTILINE)
    match = pattern.search(body)
    if not match:
        return body.rstrip() + "\n\n" + block + "\n"
    return body[: match.start()].rstrip() + "\n\n" + block + "\n" + body[match.end() :].lstrip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", action="append", required=True)
    parser.add_argument("--status", required=True, choices=sorted(VALID_STATUSES))
    parser.add_argument("--note", required=True)
    args = parser.parse_args()

    for raw in args.review:
        path = Path(raw).resolve()
        text = path.read_text(encoding="utf-8")
        fm_lines, body = split_frontmatter(text)
        new_fm = update_frontmatter(fm_lines, args.status, args.note)
        new_body = update_resolution_block(body, args.status, args.note)
        updated = "---\n" + "\n".join(new_fm) + "\n---\n\n" + new_body.lstrip()
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as tmp:
            tmp.write(updated)
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
