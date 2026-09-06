#!/usr/bin/env python3
import argparse
import re
import sys
import unicodedata
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - exercised by monkeypatch in tests
    yaml = None  # type: ignore[assignment]

SECTION_HEADER = "## Review Reconciliation"
COLUMN_PREFIX = "- review: "
TERMINAL_STATUSES = {"accepted", "rejected", "deferred"}
_WARNED_YAML_FALLBACK = False


def canonical_note(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", text).strip()


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path} is missing frontmatter")
    _, rest = text.split("---\n", 1)
    fm_text, _ = rest.split("\n---\n", 1)
    global _WARNED_YAML_FALLBACK
    if yaml is not None:
        loaded = yaml.safe_load(fm_text)  # type: ignore[union-attr]
        if not isinstance(loaded, dict):
            raise ValueError(f"{path} frontmatter is not a mapping")
        return {str(key): value for key, value in loaded.items()}
    if not _WARNED_YAML_FALLBACK:
        print(
            "yaml not available; falling back to byte comparison; YAML escaping mismatches may surface as note mismatches",
            file=sys.stderr,
        )
        _WARNED_YAML_FALLBACK = True
    data: dict[str, str] = {}
    for line in fm_text.splitlines():
        if not line.strip():
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"')
    return data


def parse_reconciliation(plan_path: Path) -> tuple[dict[str, tuple[str, str]], list[str]]:
    text = plan_path.read_text(encoding="utf-8")
    if SECTION_HEADER not in text:
        return {}, []
    _, remainder = text.split(SECTION_HEADER, 1)
    entries: dict[str, tuple[str, str]] = {}
    errors: list[str] = []
    for line in remainder.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("## "):
            break
        if not stripped.startswith(COLUMN_PREFIX):
            errors.append(f"{plan_path} has non-canonical reconciliation line: {stripped}")
            continue
        payload = stripped[len(COLUMN_PREFIX):]
        parts = [part.strip() for part in payload.split("|")]
        if len(parts) < 3:
            errors.append(f"{plan_path} has malformed reconciliation entry: {stripped}")
            continue
        review_ref = parts[0]
        if review_ref in entries:
            errors.append(f"{plan_path} has duplicate reconciliation entry for {review_ref}")
            continue
        status = parts[1].removeprefix("status:").strip()
        note = parts[2].removeprefix("note:").strip()
        entries[review_ref] = (status, note)
    return entries, errors


def review_ref(plan_path: Path, review_path: Path) -> str:
    return str(review_path.resolve().relative_to(plan_path.parent.resolve()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--review", action="append", required=True)
    parser.add_argument(
        "--require-terminal",
        action="store_true",
        help="Require all reconciled reviews to have a terminal status (accepted/rejected/deferred)",
    )
    args = parser.parse_args()

    plan_path = Path(args.plan).resolve()
    entries, errors = parse_reconciliation(plan_path)
    expected_refs: set[str] = set()

    for raw in args.review:
        review_path = Path(raw).resolve()
        ref = review_ref(plan_path, review_path)
        expected_refs.add(ref)
        data = parse_frontmatter(review_path)
        if ref not in entries:
            errors.append(f"{plan_path} is missing reconciliation entry for {ref}")
            continue
        status, note = entries[ref]
        if status != data.get("resolution_status", ""):
            errors.append(f"{plan_path} status mismatch for {ref}")
        frontmatter_note = canonical_note(data.get("resolution_note", ""))
        plan_note = canonical_note(note)
        if frontmatter_note is None or plan_note is None or plan_note != frontmatter_note:
            errors.append(f"{plan_path} note mismatch for {ref}")
        if args.require_terminal and status not in TERMINAL_STATUSES:
            errors.append(
                f"{plan_path} review {ref!r} has non-terminal status {status!r}; "
                f"must be one of: {', '.join(sorted(TERMINAL_STATUSES))}"
            )

    if errors:
        for error in errors:
            print(error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
