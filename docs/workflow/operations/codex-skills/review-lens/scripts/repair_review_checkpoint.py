#!/usr/bin/env python3
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
RUN_GEMINI = SCRIPT_DIR / "run_gemini_review.py"
RUN_CODEX = SCRIPT_DIR / "run_codex_review.py"
VERIFY = SCRIPT_DIR / "verify_review_checkpoint.py"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from workflow_utils import artifact_hash_matches, normalized_artifact_text, resolve_stored_path

REPO_ROOT = SCRIPT_DIR.parents[5]
REPAIR_LIMIT_BUFFER_CHARS = 1000
REPAIR_MIN_TOTAL_BUFFER_CHARS = 10000


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_frontmatter(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path} is missing frontmatter")
    _, rest = text.split("---\n", 1)
    fm_text, body = rest.split("\n---\n", 1)
    data: dict[str, str] = {}
    for line in fm_text.splitlines():
        if not line.strip():
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"')
    return data, body


def review_is_healthy(spec: dict, artifact_path: Path) -> bool:
    review_path = resolve_stored_path(spec["path"], REPO_ROOT)
    if not review_path.exists():
        return False
    try:
        data, body = parse_frontmatter(review_path)
    except Exception:
        return False
    recorded_artifact = resolve_stored_path(data.get("artifact_path", ""), REPO_ROOT, data.get("repo_root", ""))
    if recorded_artifact != artifact_path:
        return False
    if not artifact_hash_matches(spec.get("stage", ""), artifact_path, data):
        return False
    if data.get("resolution_status") == "failed":
        return False
    if data.get("coverage_status") == "partial":
        return False
    for marker in ("# Review:", "## Findings", "## Residual Risks", "## Resolution"):
        if marker not in body:
            return False
    if data.get("reviewer") == "gemini":
        raw_output = data.get("raw_output_path", "")
        if not raw_output or not resolve_stored_path(raw_output, REPO_ROOT, data.get("repo_root", "")).exists():
            return False
    return True


def partial_coverage_reason(spec: dict) -> str:
    review_path = resolve_stored_path(spec["path"], REPO_ROOT)
    if not review_path.exists():
        return ""
    try:
        data, _ = parse_frontmatter(review_path)
    except Exception:
        return ""
    if data.get("coverage_status") != "partial":
        return ""
    return data.get("coverage_note", "")


def context_chars(spec: dict) -> int:
    total = 0
    for raw_path in spec.get("context_paths", []):
        path = resolve_stored_path(raw_path, REPO_ROOT)
        if path.exists():
            total += len(path.read_text(encoding="utf-8"))
    return total


def expanded_checkpoint_for_partial_artifact(spec: dict, artifact_path: Path, checkpoint: dict) -> dict | None:
    if "artifact exceeded max_artifact_chars" not in partial_coverage_reason(spec):
        return None

    artifact_chars = len(normalized_artifact_text(checkpoint["stage"], artifact_path))
    expanded = dict(checkpoint)
    expanded["max_artifact_chars"] = max(
        int(checkpoint.get("max_artifact_chars") or 0),
        artifact_chars + REPAIR_LIMIT_BUFFER_CHARS,
    )
    expanded["max_total_chars"] = max(
        int(checkpoint.get("max_total_chars") or 0),
        expanded["max_artifact_chars"] + context_chars(spec) + REPAIR_MIN_TOTAL_BUFFER_CHARS,
    )
    return expanded


def run_gemini(
    spec: dict,
    artifact_path: Path,
    checkpoint: dict,
    workspace_dir: Path | None,
    timeout_seconds: int,
    retries: int,
) -> None:
    cmd = [
        sys.executable,
        str(RUN_GEMINI),
        "--artifact",
        str(artifact_path),
        "--lens",
        spec["lens"],
        "--stage",
        checkpoint["stage"],
        "--output",
        spec["path"],
    ]
    for context_path in spec.get("context_paths", []):
        cmd.extend(["--context", context_path])
    if checkpoint.get("git_base_ref"):
        cmd.extend(["--git-base-ref", checkpoint["git_base_ref"]])
    if spec.get("model"):
        cmd.extend(["--model", spec["model"]])
    if checkpoint.get("max_artifact_chars"):
        cmd.extend(["--max-artifact-chars", str(checkpoint["max_artifact_chars"])])
    if checkpoint.get("max_context_chars"):
        cmd.extend(["--max-context-chars", str(checkpoint["max_context_chars"])])
    if checkpoint.get("max_total_chars"):
        cmd.extend(["--max-total-chars", str(checkpoint["max_total_chars"])])
    if workspace_dir:
        cmd.extend(["--workspace-dir", str(workspace_dir)])
    cmd.extend(["--timeout-seconds", str(timeout_seconds), "--retries", str(retries)])
    subprocess.run(cmd, check=True, text=True, timeout=timeout_seconds + 30)


def run_codex(
    spec: dict,
    artifact_path: Path,
    checkpoint: dict,
    workspace_dir: Path | None,
    timeout_seconds: int,
    idle_timeout_seconds: int,
) -> None:
    cmd = [
        sys.executable,
        str(RUN_CODEX),
        "--artifact",
        str(artifact_path),
        "--lens",
        spec["lens"],
        "--stage",
        checkpoint["stage"],
        "--output",
        spec["path"],
    ]
    for context_path in spec.get("context_paths", []):
        cmd.extend(["--context", context_path])
    if checkpoint.get("git_base_ref"):
        cmd.extend(["--git-base-ref", checkpoint["git_base_ref"]])
    if spec.get("model"):
        cmd.extend(["--model", spec["model"]])
    if checkpoint.get("max_artifact_chars"):
        cmd.extend(["--max-artifact-chars", str(checkpoint["max_artifact_chars"])])
    if checkpoint.get("max_context_chars"):
        cmd.extend(["--max-context-chars", str(checkpoint["max_context_chars"])])
    if checkpoint.get("max_total_chars"):
        cmd.extend(["--max-total-chars", str(checkpoint["max_total_chars"])])
    if workspace_dir:
        cmd.extend(["--workspace-dir", str(workspace_dir)])
    cmd.extend(["--timeout-seconds", str(timeout_seconds)])
    cmd.extend(["--idle-timeout-seconds", str(idle_timeout_seconds)])
    # Outer wrapper mirrors run_gemini's `timeout_seconds + 30`; run_codex_review's
    # own idle watchdog (--idle-timeout-seconds) is the real liveness control, so
    # this only fires if the inner backstop ceiling itself wedges.
    subprocess.run(cmd, check=True, text=True, timeout=timeout_seconds + 30)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint-manifest", required=True)
    parser.add_argument("--workspace-dir")
    parser.add_argument("--gemini-timeout-seconds", type=int, default=120)
    parser.add_argument("--gemini-retries", type=int, default=1)
    # Codex backstop ceiling + idle watchdog window (parity with the gemini knob).
    parser.add_argument("--codex-timeout-seconds", type=int, default=540)
    parser.add_argument("--codex-idle-timeout-seconds", type=int, default=90)
    args = parser.parse_args()

    manifest_path = Path(args.checkpoint_manifest).resolve()
    checkpoint = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact_path = resolve_stored_path(checkpoint["artifact_path"], REPO_ROOT)
    workspace_dir = Path(args.workspace_dir).resolve() if args.workspace_dir else None

    failed_codex: list[str] = []
    failed_gemini: list[str] = []
    failed_claude: list[str] = []
    for spec in checkpoint.get("required_reviews", []):
        if review_is_healthy(spec, artifact_path):
            continue
        # Claude reviews are produced by orchestrator-spawned subagents, not a CLI
        # this process can launch (spec 020). An unhealthy/missing Claude review is
        # a real failure: surface it loudly so the operator re-runs prepare +
        # assemble, rather than silently dispatching the wrong reviewer's CLI.
        if spec.get("reviewer") == "claude":
            failed_claude.append(
                f"{spec['path']} (claude review missing or unhealthy — "
                "re-run prepare-claude-reviews, have a subagent review each lens, "
                "then assemble with run_claude_review.py before re-checkpointing)"
            )
            continue
        review_checkpoint = expanded_checkpoint_for_partial_artifact(spec, artifact_path, checkpoint)
        if review_checkpoint:
            print(
                f"repair expanding artifact budget for {spec['path']} to "
                f"{review_checkpoint['max_artifact_chars']} chars",
                file=sys.stderr,
            )
        else:
            review_checkpoint = checkpoint
        if spec.get("reviewer") == "gemini":
            try:
                run_gemini(
                    spec,
                    artifact_path,
                    review_checkpoint,
                    workspace_dir,
                    args.gemini_timeout_seconds,
                    args.gemini_retries,
                )
            except subprocess.TimeoutExpired:
                failed_gemini.append(f"{spec['path']} (repair timeout)")
            except subprocess.CalledProcessError:
                failed_gemini.append(f"{spec['path']} (repair failed)")
        else:
            try:
                run_codex(
                    spec,
                    artifact_path,
                    review_checkpoint,
                    workspace_dir,
                    args.codex_timeout_seconds,
                    args.codex_idle_timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                failed_codex.append(f"{spec['path']} (repair timeout)")
            except subprocess.CalledProcessError:
                failed_codex.append(f"{spec['path']} (repair failed)")

    if failed_codex:
        for path in failed_codex:
            print(f"codex review requires retry: {path}")
    if failed_gemini:
        for path in failed_gemini:
            print(f"gemini review requires retry: {path}")
    if failed_claude:
        for path in failed_claude:
            print(f"claude review requires retry: {path}")

    # Diagnose partial-coverage reviews so the operator knows what to do rather
    # than seeing a cryptic "stale" error on every retry.
    for spec in checkpoint.get("required_reviews", []):
        review_path = resolve_stored_path(spec["path"], REPO_ROOT)
        if not review_path.exists():
            continue
        try:
            data, _ = parse_frontmatter(review_path)
        except Exception:
            continue
        if data.get("coverage_status") == "partial":
            print(
                f"partial coverage: artifact is too large for {spec['path']}. "
                "Reduce diff scope, split the feature into smaller workflow slices, "
                "or raise --max-artifact-chars on the checkpoint command.",
                file=sys.stderr,
            )

    verify = subprocess.run(
        [sys.executable, str(VERIFY), "--checkpoint-manifest", str(manifest_path)],
        text=True,
        capture_output=True,
    )
    if verify.stdout:
        print(verify.stdout, end="")
    if verify.stderr:
        print(verify.stderr, end="", file=sys.stderr)
    if (failed_codex or failed_gemini or failed_claude) and verify.returncode == 0:
        return 1
    return verify.returncode


if __name__ == "__main__":
    raise SystemExit(main())
