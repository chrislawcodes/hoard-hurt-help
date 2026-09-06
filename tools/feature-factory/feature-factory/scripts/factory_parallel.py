#!/usr/bin/env python3
"""Parallel task group parsing and [P:] annotation helpers.

Uses factory_stages module-level lookup for workflow_dir and REPO_ROOT
so that tests can patch factory_stages.workflow_dir and have it take effect.
"""
import re
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# Import the module object (not individual names) so attribute lookups happen
# at call time — this lets tests patch factory_stages.workflow_dir and REPO_ROOT.
import factory_stages as _stages  # noqa: E402


def parse_p_annotation(line: str) -> list[str]:
    match = re.search(r"\[P:\s*([^\]]*)\]", line)
    if not match:
        return []

    raw_paths = [part.strip() for part in match.group(1).split(",")]
    if not any(raw_paths):
        return []

    repo_root = _stages.REPO_ROOT.resolve()
    parsed_paths: list[str] = []
    seen: set[str] = set()

    for raw_path in raw_paths:
        if not raw_path:
            continue

        cleaned = raw_path
        while cleaned.startswith("./"):
            cleaned = cleaned[2:]
        while "//" in cleaned:
            cleaned = cleaned.replace("//", "/")

        if not cleaned:
            continue

        if cleaned.startswith("/"):
            print(f"warning: rejecting absolute path in [P:] annotation: {raw_path}", file=sys.stderr)
            continue

        try:
            resolved = (_stages.REPO_ROOT / cleaned).resolve()
            resolved.relative_to(repo_root)
        except Exception:
            print(f"warning: rejecting path outside repository in [P:] annotation: {raw_path}", file=sys.stderr)
            continue

        normalized = resolved.relative_to(repo_root).as_posix()
        if normalized in seen:
            continue
        seen.add(normalized)
        parsed_paths.append(normalized)

    return parsed_paths


# Path-like tokens in a task line: at least one directory segment plus a file
# extension, e.g. ``app/games/x/agents_lifecycle.py`` or ``tests/test_y.py``.
# Requiring a ``/`` keeps bare words from matching.
_PATH_TOKEN_RE = re.compile(r"(?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9_]+")


def _marker_visible_lines(tasks_path: Path) -> list[str]:
    """tasks.md lines with fenced code blocks blanked out.

    Slicing must agree with :func:`factory_stages.parse_checkpoint_markers`
    about which lines are markers, or slice indexes drift. Both therefore mask
    fenced code blocks the same way; a task line or marker quoted inside a
    fence is an example, not a boundary. Line count is preserved and every
    line outside a fence is byte-identical to the original.
    """
    text = tasks_path.read_text(encoding="utf-8")
    return _stages.mask_fenced_code_blocks(text).splitlines()


def _task_declared_paths(line: str) -> list[str]:
    """File paths a single task line names: [P:] annotations + path tokens."""
    declared: list[str] = []
    seen: set[str] = set()
    for path in parse_p_annotation(line):
        if path not in seen:
            seen.add(path)
            declared.append(path)
    for match in _PATH_TOKEN_RE.findall(line):
        cleaned = match
        while cleaned.startswith("./"):
            cleaned = cleaned[2:]
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            declared.append(cleaned)
    return declared


def slice_task_declared_files(slug: str, slice_index: int) -> list[tuple[str, list[str]]]:
    """Return ``(task_text, declared_paths)`` per task line in one slice.

    A "slice" is the block of task list items after the ``slice_index``-th
    ``[CHECKPOINT]`` marker (slice 0 is the block before the first marker).
    Collects task lines regardless of checkbox state (``[ ]`` or ``[x]``), so a
    *completed* slice's declared files can be read back. Paths come from
    ``[P:]`` annotations plus path-like tokens in the task text. Used by the
    per-task coverage report after a slice dispatch.
    """
    tasks_path = _stages.workflow_dir(slug) / "tasks.md"
    if not tasks_path.exists():
        return []

    tasks: list[tuple[str, list[str]]] = []
    markers_seen = 0
    collecting = slice_index == 0

    for line in _marker_visible_lines(tasks_path):
        if _stages._CHECKPOINT_MARKER_RE.match(line):
            if collecting:
                break
            markers_seen += 1
            if markers_seen == slice_index:
                collecting = True
            continue
        if not collecting:
            continue
        if not re.match(r"^\s*-\s+\[[ xX]\]\s+", line):
            continue
        tasks.append((line.strip(), _task_declared_paths(line)))

    return tasks


def slice_declared_files(slug: str, slice_index: int) -> list[str]:
    """Sorted, de-duplicated file paths declared by all tasks in one slice.

    Aggregate view over :func:`slice_task_declared_files` — see there for the
    slice/extraction semantics.
    """
    declared: set[str] = set()
    for _task, paths in slice_task_declared_files(slug, slice_index):
        declared.update(paths)
    return sorted(declared)


def prior_slice_unbuilt(slug: str, current_index: int) -> str | None:
    """Return an error message if the slice before ``current_index`` looks unbuilt.

    Guards against checkpoint-index drift (e.g. a `repair` that wrongly advanced
    the index past a slice that was never implemented). If the prior slice
    declares files and *none* of them exist on disk, the index has almost
    certainly skipped an un-built slice — refuse to dispatch. Fails open
    (returns None) when there is no prior slice or it declares no checkable
    files, so legitimate runs are never blocked.
    """
    if current_index <= 0:
        return None
    declared = slice_declared_files(slug, current_index - 1)
    if not declared:
        return None
    existing = [path for path in declared if (_stages.REPO_ROOT / path).exists()]
    if existing:
        return None
    return (
        f"prior slice (index {current_index - 1}) declares "
        f"{len(declared)} file(s) — {', '.join(declared[:5])}"
        f"{' …' if len(declared) > 5 else ''} — but none exist on disk. "
        f"checkpoint_progress.index ({current_index}) appears to have drifted "
        "past an un-built slice. Refusing to dispatch. Verify the prior slice "
        "was implemented, or correct checkpoint_progress.index in state.json."
    )


def parse_parallel_task_groups(slug: str) -> list[dict]:
    tasks_path = _stages.workflow_dir(slug) / "tasks.md"
    if not tasks_path.exists():
        return []

    # Determine which slice to read based on checkpoint progress.
    # Slice N starts after the N-th [CHECKPOINT] marker.  Slice 0 starts at the top.
    progress = _stages.checkpoint_progress_state(slug)
    target_slice = progress.get("index", 0)

    unchecked_tasks: list[dict[str, object]] = []
    markers_seen = 0
    collecting = target_slice == 0  # slice 0 starts immediately

    for line in _marker_visible_lines(tasks_path):
        if _stages._CHECKPOINT_MARKER_RE.match(line):
            if collecting:
                break  # end of current slice
            markers_seen += 1
            if markers_seen == target_slice:
                collecting = True
            continue
        if not collecting:
            continue
        if not re.match(r"^\s*-\s+\[\s\]\s+", line):
            continue

        unchecked_tasks.append(
            {
                "task": re.sub(r"\s*\[P(?::[^\]]*)?]", "", line).rstrip(),
                "files": parse_p_annotation(line),
            }
        )

    if not collecting or not unchecked_tasks:
        return []

    annotated_indexes = [index for index, item in enumerate(unchecked_tasks) if item["files"]]
    task_texts = [str(item["task"]) for item in unchecked_tasks]

    def serial_group(overlap_warning: str | None = None) -> list[dict]:
        return [
            {
                "tasks": task_texts,
                "parallel": False,
                "files": [],
                "overlap_warning": overlap_warning,
            }
        ]

    if len(annotated_indexes) < 2:
        return serial_group()

    file_to_indexes: dict[str, list[int]] = {}
    for index, item in enumerate(unchecked_tasks):
        for file_path in item["files"]:
            file_to_indexes.setdefault(str(file_path), []).append(index)

    overlap_warning = None
    for file_path, indexes in file_to_indexes.items():
        if len(indexes) > 1:
            first, second = indexes[0] + 1, indexes[1] + 1
            overlap_warning = f"tasks {first},{second} share file {file_path}"
            break

    if overlap_warning:
        return serial_group(overlap_warning=overlap_warning)

    annotated_tasks = [unchecked_tasks[index] for index in annotated_indexes]
    unannotated_tasks = [item for index, item in enumerate(unchecked_tasks) if index not in set(annotated_indexes)]

    parallel_files: list[str] = []
    seen_files: set[str] = set()
    for item in annotated_tasks:
        for file_path in item["files"]:
            file_name = str(file_path)
            if file_name in seen_files:
                continue
            seen_files.add(file_name)
            parallel_files.append(file_name)

    groups: list[dict] = [
        {
            "tasks": [str(item["task"]) for item in annotated_tasks],
            "parallel": True,
            "files": parallel_files,
            "overlap_warning": None,
        }
    ]

    if unannotated_tasks:
        groups.append(
            {
                "tasks": [str(item["task"]) for item in unannotated_tasks],
                "parallel": False,
                "files": [],
                "overlap_warning": None,
            }
        )

    return groups
