#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

FEATURE_FACTORY_SCRIPTS = Path(__file__).resolve().parents[2] / "feature-factory" / "scripts"
if str(FEATURE_FACTORY_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(FEATURE_FACTORY_SCRIPTS))

from review_findings import FINDINGS_JSON_CONTRACT_LINES
from workflow_utils import normalized_artifact_text, repo_relative_path
from factory_state import load_workflow_state
from factory_telemetry import record_ai_call
from review_attempts import append_review_attempt, review_attempt_record


BASE_REF_CANDIDATES = ["origin/main", "origin/master", "main", "master"]
GEMINI_LOCK_DIR = ".codex-workflow"
GEMINI_LOCK_NAME = "gemini-review.lock"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(path: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return None
    return result.stdout.strip()


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_gemini_lock(workspace_root: Path, timeout_seconds: int) -> tuple[Path, int]:
    lock_dir = workspace_root / GEMINI_LOCK_DIR
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / GEMINI_LOCK_NAME
    deadline = time.time() + max(timeout_seconds, 30)
    payload = {
        "pid": os.getpid(),
        "started_at": int(time.time()),
        "workspace_root": str(workspace_root),
    }
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            return lock_path, os.getpid()
        except FileExistsError:
            stale = False
            try:
                existing = json.loads(lock_path.read_text(encoding="utf-8"))
                existing_pid = int(existing.get("pid", 0))
                started_at = int(existing.get("started_at", 0))
                stale = not pid_is_alive(existing_pid) or (started_at and time.time() - started_at > timeout_seconds + 120)
            except Exception:
                stale = True
            if stale:
                lock_path.unlink(missing_ok=True)
                continue
            if time.time() >= deadline:
                raise TimeoutError(f"Timed out waiting for Gemini review lock: {lock_path}")
            time.sleep(1)


def release_gemini_lock(lock_path: Path, owner_pid: int) -> None:
    try:
        existing = json.loads(lock_path.read_text(encoding="utf-8"))
        if int(existing.get("pid", -1)) == owner_pid:
            lock_path.unlink(missing_ok=True)
    except Exception:
        lock_path.unlink(missing_ok=True)


def resolve_workspace_root(raw: str | None) -> Path:
    base = Path(raw).resolve() if raw else Path.cwd().resolve()
    if not base.exists() or not base.is_dir():
        raise ValueError(f"Workspace directory does not exist: {base}")
    return base


def allowed_roots(workspace_root: Path) -> list[Path]:
    return [
        workspace_root.resolve(),
        (Path.home() / ".gemini" / "tmp" / workspace_root.name).resolve(),
    ]


def ensure_allowed_path(raw: str, field_name: str, roots: list[Path], must_exist: bool) -> Path:
    candidate = Path(raw).resolve()
    if must_exist and not candidate.exists():
        raise ValueError(f"{field_name} does not exist: {candidate}")
    for root in roots:
        try:
            candidate.relative_to(root)
            return candidate
        except ValueError:
            continue
    joined = ", ".join(str(root) for root in roots)
    raise ValueError(f"{field_name} must stay inside allowed roots: {joined}")


def resolve_repo_info(path: Path, requested_base_ref: str | None) -> dict[str, str]:
    repo_root = git(path.parent if path.is_file() else path, "rev-parse", "--show-toplevel")
    if not repo_root:
        return {
            "repo_root": "",
            "git_head_sha": "",
            "git_base_ref": "",
            "git_base_sha": "",
        }

    repo_path = Path(repo_root)
    head_sha = git(repo_path, "rev-parse", "HEAD") or ""

    candidates: list[str] = []
    if requested_base_ref:
        candidates.append(requested_base_ref)
    upstream = git(repo_path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if upstream:
        candidates.append(upstream)
    candidates.extend(BASE_REF_CANDIDATES)

    git_base_ref = ""
    git_base_sha = ""
    for candidate in candidates:
        base_sha = git(repo_path, "merge-base", candidate, "HEAD")
        if base_sha:
            git_base_ref = candidate
            git_base_sha = base_sha
            break

    return {
        "repo_root": str(repo_path),
        "git_head_sha": head_sha,
        "git_base_ref": git_base_ref,
        "git_base_sha": git_base_sha,
    }


def workflow_slug_from_path(path: Path) -> str | None:
    resolved = path.resolve()
    parts = resolved.parts
    if "feature-runs" not in parts:
        return None
    index = parts.index("feature-runs")
    if index + 1 >= len(parts):
        return None
    return parts[index + 1]


def workflow_slug_from_paths(*paths: Path) -> str | None:
    for path in paths:
        slug = workflow_slug_from_path(path)
        if slug:
            return slug
    return None


def workflow_round_from_paths(stage: str, *paths: Path) -> int:
    slug = workflow_slug_from_paths(*paths)
    if not slug:
        return 0
    state = load_workflow_state(slug)
    stages = state.get("stages", {})
    if not isinstance(stages, dict):
        return 0
    stage_state = stages.get(stage, {})
    if not isinstance(stage_state, dict):
        return 0
    try:
        return int(stage_state.get("adversarial_rounds", 0) or 0)
    except (TypeError, ValueError):
        return 0


def extract_json(stdout: str) -> dict:
    decoder = json.JSONDecoder()
    best: dict | None = None
    for idx, ch in enumerate(stdout):
        if ch != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(stdout[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "response" in payload:
            best = payload
    if best is None:
        raise ValueError("Gemini output did not contain a valid JSON response payload")
    return best


def format_stats(stats: dict) -> str:
    models = stats.get("models", {})
    lines = []
    total_input = 0
    total_output = 0
    total_tokens = 0

    for model_name, model_stats in models.items():
        tokens = model_stats.get("tokens", {})
        input_tokens = tokens.get("input", 0)
        output_tokens = tokens.get("candidates", 0)
        total = tokens.get("total", 0)
        total_input += input_tokens
        total_output += output_tokens
        total_tokens += total
        lines.append(f"- `{model_name}`: input={input_tokens}, output={output_tokens}, total={total}")

    summary = [
        f"- total_input={total_input}",
        f"- total_output={total_output}",
        f"- total_tokens={total_tokens}",
    ]
    return "\n".join(summary + lines)


def frontmatter(metadata: dict[str, str]) -> str:
    lines = ["---"]
    for key, value in metadata.items():
        safe = value.replace('"', '\\"')
        lines.append(f'{key}: "{safe}"')
    lines.append("---")
    return "\n".join(lines)


def write_report(output_path: Path, metadata: dict[str, str], body: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(frontmatter(metadata) + "\n\n" + body, encoding="utf-8")


def text_or_empty(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


SECTION_PATTERN = re.compile(
    r"## Findings\s*(?P<findings>.*?)\s*## Residual Risks\s*(?P<residual>.*)\Z",
    re.DOTALL,
)


def ensure_sections(response: str) -> tuple[str, str]:
    cleaned = response.strip()
    match = SECTION_PATTERN.search(cleaned)
    if not match:
        raise ValueError("Gemini response did not include both required sections")
    findings = match.group("findings").strip() or "No findings returned."
    residual = match.group("residual").strip() or "- No residual risks reported."
    return findings, residual


_QUOTA_PHRASE_PATTERNS = (
    "you've hit your usage limit",
    "usage_limit_exhausted",
    "quota exceeded",
    "monthly quota",
)
_CODEX_CONTEXT_MARKERS = ("openai.com", "chatgpt.com", "codex", "usage")
_CODEX_USAGE_URL = "https://chatgpt.com/codex/settings/usage"


def is_codex_quota_exhaustion(stderr: str, stdout: str) -> bool:
    """Detect Codex quota / usage-limit exhaustion in subprocess output.

    Returns True iff EITHER:
      - one of the explicit phrase patterns matches case-insensitively, OR
      - HTTP 402 or 429 appears AND a Codex/OpenAI context marker
        (openai.com, chatgpt.com, codex, or usage) appears in the same blob.

    Plain `rate limit` text is NOT enough on its own — a Gemini 429 or any
    other 429 should NOT be classified as Codex quota exhaustion. Combined
    so the classifier is canonical (one source of truth, not duplicated).
    """
    blob = f"{stderr}\n{stdout}".lower()
    for phrase in _QUOTA_PHRASE_PATTERNS:
        if phrase in blob:
            return True
    if ("402" in blob or "429" in blob) and any(
        marker in blob for marker in _CODEX_CONTEXT_MARKERS
    ):
        return True
    return False


def write_quota_deferred(
    output_path: Path,
    metadata: dict[str, str],
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
) -> None:
    """Write a review marked as deferred due to Codex quota exhaustion.

    Used when run_codex_review's subprocess fails AND
    :func:`is_codex_quota_exhaustion` matches the output. Replaces the
    `failed` outcome which would block checkpoint progression.
    """
    metadata = dict(metadata)
    metadata["resolution_status"] = "deferred"
    note = f"Codex quota exhausted — re-run after quota refresh. See {_CODEX_USAGE_URL}"
    metadata["resolution_note"] = note
    body_lines = [
        f"# Review: {metadata['stage']} {metadata['lens']}",
        "",
        "## Findings",
        "",
        "Codex quota exhausted before this review completed. The checkpoint is "
        "deferred (not failed) so the workflow can advance; re-run the checkpoint "
        "after quota refresh.",
        "",
        "## Residual Risks",
        "",
        "- Review coverage is reduced for this round; re-run to backfill.",
        "",
        "## Quota Evidence",
    ]
    if stdout_path:
        body_lines.append(f"- stdout: `{stdout_path}`")
    if stderr_path:
        body_lines.append(f"- stderr: `{stderr_path}`")
    body_lines.extend(
        [
            "",
            "## Resolution",
            "- status: deferred",
            f"- note: {note}",
        ]
    )
    write_report(output_path, metadata, "\n".join(body_lines))


def write_failure(
    output_path: Path,
    metadata: dict[str, str],
    message: str,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
) -> None:
    metadata = dict(metadata)
    metadata["resolution_status"] = "failed"
    body_lines = [
        f"# Review: {metadata['stage']} {metadata['lens']}",
        "",
        "## Findings",
        "",
        message,
        "",
        "## Residual Risks",
        "",
        "- Review did not complete successfully, so this checkpoint is not satisfied.",
        "",
        "## Failure Evidence",
    ]
    if stdout_path:
        body_lines.append(f"- stdout: `{stdout_path}`")
    if stderr_path:
        body_lines.append(f"- stderr: `{stderr_path}`")
    body_lines.extend(
        [
            "",
            "## Resolution",
            "- status: failed",
            "- note: review runner failure",
        ]
    )
    write_report(output_path, metadata, "\n".join(body_lines))


def number_lines(text: str) -> str:
    return "\n".join(f"{idx + 1:05d}: {line}" for idx, line in enumerate(text.splitlines()))


# ---------------------------------------------------------------------------
# New-file detection for diff stage (Change 3, PR #832)
#
# When a git diff contains a new file, the diff body is all `+` lines with no
# context. Gemini reads these as partial context and raises false positives
# (e.g. claiming an import is missing when it's on line 5 of the new file).
# For new files, we replace the diff chunk with the full file content so
# Gemini has accurate context.
# ---------------------------------------------------------------------------

_NEW_FILE_HEADER_RE = re.compile(r"^new file mode \d+", re.MULTILINE)
_DIFF_PLUS_PLUS_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)


def _extract_new_file_path(chunk: str) -> str | None:
    """Return the repo-relative path of a new file in a diff chunk, or None."""
    if not _NEW_FILE_HEADER_RE.search(chunk):
        return None
    m = _DIFF_PLUS_PLUS_RE.search(chunk)
    if not m:
        return None
    return m.group(1)


def expand_new_files_in_diff(diff_text: str, repo_root: Path, max_total_chars: int) -> str:
    """Replace new-file diff chunks with the file's full content from disk.

    For each file in the diff that has `new file mode` in its header, reads the
    file from ``repo_root`` and replaces the `+`-prefixed diff lines with a
    ``## New file: <path>`` block containing the raw file content.

    Modified files are left unchanged.  If the file does not exist on disk (e.g.
    the worktree is ahead of origin), the original diff chunk is kept.

    The result is re-capped to ``max_total_chars`` so callers do not need to
    worry about expansion blowing up the prompt size.
    """
    # Split the diff into per-file chunks.
    # "diff --git " starts each new file section.
    parts = diff_text.split("\ndiff --git ")
    if not parts:
        return diff_text

    output_parts: list[str] = []
    for idx, chunk in enumerate(parts):
        # Re-attach the separator (except for the very first chunk which may be
        # a preamble or the first file header without the leading \n).
        if idx > 0:
            chunk = "diff --git " + chunk

        new_file_rel = _extract_new_file_path(chunk)
        if new_file_rel is None:
            output_parts.append(chunk)
            continue

        # New file: try to read from disk
        disk_path = repo_root / new_file_rel
        if not disk_path.exists():
            # File not on disk — keep original diff chunk
            output_parts.append(chunk)
            continue

        try:
            file_content = disk_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            output_parts.append(chunk)
            continue

        # Replace the chunk with the full file content under a clear header
        replacement = f"## New file: {new_file_rel}\n\n{file_content}"
        output_parts.append(replacement)

    result = "\n".join(output_parts)
    # Guard against expansion blowing the budget — keep first max_total_chars chars
    if len(result) > max_total_chars:
        result = result[:max_total_chars]
    return result


def narrow_text(stage: str, text: str, max_chars: int) -> tuple[str, dict]:
    if len(text) <= max_chars:
        return text, {"strategy": "none"}

    if stage == "diff":
        parts = text.split("\ndiff --git ")
        chunks = [part if idx == 0 else "diff --git " + part for idx, part in enumerate(parts)]

        def chunk_label(idx: int, chunk: str) -> str:
            return chunk.splitlines()[0] if chunk.splitlines() else f"chunk-{idx}"

        head_budget = max_chars // 2
        tail_budget = max_chars - head_budget

        head = ""
        head_labels: list[str] = []
        for idx, chunk in enumerate(chunks):
            if len(head) + len(chunk) > head_budget and head:
                break
            next_chunk = chunk if not head else "\n" + chunk
            if len(next_chunk) > head_budget and not head:
                head = next_chunk[:head_budget]
                head_labels.append(chunk_label(idx, chunk))
                break
            head += next_chunk
            head_labels.append(chunk_label(idx, chunk))

        tail = ""
        tail_labels: list[str] = []
        for idx in range(len(chunks) - 1, -1, -1):
            chunk = chunks[idx]
            if chunk_label(idx, chunk) in head_labels:
                break
            next_chunk = chunk if not tail else chunk + "\n" + tail
            if len(next_chunk) > tail_budget and not tail:
                tail = chunk[-tail_budget:]
                tail_labels.append(chunk_label(idx, chunk))
                break
            if len(next_chunk) > tail_budget and tail:
                break
            tail = next_chunk
            tail_labels.append(chunk_label(idx, chunk))

        sections = [head.rstrip()]
        if tail and tail.strip() != head.strip():
            sections.extend(["", "... [narrowed diff slice: middle omitted] ...", "", tail.lstrip()])
        narrowed = "\n".join(section for section in sections if section is not None)
        return narrowed[:max_chars], {
            "strategy": "diff-head-tail-chunks",
            "head_chunks": head_labels,
            "tail_chunks": list(reversed(tail_labels)),
        }

    lines = text.splitlines()
    numbered = number_lines(text)
    if len(numbered) <= max_chars:
        return numbered, {"strategy": "numbered-lines"}

    head_budget = max_chars // 2
    tail_budget = max_chars - head_budget
    head = numbered[:head_budget].rstrip()
    tail = numbered[-tail_budget:].lstrip()
    narrowed = "\n".join(
        [
            head,
            "",
            "... [narrowed review slice: middle omitted] ...",
            "",
            tail,
        ]
    )
    return narrowed[:max_chars], {"strategy": "head-tail-numbered-lines"}


def write_narrowed_artifact(
    output_path: Path,
    source_path: Path,
    source_hash: str,
    stage: str,
    text: str,
    max_chars: int,
    repo_root: Path,
) -> tuple[Path, str]:
    narrowed_text, meta = narrow_text(stage, text, max_chars)
    narrowed_path = output_path.with_suffix(output_path.suffix + ".narrowed.txt")
    narrowed_meta_path = output_path.with_suffix(output_path.suffix + ".narrowed.json")
    narrowed_path.write_text(narrowed_text, encoding="utf-8")
    narrowed_meta = {
        "source_artifact_path": repo_relative_path(source_path, repo_root),
        "source_artifact_sha256": source_hash,
        "narrowed_artifact_path": repo_relative_path(narrowed_path, repo_root),
        "narrowed_artifact_sha256": sha256_text(narrowed_text),
        **meta,
    }
    narrowed_meta_path.write_text(json.dumps(narrowed_meta, indent=2), encoding="utf-8")
    return narrowed_path, narrowed_meta["narrowed_artifact_sha256"]


def prompt_for(stage: str, lens: str, artifact_label: str, artifact_text: str, extra_context: list[tuple[str, str]]) -> str:
    def safe_label(value: str) -> str:
        return value.replace("`", "'").replace("\r", " ").replace("\n", " ")

    has_context = len(extra_context) > 0
    context_instruction = (
        "Code context files are provided above. Before asserting any finding, check whether it is "
        "confirmed or refuted by the provided code. Each finding must include an evidence tag:\n"
        "  [CODE-CONFIRMED] — the code directly supports this finding\n"
        "  [CODE-REFUTED] — the code contradicts this finding (do not include as a finding)\n"
        "  [UNVERIFIED] — relevant code was not provided; treat as lower confidence\n"
        "Only assign HIGH severity to CODE-CONFIRMED findings."
        if has_context else
        "No code context files were provided. Flag any finding that depends on an assumption about "
        "the existing codebase as [UNVERIFIED] and limit it to MEDIUM severity or lower."
    )

    # Code-reviewing stages (diff, closeout) look at real code: specifically hunt
    # for swallowed errors and silent fallbacks. Artifact stages (spec/plan/tasks)
    # have no code to swallow, so this guidance is omitted there.
    fail_loud_instruction = (
        "This artifact contains code. Specifically hunt for swallowed errors and silent "
        "fallbacks: an except block that returns a default / None / empty / fake-success "
        "instead of re-raising, a subprocess or shell command whose return code or stderr "
        "is never checked, and any shim, stub, or fail-open path that lets a real failure "
        "read as success. Report each as a finding unless the code explicitly labels it as "
        "deliberate advisory fail-open."
        if stage in {"diff", "closeout"} else ""
    )

    parts = [
        f"Review this {stage} artifact using a {lens} lens.",
        "Stay scoped to that lens.",
        "Approach the artifact adversarially: look for hidden flaws, omitted cases, and weak assumptions before giving credit.",
        *([fail_loud_instruction] if fail_loud_instruction else []),
        context_instruction,
        "The full review artifact text is included below in this prompt.",
        "Return markdown using exactly these sections:",
        "## Findings",
        "## Residual Risks",
        "Keep the response concrete and ordered by severity.",
        *FINDINGS_JSON_CONTRACT_LINES,
        "",
    ]

    for label, text in extra_context:
        parts.append(f"Context: {safe_label(label)}")
        parts.append(text)
        parts.append("")

    parts.append(f"Artifact: {safe_label(artifact_label)}")
    parts.append(artifact_text)
    return "\n".join(parts)


def main() -> int:
    started_at = time.monotonic()
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--lens", required=True)
    parser.add_argument("--stage", required=True, choices=["spec", "plan", "tasks", "diff", "closeout"])
    parser.add_argument("--output", required=True)
    parser.add_argument("--artifact-label")
    parser.add_argument("--context", action="append", default=[])
    parser.add_argument("--model", default=os.environ.get("GEMINI_REVIEW_MODEL") or "gemini-3.1-flash-lite")
    parser.add_argument("--workspace-dir")
    parser.add_argument("--git-base-ref", default=os.environ.get("REVIEW_BASE_REF"))
    parser.add_argument("--timeout-seconds", type=int, default=90)
    # Raised per PR #789's analyzer report plus PR #791's perf fixes.
    # Operators were already overriding to these values routinely; they
    # can still override per-call when a review needs tighter limits.
    parser.add_argument(
        "--max-artifact-chars",
        type=int,
        default=100000,
        help="Maximum artifact chars to include before narrowing.",
    )
    parser.add_argument(
        "--max-context-chars",
        type=int,
        default=20000,
        help="Maximum chars to include from each context file before narrowing.",
    )
    parser.add_argument(
        "--max-total-chars",
        type=int,
        default=200000,
        help="Maximum total prompt chars allowed after narrowing.",
    )
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument(
        "--no-gemini-lock",
        action="store_true",
        default=False,
        help="Skip the Gemini concurrency lock. Used for staggered-parallel experiments where the "
        "caller manages launch timing instead of relying on the lock for serialization.",
    )
    args = parser.parse_args()

    try:
        workspace_root = resolve_workspace_root(args.workspace_dir)
        roots = allowed_roots(workspace_root)
        artifact_path = ensure_allowed_path(args.artifact, "artifact", roots, must_exist=True)
        output_path = ensure_allowed_path(args.output, "output", roots, must_exist=False)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    output_path.parent.mkdir(parents=True, exist_ok=True)

    source_artifact_text = normalized_artifact_text(args.stage, artifact_path)
    source_artifact_hash = sha256_text(source_artifact_text)
    artifact_label = args.artifact_label or artifact_path.name
    repo_info = resolve_repo_info(artifact_path, args.git_base_ref)
    repo_root = Path(repo_info["repo_root"]).resolve() if repo_info["repo_root"] else artifact_path.parents[0]

    metadata = {
        "reviewer": "gemini",
        "lens": args.lens,
        "stage": args.stage,
        "artifact_path": repo_relative_path(artifact_path, repo_root),
        "artifact_sha256": source_artifact_hash,
        "repo_root": ".",
        "git_head_sha": repo_info["git_head_sha"],
        "git_base_ref": repo_info["git_base_ref"],
        "git_base_sha": repo_info["git_base_sha"],
        "generation_method": "gemini-cli",
        "resolution_status": "open",
        "resolution_note": "",
        "raw_output_path": "",
        "narrowed_artifact_path": "",
        "narrowed_artifact_sha256": "",
        "coverage_status": "full",
        "coverage_note": "",
    }

    artifact_text = source_artifact_text
    # Change 3, PR #832: for diff reviews, expand new-file chunks so Gemini sees
    # full file content instead of `+`-prefixed lines without context.
    #
    # This is a PROMPT-PRESENTATION transform only: it shapes what the model
    # reads (`artifact_text`), NOT the recorded artifact hash. `artifact_sha256`
    # must stay the hash of the canonical on-disk patch (`source_artifact_hash`,
    # computed from normalized_artifact_text above), because review_is_healthy /
    # artifact_hash_matches re-hash that raw patch to decide staleness. Rehashing
    # the expanded text here made every slice whose diff adds a new file
    # permanently "stale" (sha256(expanded) never equals sha256(raw patch)), so
    # the diff checkpoint never advanced. Codex already records the raw-patch
    # hash; leaving artifact_sha256 untouched keeps Gemini consistent with it.
    if args.stage == "diff":
        artifact_text = expand_new_files_in_diff(artifact_text, repo_root, args.max_total_chars)

    if len(artifact_text) > args.max_artifact_chars:
        narrowed_path, narrowed_hash = write_narrowed_artifact(
            output_path,
            artifact_path,
            source_artifact_hash,
            args.stage,
            artifact_text,
            args.max_artifact_chars,
            repo_root,
        )
        metadata["narrowed_artifact_path"] = repo_relative_path(narrowed_path, repo_root)
        metadata["narrowed_artifact_sha256"] = narrowed_hash
        metadata["coverage_status"] = "partial"
        metadata["coverage_note"] = "artifact exceeded max_artifact_chars and was narrowed"
        artifact_text = read_text(narrowed_path)
        artifact_label = narrowed_path.name

    extra_context: list[tuple[str, str]] = []
    total_context_chars = 0
    for idx, raw in enumerate(args.context):
        try:
            ctx_path = ensure_allowed_path(raw, "context", roots, must_exist=True)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        text = read_text(ctx_path)
        if len(text) > args.max_context_chars:
            narrowed_path, _ = write_narrowed_artifact(
                output_path.with_name(output_path.stem + f".context{idx}"),
                ctx_path,
                sha256_text(text),
                args.stage,
                text,
                args.max_context_chars,
                repo_root,
            )
            text = read_text(narrowed_path)
            metadata["coverage_status"] = "partial"
            metadata["coverage_note"] = "context exceeded max_context_chars and was narrowed"
        total_context_chars += len(text)
        extra_context.append((ctx_path.name, text))

    def log_attempt(result_name: str, exit_code: int, error_summary: str = "") -> None:
        try:
            append_review_attempt(
                review_attempt_record(
                    repo_root=repo_root,
                    reviewer="gemini",
                    model=args.model,
                    stage=args.stage,
                    lens=args.lens,
                    artifact_chars=len(source_artifact_text),
                    context_chars=total_context_chars,
                    total_chars=len(source_artifact_text) + total_context_chars,
                    max_artifact_chars=args.max_artifact_chars,
                    max_context_chars=args.max_context_chars,
                    max_total_chars=args.max_total_chars,
                    coverage_status=metadata.get("coverage_status", ""),
                    coverage_note=metadata.get("coverage_note", ""),
                    result=result_name,
                    exit_code=exit_code,
                    duration_seconds=time.monotonic() - started_at,
                    artifact_sha256=source_artifact_hash,
                    review_path=output_path,
                    error_summary=error_summary,
                ),
                repo_root,
            )
        except Exception as exc:
            print(f"warning: failed to log review attempt metadata: {exc}", file=sys.stderr)

    if len(artifact_text) + total_context_chars > args.max_total_chars:
        write_failure(
            output_path,
            metadata,
            f"Combined prompt content still exceeds max_total_chars ({len(artifact_text) + total_context_chars} > {args.max_total_chars}) after narrowing.",
        )
        log_attempt("failed", 2, "combined prompt content exceeds max_total_chars")
        return 2

    prompt = prompt_for(args.stage, args.lens, artifact_label, artifact_text, extra_context)

    cmd = ["gemini", "-p", prompt, "--output-format", "json", "--sandbox"]
    if args.model:
        cmd.extend(["-m", args.model])

    workflow_slug = workflow_slug_from_paths(output_path, artifact_path) or artifact_path.parent.name
    workflow_round = workflow_round_from_paths(args.stage, output_path, artifact_path)

    run_cwd = workspace_root
    stdout_path = output_path.with_suffix(output_path.suffix + ".stdout.txt")
    stderr_path = output_path.with_suffix(output_path.suffix + ".stderr.txt")
    raw_path = output_path.with_suffix(output_path.suffix + ".json")

    last_failure: tuple[str, str] | None = None
    result = None
    lock_path = None
    owner_pid = None
    try:
        if not args.no_gemini_lock:
            lock_path, owner_pid = acquire_gemini_lock(workspace_root, args.timeout_seconds)
        for _ in range(args.retries + 1):
            def _call(cwd: str | None) -> subprocess.CompletedProcess:
                try:
                    return subprocess.run(
                        cmd,
                        check=False,
                        capture_output=True,
                        text=True,
                        cwd=cwd,
                        timeout=args.timeout_seconds,
                    )
                except subprocess.TimeoutExpired as exc:
                    return subprocess.CompletedProcess(
                        cmd,
                        124,
                        stdout=text_or_empty(exc.stdout),
                        stderr=text_or_empty(exc.stderr),
                    )

            if run_cwd is None:
                with tempfile.TemporaryDirectory() as tmpdir:
                    result = record_ai_call(
                        workflow_slug,
                        args.stage,
                        workflow_round,
                        "adversarial_review",
                        args.model,
                        lambda tmpdir=tmpdir: _call(tmpdir),
                        lens=args.lens,
                        prompt_chars=len(prompt),
                        prompt_cap=args.max_total_chars,
                    )
            else:
                result = record_ai_call(
                    workflow_slug,
                    args.stage,
                    workflow_round,
                    "adversarial_review",
                    args.model,
                    lambda: _call(str(run_cwd)),
                    lens=args.lens,
                    prompt_chars=len(prompt),
                    prompt_cap=args.max_total_chars,
                )
            if result.returncode == 0:
                break
            last_failure = (result.stdout or "", result.stderr or "")
            result = None
    except TimeoutError as exc:
        write_failure(
            output_path,
            metadata,
            str(exc),
        )
        log_attempt("timeout", 3, str(exc))
        return 3
    finally:
        if lock_path and owner_pid is not None:
            release_gemini_lock(lock_path, owner_pid)

    if result is None:
        stdout_path.write_text(text_or_empty(last_failure[0]) if last_failure else "", encoding="utf-8")
        stderr_path.write_text(text_or_empty(last_failure[1]) if last_failure else "", encoding="utf-8")
        write_failure(
            output_path,
            metadata,
            f"Gemini review failed after {args.retries + 1} attempt(s).",
            stdout_path,
            stderr_path,
        )
        log_attempt("failed", 3, f"Gemini review failed after {args.retries + 1} attempt(s)")
        return 3

    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")

    try:
        payload = extract_json(result.stdout)
    except Exception as exc:
        write_failure(
            output_path,
            metadata,
            f"Gemini output could not be parsed as JSON: {exc}",
            stdout_path,
            stderr_path,
        )
        log_attempt("malformed", 4, f"Gemini output could not be parsed as JSON: {exc}")
        return 4

    response = payload.get("response", "").strip()
    try:
        findings, residual = ensure_sections(response)
    except Exception as exc:
        write_failure(
            output_path,
            metadata,
            f"Gemini output did not match the required review format: {exc}",
            stdout_path,
            stderr_path,
        )
        log_attempt("malformed", 5, f"Gemini output did not match the required review format: {exc}")
        return 5
    stats = payload.get("stats", {})
    raw_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    metadata["raw_output_path"] = repo_relative_path(raw_path, repo_root)

    body = "\n".join(
        [
            f"# Review: {args.stage} {args.lens}",
            "",
            "## Findings",
            "",
            findings or "No findings returned.",
            "",
            "## Residual Risks",
            "",
            residual,
            "",
            "## Token Stats",
            "",
            format_stats(stats),
            "",
            "## Resolution",
            "- status: open",
            "- note:",
        ]
    )
    write_report(output_path, metadata, body)
    print(str(output_path))
    log_attempt("partial" if metadata.get("coverage_status") == "partial" else "passed", 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
