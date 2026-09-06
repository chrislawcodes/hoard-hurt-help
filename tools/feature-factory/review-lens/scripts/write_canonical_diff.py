#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from workflow_utils import repo_relative_path


BASE_REF_CANDIDATES = ["origin/main", "origin/master", "main", "master"]
EMPTY_DIFF_ERROR = "Canonical diff is empty for the requested scope"


def git(repo: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return None
    return result.stdout.rstrip("\n")


def resolve_base_ref(repo: Path, requested: str | None) -> tuple[str, str]:
    candidates: list[str] = []
    if requested:
        candidates.append(requested)
    upstream = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if upstream:
        candidates.append(upstream)
    candidates.extend(BASE_REF_CANDIDATES)

    for candidate in candidates:
        base_sha = git(repo, "merge-base", candidate, "HEAD")
        if base_sha:
            return candidate, base_sha
    raise RuntimeError("Could not resolve a merge base for the canonical diff")


def validate_scope_paths(repo: Path, scope: list[str]) -> None:
    missing = [path for path in scope if not (repo / path).exists()]
    if missing:
        raise RuntimeError(f"Canonical diff scope contains missing paths: {', '.join(sorted(missing))}")


def normalize_tolerated_paths(repo: Path, raw_paths: list[str]) -> list[str]:
    normalized: set[str] = set()
    repo_root = repo.resolve()
    for raw in raw_paths:
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped in {".", "./", "/"}:
            raise RuntimeError("Dirty-path tolerance cannot target the repo root")
        candidate = Path(stripped)
        resolved = candidate.resolve() if candidate.is_absolute() else (repo_root / candidate).resolve()
        try:
            relative = resolved.relative_to(repo_root)
        except ValueError as exc:
            raise RuntimeError(f"Dirty-path tolerance escapes the repo: {raw}") from exc
        text = str(relative).rstrip("/")
        if not text:
            raise RuntimeError("Dirty-path tolerance cannot target the repo root")
        normalized.add(text)
    return sorted(normalized)


def load_scope(path_manifest: Path | None, paths: list[str]) -> tuple[list[str], list[str]]:
    scope = list(paths)
    allowed_dirty = list(paths)
    if path_manifest:
        payload = json.loads(path_manifest.read_text(encoding="utf-8"))
        scope.extend(payload.get("paths", []))
        allowed_dirty.extend(payload.get("allowed_dirty_paths", []))
    normalized = sorted({path.rstrip("/") for path in scope if path.strip()})
    dirty_scope = sorted({path.rstrip("/") for path in allowed_dirty if path.strip()})
    if not normalized:
        raise RuntimeError("Canonical diff generation requires an explicit feature path scope")
    return normalized, dirty_scope or normalized


def parse_dirty_paths(repo: Path) -> list[str]:
    raw = git(repo, "status", "--porcelain") or ""
    paths: list[str] = []
    for line in raw.splitlines():
        if len(line) < 4:
            continue
        candidate = line[3:]
        if " -> " in candidate:
            candidate = candidate.split(" -> ", 1)[1]
        paths.append(candidate)
    return paths


def untracked_scope_files(repo: Path, scope: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--others", "--exclude-standard", "--", *scope],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return sorted(path for path in files if path_allowed(path, scope))


def diff_for_untracked_file(repo: Path, rel_path: str) -> str:
    full_path = repo / rel_path
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--no-index", "--", "/dev/null", str(full_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError(f"Could not generate untracked diff for {rel_path}")
    return result.stdout.replace(str(full_path), rel_path)


def path_allowed(path: str, allowed: list[str]) -> bool:
    normalized = path.rstrip("/")
    for prefix in allowed:
        if normalized == prefix or normalized.startswith(prefix + "/"):
            return True
    return False


def ensure_dirty_scope(repo: Path, scope: list[str], tolerated: list[str]) -> None:
    dirty_paths = parse_dirty_paths(repo)
    unrelated = sorted(
        path for path in dirty_paths if not path_allowed(path, scope) and not path_allowed(path, tolerated)
    )
    if unrelated:
        joined = ", ".join(unrelated)
        raise RuntimeError(f"Canonical diff scope is dirty outside the feature paths: {joined}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--output", required=True)
    parser.add_argument("--base-ref")
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--path-manifest")
    parser.add_argument("--allow-dirty-outside-scope", action="store_true")
    parser.add_argument("--allow-dirty-path", action="append", default=[])
    parser.add_argument("--allow-empty-diff", action="store_true")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    repo_root = git(repo, "rev-parse", "--show-toplevel")
    if not repo_root:
        raise RuntimeError(f"{repo} is not inside a git repository")
    repo = Path(repo_root)

    path_manifest = Path(args.path_manifest).resolve() if args.path_manifest else None
    scope, dirty_scope = load_scope(path_manifest, args.path)
    validate_scope_paths(repo, scope)
    tolerated_dirty = normalize_tolerated_paths(repo, args.allow_dirty_path)

    if not args.allow_dirty_outside_scope:
        ensure_dirty_scope(repo, dirty_scope, tolerated_dirty)

    head_sha = git(repo, "rev-parse", "HEAD")
    if not head_sha:
        raise RuntimeError("Could not resolve HEAD")

    base_ref, base_sha = resolve_base_ref(repo, args.base_ref)

    diff_cmd = ["git", "-C", str(repo), "diff", base_sha, "--", *scope]
    diff = subprocess.run(diff_cmd, check=True, capture_output=True, text=True, timeout=60).stdout
    untracked_files = untracked_scope_files(repo, scope)
    if untracked_files:
        extra = [diff_for_untracked_file(repo, rel_path) for rel_path in untracked_files]
        diff = "\n".join(part for part in [diff, *extra] if part).rstrip() + "\n"
    if not diff.strip() and not args.allow_empty_diff:
        raise RuntimeError(EMPTY_DIFF_ERROR)
    output_path.write_text(diff, encoding="utf-8")

    meta = {
        "repo_root": ".",
        "git_head_sha": head_sha,
        "git_base_ref": base_ref,
        "git_base_sha": base_sha,
        "paths": scope,
        "allowed_dirty_paths": tolerated_dirty,
        "untracked_files_included": untracked_files,
        "artifact_path": repo_relative_path(output_path, repo),
        "allow_empty_diff": args.allow_empty_diff,
    }
    meta_path = output_path.with_suffix(output_path.suffix + ".json")
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(str(output_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
