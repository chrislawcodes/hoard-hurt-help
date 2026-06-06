#!/usr/bin/env python3
import hashlib
import re
from pathlib import Path


def repo_relative_path(path: Path, repo_root: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(repo_root.resolve()))
    except ValueError:
        return str(resolved)


def resolve_stored_path(raw: str, repo_root: Path, stored_repo_root: str = "") -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        return (repo_root / candidate).resolve()

    if candidate.exists():
        return candidate.resolve()

    if stored_repo_root:
        try:
            return (repo_root / candidate.relative_to(Path(stored_repo_root))).resolve()
        except ValueError:
            pass

    repo_name = repo_root.name
    if repo_name in candidate.parts:
        idx = candidate.parts.index(repo_name)
        suffix = candidate.parts[idx + 1 :]
        if suffix:
            return (repo_root / Path(*suffix)).resolve()

    return candidate


def _strip_plan_reconciliation_section(text: str) -> str:
    lines = text.splitlines(keepends=True)
    in_fence = False
    starts: list[int] = []
    heading_re = re.compile(r"^##\s+Review Reconciliation\s*$")
    for idx, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and heading_re.match(line.rstrip()):
            starts.append(idx)
    if len(starts) != 1:
        return text
    start = starts[0]
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        if re.match(r"^##\s+\S", lines[idx]):
            end = idx
            break
    return "".join(lines[:start]).rstrip() + "\n\n" + "".join(lines[end:]).lstrip()


def normalized_artifact_text(stage: str, path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if stage == "plan":
        return _strip_plan_reconciliation_section(text).rstrip() + "\n"
    return text


def normalized_artifact_hash(stage: str, path: Path) -> str:
    return hashlib.sha256(normalized_artifact_text(stage, path).encode("utf-8")).hexdigest()


def full_artifact_hash(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def artifact_hash_matches(stage: str, path: Path, data: dict) -> bool:
    current = normalized_artifact_hash(stage, path)
    if data.get("artifact_sha256") == current:
        return True
    if stage == "plan" and not data.get("narrowed_artifact_sha256"):
        return data.get("artifact_sha256") == full_artifact_hash(path)
    return False
