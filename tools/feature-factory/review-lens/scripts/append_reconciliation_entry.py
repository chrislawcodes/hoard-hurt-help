#!/usr/bin/env python3
import argparse
from pathlib import Path


SECTION_HEADER = "## Review Reconciliation"
COLUMN_PREFIX = "- review: "


def relative_review_path(plan_path: Path, review_path: Path) -> str:
    return str(review_path.resolve().relative_to(plan_path.parent.resolve()))


def render_entry(review_ref: str, status: str, note: str) -> str:
    return f"- review: {review_ref} | status: {status} | note: {note}"


def parse_sections(text: str) -> tuple[str, str, str]:
    if SECTION_HEADER not in text:
        return text.rstrip(), "", ""
    before, remainder = text.split(SECTION_HEADER, 1)
    after = remainder.lstrip("\n")
    lines = after.splitlines()
    section_lines: list[str] = []
    trailing_lines: list[str] = []
    in_section = True
    for line in lines:
        if in_section and line.startswith("## "):
            in_section = False
        if in_section:
            section_lines.append(line)
        else:
            trailing_lines.append(line)
    return before.rstrip(), "\n".join(section_lines).strip(), "\n".join(trailing_lines).rstrip()


def canonical_lines(section_body: str) -> list[str]:
    lines: list[str] = []
    for line in section_body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(COLUMN_PREFIX):
            lines.append(stripped)
    return lines


def upsert_entry(section_body: str, entry: str, review_ref: str) -> str:
    lines = canonical_lines(section_body)
    replaced = False
    updated: list[str] = []
    for line in lines:
        if line.split(" | ", 1)[0] == f"{COLUMN_PREFIX}{review_ref}":
            updated.append(entry)
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(entry)
    return "\n".join(updated)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--review", action="append", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--note", required=True)
    args = parser.parse_args()

    plan_path = Path(args.plan).resolve()
    text = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""
    before, section_body, trailing = parse_sections(text)

    for raw in args.review:
        review_path = Path(raw).resolve()
        review_ref = relative_review_path(plan_path, review_path)
        entry = render_entry(review_ref, args.status, args.note)
        section_body = upsert_entry(section_body, entry, review_ref)

    parts = [before] if before else []
    parts.append(SECTION_HEADER)
    if section_body:
        parts.append(section_body)
    if trailing:
        parts.append(trailing)
    plan_path.write_text("\n\n".join(part for part in parts if part is not None and part != "").rstrip() + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
