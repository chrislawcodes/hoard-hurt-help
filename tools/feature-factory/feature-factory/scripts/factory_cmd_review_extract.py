#!/usr/bin/env python3
"""Deterministic review finding extractor."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_mutating import readonly_command  # noqa: E402
from factory_io import read_text  # noqa: E402
from factory_state import FACTORY_RUNS_ROOT, REPO_ROOT  # noqa: E402
from factory_review_specs import _ACTIONABLE_FINDING_RE  # noqa: E402

try:  # pragma: no cover - optional dependency
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None


_FINDINGS_HEADING = re.compile(r"^##\s+Findings\b", re.IGNORECASE)
_NEXT_SECTION_HEADING = re.compile(r"^##\s+\S")


def _parse_frontmatter(text: str) -> tuple[dict, int]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing opening frontmatter delimiter ---")
    end_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        raise ValueError("missing closing frontmatter delimiter ---")
    raw = "\n".join(lines[1:end_idx])
    if yaml is not None:
        try:
            parsed = yaml.safe_load(raw) or {}
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"frontmatter parse failed: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("frontmatter must parse to a mapping")
        return parsed, end_idx + 1
    data: dict[str, str] = {}
    for line in lines[1:end_idx]:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data, end_idx + 1


def _extract_findings(body_lines: list[str], body_offset: int) -> list[dict]:
    findings_start = None
    for i, line in enumerate(body_lines):
        if _FINDINGS_HEADING.match(line):
            findings_start = i + 1
            break
    if findings_start is None:
        return []

    fence_open = False
    starts: list[tuple[int, str, str]] = []
    end_of_findings = len(body_lines)
    for i in range(findings_start, len(body_lines)):
        line = body_lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            fence_open = not fence_open
            continue
        if fence_open:
            continue
        if _NEXT_SECTION_HEADING.match(line):
            end_of_findings = i
            break
        match = _ACTIONABLE_FINDING_RE.match(line.lower())
        if not match:
            continue
        severity_match = re.search(r"\b(critical|high|medium)\b", line, re.IGNORECASE)
        if severity_match is None:
            continue
        severity = severity_match.group(1).upper()
        first_line = line[match.end():].strip().lstrip(":*").strip()
        if not first_line:
            for j in range(i + 1, min(i + 6, len(body_lines))):
                candidate = body_lines[j].strip()
                if candidate and not candidate.startswith("```"):
                    first_line = candidate.lstrip("-*").strip()
                    break
        starts.append((i, severity, first_line))

    records: list[dict] = []
    for idx, (start_idx, severity, first_line) in enumerate(starts):
        if idx + 1 < len(starts):
            end_idx = starts[idx + 1][0] - 1
        else:
            end_idx = end_of_findings - 1
        records.append(
            {
                "review_index": idx,
                "severity": severity,
                "first_line": first_line,
                "line_start": start_idx + 1 + body_offset,
                "line_end": end_idx + 1 + body_offset,
            }
        )
    return records


@readonly_command("review-extract")
def command_review_extract(args: argparse.Namespace) -> int:
    slug_dir = FACTORY_RUNS_ROOT / args.slug
    reviews_dir = slug_dir / "reviews"
    if not reviews_dir.exists():
        print(f"reviews dir not found: {reviews_dir}", file=sys.stderr)
        return 2

    records: list[dict] = []
    for review_path in sorted(reviews_dir.glob(f"{args.stage}.*.review.md")):
        text = read_text(review_path)
        try:
            frontmatter, body_offset = _parse_frontmatter(text)
        except ValueError as exc:
            print(f"error: malformed frontmatter in {review_path}: {exc}", file=sys.stderr)
            return 2
        body_lines = text.splitlines()[body_offset:]
        for record in _extract_findings(body_lines, body_offset):
            try:
                review_rel = str(review_path.relative_to(REPO_ROOT))
            except Exception:
                review_rel = str(review_path)
            record["review"] = review_rel
            record["frontmatter_status"] = frontmatter.get("resolution_status")
            records.append(record)

    output = json.dumps(records, indent=2) if args.format == "json" else "\n".join(json.dumps(record) for record in records)
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
    else:
        print(output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--stage", required=True, choices=["spec", "plan", "tasks", "diff", "closeout"])
    parser.add_argument("--format", choices=["jsonl", "json"], default="jsonl")
    parser.add_argument("--out")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return command_review_extract(args)


if __name__ == "__main__":
    raise SystemExit(main())
