#!/usr/bin/env python3
"""Git and subprocess utilities for the feature factory runner.

Pure wrappers around git, shell commands, and filesystem scaffolding.
No domain knowledge — just tool invocation.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_state import REPO_ROOT  # noqa: E402

SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync-codex-skills.py"

# Files that agents (Codex, Gemini) frequently edit out-of-scope.
# revert_protected_files() restores these to HEAD after every agent subprocess.
PROTECTED_FILES = [
    "CLAUDE.md",
    "AGENTS.md",
    "MEMORY.md",
    "DESIGN.md",
    ".gitignore",
]


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, text=True)


def git_output(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result.stdout.strip()
    except Exception:
        return None


def _sha_is_valid_ancestor(sha: str) -> bool:
    """Return True iff sha exists in the repo AND is an ancestor of HEAD."""
    if not sha:
        return False
    # Check existence first.
    try:
        subprocess.run(
            ["git", "-C", str(REPO_ROOT), "cat-file", "-t", sha],
            check=True, capture_output=True, timeout=10,
        )
    except Exception:
        return False
    # Check ancestry.
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "merge-base", "--is-ancestor", sha, "HEAD"],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _git_head_sha(repo: Path) -> str | None:
    """Return the current HEAD SHA for repo, or None if unavailable."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except Exception:
        return None


def current_branch_name() -> str | None:
    branch = git_output("rev-parse", "--abbrev-ref", "HEAD")
    if not branch or branch == "HEAD":
        return None
    return branch


def upstream_branch_name() -> str | None:
    return git_output("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")


def commits_behind_upstream() -> int | None:
    """Return how many commits HEAD is behind its upstream, or None if unknown."""
    result = git_output("rev-list", "--count", "HEAD..@{upstream}")
    if result is None:
        return None
    try:
        return int(result)
    except ValueError:
        return None


def repo_remote_url(remote_name: str) -> str | None:
    return git_output("remote", "get-url", remote_name)


def command_path(name: str) -> str | None:
    return shutil.which(name)


def ensure_sync() -> None:
    run([sys.executable, str(SYNC_SCRIPT), "--sync-if-needed"])


def ensure_file(path: Path, heading: str) -> None:
    if not path.exists():
        path.write_text(f"# {heading}\n", encoding="utf-8")


def create_worktree(slug: str, index: int, run_fn=subprocess.run) -> Path:
    safe_slug = "".join(
        ch if ch.isalnum() or ch in "-_" else "_"
        for ch in slug
    )
    path = Path(f"/tmp/wt-{safe_slug}-{os.getpid()}-{index}")

    try:
        if path.exists():
            remove_worktree(path, run_fn)
        run_fn(
            ["git", "-C", str(REPO_ROOT), "worktree", "add", str(path), "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        )
        return path
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"failed to create worktree at {path}: {exc.stderr or exc.stdout or exc}"
        ) from exc
    except RuntimeError as exc:
        raise RuntimeError(f"failed to create worktree at {path}: {exc}") from exc


def remove_worktree(path: Path, run_fn=subprocess.run) -> None:
    if not path.exists():
        return
    try:
        run_fn(
            ["git", "-C", str(REPO_ROOT), "worktree", "remove", "--force", str(path)],
            check=True,
            text=True,
            capture_output=True,
        )
        run_fn(
            ["git", "-C", str(REPO_ROOT), "worktree", "prune"],
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        if not path.exists():
            return
        raise RuntimeError(
            f"failed to remove worktree at {path}: {exc.stderr or exc.stdout or exc}"
        ) from exc


def remove_all_worktrees(paths: list[Path], run_fn=subprocess.run) -> None:
    def _run_without_prune(cmd: list[str], **kwargs):
        if cmd == ["git", "-C", str(REPO_ROOT), "worktree", "prune"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return run_fn(cmd, **kwargs)

    for path in paths:
        try:
            remove_worktree(path, run_fn=_run_without_prune)
        except RuntimeError as exc:
            print(f"warning: {exc}", file=sys.stderr)
        except subprocess.CalledProcessError as exc:
            print(
                f"warning: failed to remove worktree at {path}: {exc.stderr or exc.stdout or exc}",
                file=sys.stderr,
            )
    try:
        run_fn(
            ["git", "-C", str(REPO_ROOT), "worktree", "prune"],
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        print(
            f"warning: failed to prune worktrees: {exc.stderr or exc.stdout or exc}",
            file=sys.stderr,
        )


def get_new_commits(worktree_path: Path, base_sha: str, run_fn=subprocess.run) -> list[str]:
    try:
        result = run_fn(
            ["git", "log", "--reverse", "--format=%H", f"{base_sha}..HEAD"],
            cwd=str(worktree_path),
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"failed to get commits in {worktree_path} since {base_sha}: {exc.stderr or exc.stdout or exc}"
        ) from exc
    output = result.stdout.strip()
    if not output:
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


def stage_and_commit_if_dirty(worktree_path: Path, message: str, run_fn=subprocess.run) -> str | None:
    try:
        status = run_fn(
            ["git", "-C", str(worktree_path), "status", "--porcelain"],
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"failed to check status for {worktree_path}: {exc.stderr or exc.stdout or exc}"
        ) from exc

    if not status.stdout.strip():
        return None

    try:
        run_fn(
            ["git", "-C", str(worktree_path), "add", "-A"],
            check=True,
            text=True,
            capture_output=True,
        )
        run_fn(
            ["git", "-C", str(worktree_path), "commit", "-m", message],
            check=True,
            text=True,
            capture_output=True,
        )
        rev_parse = run_fn(
            ["git", "-C", str(worktree_path), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"failed to commit changes in {worktree_path}: {exc.stderr or exc.stdout or exc}"
        ) from exc
    return rev_parse.stdout.strip()


def cherry_pick_commits(commits: list[str], run_fn=subprocess.run) -> tuple[bool, str]:
    if not commits:
        return True, ""

    for sha in commits:
        try:
            run_fn(
                ["git", "-C", str(REPO_ROOT), "cherry-pick", sha],
                check=True,
                text=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            try:
                run_fn(
                    ["git", "-C", str(REPO_ROOT), "cherry-pick", "--abort"],
                    check=True,
                    text=True,
                    capture_output=True,
                )
            except Exception:
                pass
            return False, f"cherry-pick failed on {sha}: {exc.stderr or exc.stdout or exc}"

    return True, ""


def revert_protected_files() -> list[str]:
    """Revert PROTECTED_FILES to HEAD. Returns list of files actually reverted."""
    # Only revert files that are tracked and have been modified.
    dirty = git_output("diff", "--name-only", "HEAD", "--")
    if not dirty:
        return []
    dirty_set = set(dirty.splitlines())
    to_revert = [f for f in PROTECTED_FILES if f in dirty_set]
    if not to_revert:
        return []
    try:
        subprocess.run(
            ["git", "-C", str(REPO_ROOT), "checkout", "HEAD", "--"] + to_revert,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        print(f"warning: failed to revert protected files: {exc}", file=sys.stderr)
        return []
    return to_revert
