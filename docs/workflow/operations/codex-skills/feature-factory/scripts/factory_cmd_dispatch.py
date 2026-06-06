#!/usr/bin/env python3
"""command_dispatch_codex implementation.

Artifacts are written before workflow state is updated. That order is
intentional: an orphan dispatch directory is harmless and easy to clean up,
but a state record that points at missing stdout/stderr artifacts would leave
later checks with a false picture of what actually ran.
"""
import argparse
import hashlib
import os
import shlex
import tempfile
import warnings
import shutil
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import factory_deliver  # noqa: E402
import factory_git  # noqa: E402
import factory_state  # noqa: E402
from factory_mutating import mutates_state  # noqa: E402
from factory_io import read_text, write_text  # noqa: E402

_REVIEW_LENS_DIR = factory_state.REPO_ROOT / "docs/workflow/operations/codex-skills/review-lens/scripts"
if str(_REVIEW_LENS_DIR) not in sys.path:
    sys.path.insert(0, str(_REVIEW_LENS_DIR))

from run_gemini_review import is_codex_quota_exhaustion  # noqa: E402


_PROMPT_BYTES_LIMIT = 100_000
_CODEX_TIMEOUT_SECONDS = 600
_CODEX_USAGE_URL = "https://chatgpt.com/codex/settings/usage"
_DEFAULT_MODEL = "gpt-5.4-mini"


def _text_from_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _write_artifacts(dispatch_dir: Path, stdout: str, stderr: str) -> tuple[Path, Path]:
    stdout_path = dispatch_dir / "stdout.txt"
    stderr_path = dispatch_dir / "stderr.txt"
    write_text(stdout_path, stdout)
    write_text(stderr_path, stderr)
    return stdout_path, stderr_path


def _capture_porcelain_with_shas(repo_root: Path) -> tuple[dict[str, str | None], dict[str, str]]:
    repo_arg = shlex.quote(str(repo_root))
    status_out_fd, status_out_path = tempfile.mkstemp()
    status_err_fd, status_err_path = tempfile.mkstemp()
    os.close(status_out_fd)
    os.close(status_err_fd)
    status_cmd = f"cd {repo_arg} && git status --porcelain -uall -- > {shlex.quote(status_out_path)} 2> {shlex.quote(status_err_path)}"
    status_status = os.system(status_cmd)
    try:
        status_exit = os.waitstatus_to_exitcode(status_status)
    except Exception:
        status_exit = status_status
    try:
        status_stdout = Path(status_out_path).read_text(encoding="utf-8")
        status_stderr = Path(status_err_path).read_text(encoding="utf-8")
    finally:
        Path(status_out_path).unlink(missing_ok=True)
        Path(status_err_path).unlink(missing_ok=True)
    if status_exit != 0:
        raise RuntimeError((status_stderr or status_stdout or "").strip() or "git status failed")
    shas: dict[str, str | None] = {}
    statuses: dict[str, str] = {}
    for line in status_stdout.splitlines():
        if len(line) < 4:
            continue
        status_code = line[:2]
        path = line[3:]
        statuses[path] = status_code
        if status_code.strip() == "" or status_code == " D":
            shas[path] = None
            continue
        try:
            path_arg = shlex.quote(path)
            hash_out_fd, hash_out_path = tempfile.mkstemp()
            hash_err_fd, hash_err_path = tempfile.mkstemp()
            os.close(hash_out_fd)
            os.close(hash_err_fd)
            hash_cmd = f"cd {repo_arg} && git hash-object {path_arg} > {shlex.quote(hash_out_path)} 2> {shlex.quote(hash_err_path)}"
            hash_status = os.system(hash_cmd)
            try:
                hash_exit = os.waitstatus_to_exitcode(hash_status)
            except Exception:
                hash_exit = hash_status
            try:
                hash_stdout = Path(hash_out_path).read_text(encoding="utf-8")
            finally:
                Path(hash_out_path).unlink(missing_ok=True)
                Path(hash_err_path).unlink(missing_ok=True)
            shas[path] = hash_stdout.strip() if hash_exit == 0 else None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
            shas[path] = None
    return shas, statuses


def _allocate_dispatch_dir(slug: str, base_id: str) -> Path:
    dispatch_root = factory_state.REPO_ROOT / "docs/workflow/feature-runs" / slug / "codex-dispatches"
    candidate = base_id
    for retry in range(1000):
        dir_path = dispatch_root / candidate
        try:
            dir_path.mkdir(parents=True, exist_ok=False)
            return dir_path
        except FileExistsError:
            if retry == 999:
                break
            candidate = f"{base_id}_{retry:03d}"
    raise RuntimeError(f"could not allocate unique dispatch directory for {base_id}")


def _kill_process_group(proc: subprocess.Popen[str], sig: signal.Signals) -> None:
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return


@mutates_state("dispatch-codex")
def command_dispatch_codex(args: argparse.Namespace) -> int:
    codex_path = shutil.which("codex")
    if codex_path is None:
        print(
            "codex CLI not found on PATH; install or activate before dispatching",
            file=sys.stderr,
        )
        raise SystemExit(2)

    prompt_text = read_text(Path(args.prompt_path))
    prompt_bytes = prompt_text.encode("utf-8")
    if len(prompt_bytes) > _PROMPT_BYTES_LIMIT:
        print(
            f"prompt at {args.prompt_path} is {len(prompt_bytes)} bytes, exceeds "
            f"{_PROMPT_BYTES_LIMIT}-byte hard limit; split into multiple dispatches "
            "or use a follow-up feature to add stdin-based prompts",
            file=sys.stderr,
        )
        raise SystemExit(2)
    prompt_sha256 = hashlib.sha256(prompt_bytes).hexdigest()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        base_id = datetime.utcnow().strftime("%Y%m%dT%H%M%S_%fZ")
    dispatch_dir = _allocate_dispatch_dir(args.slug, base_id)
    dispatch_id = dispatch_dir.name
    dispatch_dir_rel = str(dispatch_dir.relative_to(factory_state.REPO_ROOT))
    dispatch_artifact_prefix = f"{dispatch_dir_rel}/"

    pre_dispatch_dirty: dict[str, str | None] = {}
    pre_dispatch_status: dict[str, str] = {}
    if not getattr(args, "no_auto_commit", False):
        try:
            pre_dispatch_dirty, pre_dispatch_status = _capture_porcelain_with_shas(factory_state.REPO_ROOT)
        except Exception as exc:  # noqa: BLE001 - overlap detection must not block dispatch
            print(
                f"[auto-commit-warning] could not capture pre-dispatch dirty state: {exc}; "
                "overlap detection disabled for this dispatch",
                file=sys.stderr,
            )
            pre_dispatch_dirty = {}
            pre_dispatch_status = {}

    proc = subprocess.Popen(
        [codex_path, "exec", "-m", args.model, "-s", "workspace-write", prompt_text],
        cwd=factory_state.REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    try:
        stdout, stderr = proc.communicate(timeout=_CODEX_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        stdout = _text_from_timeout_output(exc.stdout)
        stderr = _text_from_timeout_output(exc.stderr)
        _write_artifacts(dispatch_dir, stdout, stderr)
        _kill_process_group(proc, signal.SIGTERM)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc, signal.SIGKILL)
        print(
            f"codex exec exceeded {_CODEX_TIMEOUT_SECONDS}s timeout — process group killed",
            file=sys.stderr,
        )
        raise SystemExit(5)

    stdout_path, stderr_path = _write_artifacts(dispatch_dir, stdout, stderr)

    if is_codex_quota_exhaustion(stderr, stdout):
        print(
            f"Codex quota exhausted — see {_CODEX_USAGE_URL}",
            file=sys.stderr,
        )
        raise SystemExit(4)

    try:
        head_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=factory_state.REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        head_sha = head_result.stdout.strip() if head_result.returncode == 0 else None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, OSError):
        head_sha = None

    auto_commit: dict[str, object]
    commit_failed = False
    if getattr(args, "no_auto_commit", False):
        auto_commit = {"skipped": True, "reason": "--no-auto-commit flag"}
    else:
        try:
            post_dispatch_dirty, _ = _capture_porcelain_with_shas(factory_state.REPO_ROOT)
        except Exception as exc:  # noqa: BLE001 - fallback to no-op auto-commit path
            print(f"[auto-commit-warning] could not capture post-dispatch dirty state: {exc}; skipping auto-commit", file=sys.stderr)
            post_dispatch_dirty = {}

        def _is_dispatch_artifact(path: str) -> bool:
            return path == dispatch_dir_rel or path.startswith(dispatch_artifact_prefix)

        pre_paths = {path for path in pre_dispatch_dirty.keys() if not _is_dispatch_artifact(path)}
        post_paths = {path for path in post_dispatch_dirty.keys() if not _is_dispatch_artifact(path)}
        codex_modified_existing_dirty = {
            path
            for path in pre_paths
            if path not in post_paths or pre_dispatch_dirty.get(path) != post_dispatch_dirty.get(path)
        }
        codex_introduced = sorted(post_paths - pre_paths)

        if codex_modified_existing_dirty:
            overlap_paths = sorted(codex_modified_existing_dirty)
            print(
                "[auto-commit-skip] overlap with operator dirty: "
                f"{', '.join(overlap_paths)}. Manual review required. Run 'git add' + 'git commit' "
                "to commit, or 'git checkout' to discard.",
                file=sys.stderr,
            )
            auto_commit = {
                "skipped": True,
                "reason": "overlap with operator dirty",
                "overlap_paths": overlap_paths,
            }
        elif not codex_introduced:
            auto_commit = {"skipped": True, "reason": "no codex-introduced changes"}
        else:
            excluded_pre_existing_paths = sorted(pre_paths)
            try:
                subprocess.run(
                    ["git", "add", "-A", "--", *codex_introduced],
                    cwd=factory_state.REPO_ROOT,
                    check=True,
                    text=True,
                    capture_output=True,
                )
                commit_message = (
                    f"dispatch-codex auto-commit: {Path(args.prompt_path).name}\n\n"
                    f"Prompt sha256: {prompt_sha256}\n"
                    f"Dispatch ID: {dispatch_id}\n"
                    f"Model: {args.model}\n\n"
                    f"Co-Authored-By: Codex ({args.model}) <noreply@openai.com>"
                )
                commit_result = subprocess.run(
                    ["git", "commit", "-m", commit_message],
                    cwd=factory_state.REPO_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if commit_result.returncode != 0:
                    commit_failed = True
                    auto_commit = {
                        "skipped": True,
                        "reason": "git commit failed",
                        "error": (commit_result.stderr or commit_result.stdout or "").strip() or "git commit failed",
                    }
                else:
                    try:
                        commit_head = subprocess.run(
                            ["git", "rev-parse", "HEAD"],
                            cwd=factory_state.REPO_ROOT,
                            capture_output=True,
                            text=True,
                            timeout=10,
                            check=False,
                        )
                        commit_sha = commit_head.stdout.strip() if commit_head.returncode == 0 else None
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                            FileNotFoundError, OSError):
                        commit_sha = None
                    if commit_sha:
                        head_sha = commit_sha
                    auto_commit = {
                        "introduced_paths": codex_introduced,
                        "excluded_pre_existing_paths": excluded_pre_existing_paths,
                        "commit_sha": commit_sha,
                    }
            except subprocess.CalledProcessError as exc:
                commit_failed = True
                auto_commit = {
                    "skipped": True,
                    "reason": "git commit failed",
                    "error": (exc.stderr or exc.stdout or str(exc)).strip(),
                }

    branch_base = factory_deliver._resolve_branch_base()
    if branch_base is not None:
        lines_added = factory_deliver._added_code_lines(branch_base)
    else:
        lines_added = None

    record = {
        "head_sha": head_sha,
        "ts": dispatch_id,
        "prompt_path": str(args.prompt_path),
        "prompt_sha256": prompt_sha256,
        "model": args.model,
        "exit_code": proc.returncode,
        "stdout_path": str(stdout_path.relative_to(factory_state.REPO_ROOT)),
        "stderr_path": str(stderr_path.relative_to(factory_state.REPO_ROOT)),
        "branch_base_sha": branch_base,
        "lines_added_at_dispatch_time": lines_added,
        "auto_commit": auto_commit,
    }

    def mutate(state: dict) -> None:
        state.setdefault("codex_dispatches", []).append(record)

    factory_state.update_state(args.slug, mutate)
    if commit_failed:
        print(
            (record["auto_commit"].get("error") if isinstance(record.get("auto_commit"), dict) else "git commit failed"),
            file=sys.stderr,
        )
        return 1
    print(f"[workflow] ✓ dispatch-codex ({dispatch_id}) → exit {proc.returncode}")
    return 0 if proc.returncode == 0 else 1
