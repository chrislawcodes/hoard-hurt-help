#!/usr/bin/env python3
"""command_audit — classify Feature Factory runs as closed/active/abandoned/empty.

Walks FACTORY_RUNS_ROOT and classifies each slug directory based on the
contents of state.json:

  closed    — delivery is non-empty (run was closed out with a branch/PR)
  active    — stages or token_usage is populated AND delivery is empty
  abandoned — spec.md or tasks.md is present but no stages and no token_usage
  empty     — no real work product (just a directory, possibly with scaffolding)

This command is a READ-ONLY reporter. It never mutates any file.

Exit code is always 0.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import factory_state
from factory_mutating import readonly_command  # noqa: E402

# ---------------------------------------------------------------------------
# Work-product files that indicate a human put real intent into a slug dir.
# ---------------------------------------------------------------------------
_WORK_FILES = {"spec.md", "tasks.md", "plan.md"}


def _last_modified(slug_dir: Path) -> str:
    """Return the most-recent mtime across all files in slug_dir as YYYY-MM-DD."""
    try:
        mtimes = [
            f.stat().st_mtime
            for f in slug_dir.rglob("*")
            if f.is_file()
        ]
        if not mtimes:
            return "unknown"
        ts = max(mtimes)
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return "unknown"


def _files_present(slug_dir: Path) -> list[str]:
    """Return names of files in the top level of slug_dir (not recursive)."""
    try:
        return sorted(f.name for f in slug_dir.iterdir() if f.is_file())
    except Exception:
        return []


def _delivery_is_nonempty(delivery: object) -> bool:
    """Return True if delivery looks like a meaningful closed-out state."""
    if isinstance(delivery, dict) and delivery:
        # Require at least one of the standard delivery keys to be present.
        # This avoids treating an empty dict or a dict with only null values
        # as a real delivery.
        return bool(
            delivery.get("branch")
            or delivery.get("pr_url")
            or delivery.get("pr_number")
            or delivery.get("completed")
        )
    return False


def _classify_slug(slug_dir: Path) -> dict:
    """Return a classification record for one slug directory."""
    slug = slug_dir.name
    files = _files_present(slug_dir)
    last_mod = _last_modified(slug_dir)
    has_work = bool(_WORK_FILES & set(files))

    state_path = slug_dir / "state.json"
    if not state_path.exists():
        category = "abandoned" if has_work else "empty"
        return {
            "slug": slug,
            "category": category,
            "files": files,
            "last_modified": last_mod,
            "last_stage": None,
            "parse_error": False,
        }

    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "slug": slug,
            "category": "abandoned",
            "files": files,
            "last_modified": last_mod,
            "last_stage": None,
            "parse_error": True,
            "parse_error_detail": str(exc),
        }

    delivery = raw.get("delivery")
    stages = raw.get("stages")
    token_usage = raw.get("token_usage")

    has_delivery = _delivery_is_nonempty(delivery)
    has_stages = bool(stages)
    has_tokens = bool(token_usage)

    last_stage: str | None = None
    if isinstance(stages, dict) and stages:
        last_stage = list(stages.keys())[-1]

    if has_delivery:
        category = "closed"
    elif has_stages or has_tokens:
        category = "active"
    elif has_work:
        category = "abandoned"
    else:
        category = "empty"

    return {
        "slug": slug,
        "category": category,
        "files": files,
        "last_modified": last_mod,
        "last_stage": last_stage,
        "parse_error": False,
    }


def _walk_runs_root() -> list[dict]:
    """Walk FACTORY_RUNS_ROOT and return a list of classification records."""
    runs_root = factory_state.FACTORY_RUNS_ROOT
    if not runs_root.exists():
        return []
    records: list[dict] = []
    for slug_dir in sorted(runs_root.iterdir()):
        if slug_dir.is_dir():
            records.append(_classify_slug(slug_dir))
    return records


def _render_markdown(records: list[dict], today: str) -> str:
    """Render the audit report as markdown."""
    by_cat: dict[str, list[dict]] = {
        "abandoned": [],
        "active": [],
        "closed": [],
        "empty": [],
    }
    for r in records:
        by_cat.setdefault(r["category"], []).append(r)

    lines: list[str] = [f"# FF Run Audit ({today})", ""]

    # Abandoned
    abandoned = by_cat["abandoned"]
    lines.append(f"## Abandoned ({len(abandoned)}) — files present but no runner activity")
    if abandoned:
        lines.append("")
        lines.append("| slug | files | last_modified |")
        lines.append("| --- | --- | --- |")
        for r in abandoned:
            files_str = ", ".join(r["files"]) if r["files"] else "—"
            parse_note = " *(state.json parse error)*" if r.get("parse_error") else ""
            lines.append(f"| {r['slug']}{parse_note} | {files_str} | {r['last_modified']} |")
    lines.append("")

    # Active
    active = by_cat["active"]
    lines.append(f"## Active ({len(active)}) — stages populated, no delivery yet")
    if active:
        lines.append("")
        lines.append("| slug | last_stage | last_modified |")
        lines.append("| --- | --- | --- |")
        for r in active:
            last_stage = r.get("last_stage") or "—"
            lines.append(f"| {r['slug']} | {last_stage} | {r['last_modified']} |")
    lines.append("")

    # Closed
    closed = by_cat["closed"]
    lines.append(f"## Closed ({len(closed)})")
    lines.append("")

    # Empty
    empty = by_cat["empty"]
    lines.append(f"## Empty ({len(empty)})")
    lines.append("")

    return "\n".join(lines)


@readonly_command("audit")
def command_audit(args: argparse.Namespace) -> int:
    """Classify all FF runs and emit a report to stdout or a file."""
    records = _walk_runs_root()
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    use_json = getattr(args, "json", False)
    out_path_str = getattr(args, "out", None)

    if use_json:
        output = json.dumps(
            {"date": today, "runs": records},
            indent=2,
        )
    else:
        output = _render_markdown(records, today)

    if out_path_str:
        out_path = Path(out_path_str)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"[ff] audit report written to {out_path}", file=sys.stderr)
    else:
        print(output)

    return 0
