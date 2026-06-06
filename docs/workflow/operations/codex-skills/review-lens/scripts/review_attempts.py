#!/usr/bin/env python3
"""Central metadata logging for review runner attempts.

This stores only sizes, model metadata, result status, and paths. It does not
store prompt text, artifact contents, review bodies, secrets, or credentials.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from workflow_utils import repo_relative_path


def review_attempt_log_path(repo_root: Path) -> Path:
    return repo_root / "docs" / "workflow" / "operations" / "review-attempts.jsonl"


def infer_slug(review_path: Path, repo_root: Path) -> str:
    rel = repo_relative_path(review_path, repo_root)
    parts = Path(rel).parts
    feature_runs = ("docs", "workflow", "feature-runs")
    for idx in range(len(parts) - len(feature_runs)):
        if parts[idx : idx + len(feature_runs)] == feature_runs:
            slug_idx = idx + len(feature_runs)
            return parts[slug_idx] if slug_idx < len(parts) else ""
    return ""


def append_review_attempt(record: dict[str, Any], repo_root: Path) -> None:
    path = review_attempt_log_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def review_attempt_record(
    *,
    repo_root: Path,
    reviewer: str,
    model: str,
    stage: str,
    lens: str,
    artifact_chars: int,
    context_chars: int,
    total_chars: int,
    max_artifact_chars: int,
    max_context_chars: int,
    max_total_chars: int,
    coverage_status: str,
    coverage_note: str,
    result: str,
    exit_code: int,
    duration_seconds: float,
    artifact_sha256: str,
    review_path: Path,
    error_summary: str = "",
) -> dict[str, Any]:
    return {
        "timestamp": int(time.time()),
        "slug": infer_slug(review_path, repo_root),
        "stage": stage,
        "reviewer": reviewer,
        "model": model,
        "lens": lens,
        "artifact_chars": artifact_chars,
        "context_chars": context_chars,
        "total_chars": total_chars,
        "max_artifact_chars": max_artifact_chars,
        "max_context_chars": max_context_chars,
        "max_total_chars": max_total_chars,
        "coverage_status": coverage_status,
        "coverage_note": coverage_note,
        "result": result,
        "exit_code": exit_code,
        "duration_seconds": round(duration_seconds, 3),
        "artifact_sha256": artifact_sha256,
        "review_path": repo_relative_path(review_path, repo_root),
        "error_summary": error_summary,
    }
