#!/usr/bin/env python3
"""Quick-mode review command: skip the full spec/plan/tasks cycle and run
a single diff-stage review against origin/main..HEAD.

This is opt-in for small features where the full machinery is overkill.
The operator decides what to do with any findings — this command exits 0
regardless of severity counts.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

_REVIEW_LENS_DIR = _SCRIPT_DIR.parents[1] / "review-lens" / "scripts"
if str(_REVIEW_LENS_DIR) not in sys.path:
    sys.path.insert(0, str(_REVIEW_LENS_DIR))

import factory_state  # noqa: E402
from factory_mutating import readonly_command  # noqa: E402


def _resolve_diff_base() -> str:
    """Return the best available diff base ref (origin/main or origin/master)."""
    for candidate in ("origin/main", "origin/master"):
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--verify", candidate],
                cwd=factory_state.REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return candidate
        except (subprocess.TimeoutExpired, OSError):
            continue
    return "origin/main"


def _write_diff_artifact(slug: str) -> Path:
    """Write origin/main..HEAD diff to the reviews dir and return the path."""
    diff_path = factory_state.reviews_dir(slug) / "quick.diff.txt"
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    base_ref = _resolve_diff_base()
    try:
        result = subprocess.run(
            ["git", "diff", f"{base_ref}...HEAD"],
            cwd=factory_state.REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        diff_text = result.stdout if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, OSError):
        diff_text = ""
    diff_path.write_text(diff_text, encoding="utf-8")
    return diff_path


def _count_severities(review_path: Path) -> dict[str, int]:
    """Count finding entries by severity in a review file.

    Each finding entry is a line that starts an actionable finding
    (bullet, numbered list item, or heading) that contains a severity label.
    Returns a dict with keys HIGH, MEDIUM, LOW, CRITICAL (all default to 0).
    """
    import re

    counts: dict[str, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "CRITICAL": 0}
    if not review_path.exists():
        return counts
    text = review_path.read_text(encoding="utf-8")

    # Match the start of a finding entry (bullet, numbered list, or heading)
    _FINDING_ENTRY_RE = re.compile(
        r"^(?:\s*[-*+]|\s*\d+[.)]\s|\s*#+\s)",
        re.IGNORECASE,
    )
    _SEVERITY_LABEL_RE = re.compile(r"\*{0,2}(critical|high|medium|low)\*{0,2}", re.IGNORECASE)

    in_findings = False
    fence_open = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            fence_open = not fence_open
            continue
        if fence_open:
            continue
        if re.match(r"^##\s+Findings\b", stripped, re.IGNORECASE):
            in_findings = True
            continue
        if in_findings and re.match(r"^##\s+\S", stripped):
            in_findings = False
            continue
        if not in_findings:
            continue
        # Only count lines that start a finding entry
        if not _FINDING_ENTRY_RE.match(line):
            continue
        # Take only the first severity label on this line
        m = _SEVERITY_LABEL_RE.search(line)
        if m:
            label = m.group(1).upper()
            if label in counts:
                counts[label] += 1
    return counts


def _run_codex_review(slug: str, diff_path: Path, output_path: Path) -> int:
    """Shell out to run_codex_review.py and return its exit code."""
    run_codex = _REVIEW_LENS_DIR / "run_codex_review.py"
    cmd = [
        sys.executable,
        str(run_codex),
        "--artifact", str(diff_path),
        "--lens", "correctness-adversarial",
        "--stage", "diff",
        "--output", str(output_path),
        "--workspace-dir", str(factory_state.REPO_ROOT),
    ]
    try:
        result = subprocess.run(cmd, cwd=factory_state.REPO_ROOT, timeout=300)
        return result.returncode
    except subprocess.TimeoutExpired:
        return 124
    except OSError:
        return 1


def _run_gemini_review(slug: str, diff_path: Path, output_path: Path) -> int:
    """Shell out to run_gemini_review.py and return its exit code."""
    run_gemini = _REVIEW_LENS_DIR / "run_gemini_review.py"
    cmd = [
        sys.executable,
        str(run_gemini),
        "--artifact", str(diff_path),
        "--lens", "quality-adversarial",
        "--stage", "diff",
        "--output", str(output_path),
        "--workspace-dir", str(factory_state.REPO_ROOT),
    ]
    try:
        result = subprocess.run(cmd, cwd=factory_state.REPO_ROOT, timeout=300)
        return result.returncode
    except subprocess.TimeoutExpired:
        return 124
    except OSError:
        return 1


@readonly_command("quick")
def command_quick(args: argparse.Namespace) -> int:
    """Run a single diff-stage review for a small feature.

    1. Assert slug exists.
    2. Optionally dispatch Codex if --prompt-path is given.
    3. Run ONE diff review (Codex correctness or Gemini quality).
    4. Write the review file and print a summary.
    5. Exit 0 — operator decides what to do with findings.
    """
    # Step 1: assert slug exists
    slug_dir = factory_state.workflow_dir(args.slug)
    if not slug_dir.exists():
        print(
            f"error: workflow directory not found for slug '{args.slug}'. "
            "Run `init --slug <slug> --path <path>` first.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    # Step 2: optionally dispatch Codex
    if getattr(args, "prompt_path", None):
        from factory_cmd_dispatch import command_dispatch_codex  # noqa: PLC0415
        dispatch_args = argparse.Namespace(
            slug=args.slug,
            prompt_path=args.prompt_path,
            model=getattr(args, "model", "gpt-5.4-mini"),
            no_auto_commit=False,
        )
        rc = command_dispatch_codex(dispatch_args)
        if rc != 0:
            print(
                f"warning: dispatch-codex exited {rc}; continuing to review",
                file=sys.stderr,
            )

    # Step 3: write diff artifact and run review
    diff_path = _write_diff_artifact(args.slug)
    lens = getattr(args, "review_lens", "correctness")

    if lens == "quality":
        review_filename = "diff.gemini.quality-adversarial.review.md"
        output_path = factory_state.reviews_dir(args.slug) / review_filename
        output_path.parent.mkdir(parents=True, exist_ok=True)
        review_rc = _run_gemini_review(args.slug, diff_path, output_path)
    else:
        review_filename = "diff.codex.correctness-adversarial.review.md"
        output_path = factory_state.reviews_dir(args.slug) / review_filename
        output_path.parent.mkdir(parents=True, exist_ok=True)
        review_rc = _run_codex_review(args.slug, diff_path, output_path)

    if review_rc not in (0, 3, 4, 5):
        # Non-zero exit codes from reviewer don't abort; we still summarize
        pass

    # Step 5: print summary
    counts = _count_severities(output_path)
    high = counts.get("HIGH", 0) + counts.get("CRITICAL", 0)
    medium = counts.get("MEDIUM", 0)
    low = counts.get("LOW", 0)

    try:
        rel_path = str(output_path.relative_to(factory_state.REPO_ROOT))
    except ValueError:
        rel_path = str(output_path)

    print(f"review: {rel_path}")
    print(f"severity counts — HIGH/CRITICAL: {high}  MEDIUM: {medium}  LOW: {low}")

    if high > 0:
        print("next-action: address HIGH/CRITICAL findings, then run `deliver`")
    else:
        print("next-action: open PR if no HIGH/CRITICAL findings (run `deliver`)")

    return 0
