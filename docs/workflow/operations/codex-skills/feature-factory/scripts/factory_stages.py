#!/usr/bin/env python3
"""Stage health analysis, checkpoint marker parsing, and prerequisite checks.

Reads state and classifies stage health. No review execution — just analysis.
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_state import (  # noqa: E402
    REPO_ROOT,
    ARCH_DOCS_KEY,
    CHECKPOINT_PROGRESS_KEY,
    INIT_HEAD_SHA_KEY,
    read_json_file,
    workflow_dir,
    reviews_dir,
    checkpoint_manifest_path,
    default_artifact_path,
    load_workflow_state,
    load_checkpoint_manifest,
    load_scope_manifest,
    parse_review_frontmatter,
    is_ancestor_of_head,
)
from factory_io import read_text  # noqa: E402

REVIEW_SCRIPTS = REPO_ROOT / "docs" / "workflow" / "operations" / "codex-skills" / "review-lens" / "scripts"
if str(REVIEW_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(REVIEW_SCRIPTS))

from workflow_utils import normalized_artifact_hash, resolve_stored_path  # noqa: E402
from factory_git import _git_head_sha, merge_base_with_default_branch  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VERIFY_CHECKPOINT = REVIEW_SCRIPTS / "verify_review_checkpoint.py"
VERIFY_RECONCILIATION = REVIEW_SCRIPTS / "verify_reconciliation.py"

# Raised per PR #789's analyzer report plus PR #791's perf fixes.
# Operators can still override per-call on checkpoint commands.
HARD_DIFF_ARTIFACT_MAX_CHARS = 300000
LARGE_DIFF_RERUN_WARN_CHARS = 80000

NEXT_ACTION_LABELS: dict[str, str] = {
    "mark_blocked": "workflow is blocked — human intervention required",
    "discover": "complete discovery before writing spec",
    "author_spec": "write spec.md",
    "run_spec_checkpoint": "run spec checkpoint",
    "author_plan": "write plan.md",
    "run_plan_checkpoint": "run plan checkpoint",
    "author_tasks": "write tasks.md with [CHECKPOINT] markers",
    "record_parallel_analysis": "record parallel task analysis before checkpointing tasks",
    "run_tasks_checkpoint": "run tasks checkpoint",
    "dispatch_next_slice_to_codex": "implement next slice",
    "run_diff_checkpoint": "run diff checkpoint",
    "reconcile_reviews": "reconcile open review findings",
    "deliver": "create PR and watch CI",
    "closeout": "write closeout summary and run closeout checkpoint",
    "run_closeout_checkpoint": "run closeout checkpoint",
    "write_postmortem": "write postmortem.md",
    "update_status_md": "update STATUS.md",
    "reconcile_arch_docs": "update the scoped design/architecture docs under docs/ (or ack no change via `arch-docs --no-change-needed`)",
    "done": "workflow complete",
}

CHECKPOINT_STAGES = ["spec", "plan", "tasks", "diff", "closeout"]
VERIFY_ON_CLOSEOUT_STAGES = ["spec", "plan", "tasks", "diff"]
REQUIRED_PREDELIVERY_STAGES = ["spec", "plan", "tasks", "diff"]
STAGE_PREREQUISITES = {
    "plan": ["spec"],
    "tasks": ["plan"],
    "diff": ["tasks"],
}
STAGE_ARTIFACT_HEADINGS = {
    "spec": "# Spec",
    "plan": "# Plan",
    "tasks": "# Tasks",
}

# Matches [CHECKPOINT] as a marker on a structured line. Accepted forms:
#   - markdown heading:    "### [CHECKPOINT] Slice 1 — Foo"
#   - unordered list item: "- ... [CHECKPOINT]" / "* ... [CHECKPOINT]"
#   - ordered list item:   "1. ... [CHECKPOINT]"
#   - checkbox list item:  "- [ ] ... [CHECKPOINT]" / "- [x] ... [CHECKPOINT]"
# The marker may appear anywhere on the line (not just at the end), so a heading
# like "### [CHECKPOINT] Slice 1" matches. The line must START with a heading
# (#..######) or a list marker, so a bare "[CHECKPOINT]" mid-prose does not match.
_CHECKPOINT_MARKER_RE = re.compile(
    r"^\s*(?:#{1,6}\s+|[-*]\s+|\d+\.\s+|-\s+\[[ xX]\]\s+).*\[CHECKPOINT\].*$",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Checkpoint marker parsing
# ---------------------------------------------------------------------------


def parse_checkpoint_markers(slug: str) -> tuple[int, str]:
    """Return (count, markers_sha) for [CHECKPOINT] markers in tasks.md.

    markers_sha is sha256 of only the matched marker lines (normalised to LF,
    stripped of leading/trailing whitespace) — routine edits to non-marker
    lines do not change the hash.  Returns (0, '') if tasks.md is missing or
    has no markers.
    """
    tasks_path = workflow_dir(slug) / "tasks.md"
    if not tasks_path.exists():
        return 0, ""
    text = tasks_path.read_text(encoding="utf-8")
    matches = _CHECKPOINT_MARKER_RE.findall(text)
    if not matches:
        return 0, ""
    # Normalise: strip individual lines and join with LF to avoid CRLF drift.
    normalised = "\n".join(line.strip() for line in matches)
    sha = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
    return len(matches), sha


def checkpoint_progress_state(slug: str) -> dict:
    """Return checkpoint_progress from workflow state, filling missing keys with defaults."""
    state = load_workflow_state(slug)
    raw = state.get(CHECKPOINT_PROGRESS_KEY, {})
    return {
        "index": int(raw.get("index", 0)),
        "markers_sha": str(raw.get("markers_sha", "")),
        "last_diff_head_sha": str(raw.get("last_diff_head_sha", "")),
    }


def _default_checkpoint_progress() -> dict:
    return {"index": 0, "markers_sha": "", "last_diff_head_sha": ""}


# ---------------------------------------------------------------------------
# Artifact & manifest health
# ---------------------------------------------------------------------------


def artifact_has_meaningful_content(stage: str, path: Path) -> bool:
    if not path.exists():
        return False
    text = read_text(path).strip()
    heading = STAGE_ARTIFACT_HEADINGS.get(stage)
    if not text:
        return False
    if heading and text == heading:
        return False
    return True


def verify_checkpoint_manifest(manifest_path: Path) -> tuple[bool, str]:
    result = subprocess.run(
        [sys.executable, str(VERIFY_CHECKPOINT), "--checkpoint-manifest", str(manifest_path)],
        text=True,
        capture_output=True,
    )
    detail = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, detail


# ---------------------------------------------------------------------------
# Diff review budget
# ---------------------------------------------------------------------------


def diff_review_budget_state(slug: str) -> dict[str, object]:
    artifact_path = default_artifact_path(slug, "diff")
    state: dict[str, object] = {
        "artifact_path": artifact_path,
        "artifact_exists": artifact_path.exists(),
        "artifact_bytes": artifact_path.stat().st_size if artifact_path.exists() else 0,
        "large_artifact": artifact_path.exists() and artifact_path.stat().st_size >= LARGE_DIFF_RERUN_WARN_CHARS,
        "recorded_base_ref": "",
        "recorded_base_sha": "",
        "recorded_head_sha": "",
        "current_head_sha": _git_head_sha(REPO_ROOT) or "",
        "head_mismatch": False,
        "scope_basis": "branch-merge-base",
        "suggested_base_ref": "",
        "codex_review_path": None,
        "codex_review_present": False,
        "artifact_changed_since_codex": False,
    }
    meta_path = artifact_path.with_suffix(artifact_path.suffix + ".json")
    if meta_path.exists():
        try:
            meta = json.loads(read_text(meta_path))
            state["recorded_base_ref"] = meta.get("git_base_ref", "")
            state["recorded_base_sha"] = meta.get("git_base_sha", "")
            state["recorded_head_sha"] = meta.get("git_head_sha", "")
        except Exception:
            state["recorded_base_ref"] = ""
            state["recorded_base_sha"] = ""
            state["recorded_head_sha"] = ""
    recorded_head = str(state["recorded_head_sha"])
    recorded_base_sha = str(state["recorded_base_sha"])
    current_head = str(state["current_head_sha"])
    state["head_mismatch"] = bool(recorded_head and current_head and recorded_head != current_head)
    # Pick the base for the NEXT diff. Any base we reuse from a prior diff must
    # still be a real ancestor of HEAD: a mid-run rebase/amend can orphan the
    # last-reviewed head or leave the recorded base ref pointing at a stale
    # remote *feature* branch, which would silently sweep unrelated commits into
    # the reviewed diff. We never reuse the recorded symbolic ref (the drift
    # source) -- only validated SHAs.
    if state["head_mismatch"] and is_ancestor_of_head(recorded_head):
        state["scope_basis"] = "last-reviewed-head"
        state["suggested_base_ref"] = recorded_head
    elif recorded_base_sha and is_ancestor_of_head(recorded_base_sha):
        state["scope_basis"] = "recorded-base"
        state["suggested_base_ref"] = recorded_base_sha
    elif recorded_head or recorded_base_sha or state["recorded_base_ref"]:
        # A prior diff recorded a base, but it no longer resolves to a valid
        # ancestor of HEAD (the drift case above). Anchor to the merge-base with
        # the integration branch (origin/main) instead of reusing the drifted
        # ref. Fresh runs (no recorded base) skip this and leave the base empty
        # so write_canonical_diff resolves the first diff's base itself -- that
        # avoids a git call on every pre-diff status/deliver path.
        fork_point = merge_base_with_default_branch()
        if fork_point:
            state["scope_basis"] = "branch-merge-base"
            state["suggested_base_ref"] = fork_point

    manifest = load_checkpoint_manifest(slug, "diff")
    if not manifest:
        return state
    for review in manifest.get("required_reviews", []):
        if review.get("reviewer") != "codex":
            continue
        review_path = resolve_stored_path(review["path"], REPO_ROOT)
        state["codex_review_path"] = review_path
        state["codex_review_present"] = review_path.exists()
        if not review_path.exists() or not artifact_path.exists():
            return state
        try:
            data, _ = parse_review_frontmatter(review_path)
        except Exception:
            return state
        state["artifact_changed_since_codex"] = (
            data.get("artifact_sha256", "") != normalized_artifact_hash("diff", artifact_path)
        )
        return state
    return state


def preferred_diff_base_ref(slug: str, requested: str | None = None) -> str | None:
    if requested:
        return requested
    diff_budget = diff_review_budget_state(slug)
    suggested = str(diff_budget.get("suggested_base_ref", ""))
    if suggested:
        return suggested
    # Fresh run (no validated recorded base). Anchor the first diff explicitly to
    # the merge-base with the integration branch rather than returning None:
    # None lets write_canonical_diff resolve @{upstream} first, which a push or
    # rebase can repoint at a stale remote feature branch. Anchoring here also
    # records a stable base SHA (not a symbolic ref) for subsequent slices to
    # reuse. Only called at diff-generation time, so the git lookup is not on the
    # status/deliver hot paths. Falls back to None if no integration branch
    # resolves, preserving write_canonical_diff's own resolution as a last resort.
    return merge_base_with_default_branch()


# ---------------------------------------------------------------------------
# Stage manifest state & health classification
# ---------------------------------------------------------------------------


def stage_manifest_state(slug: str, stage: str) -> dict[str, object]:
    artifact_path = default_artifact_path(slug, stage)
    manifest_path = checkpoint_manifest_path(slug, stage)
    artifact_exists = artifact_path.exists()
    meaningful = artifact_has_meaningful_content(stage, artifact_path) if stage in STAGE_ARTIFACT_HEADINGS else artifact_exists
    state: dict[str, object] = {
        "artifact_path": artifact_path,
        "artifact_exists": artifact_exists,
        "artifact_meaningful": meaningful,
        "manifest_path": manifest_path,
        "manifest_exists": manifest_path.exists(),
        "healthy": False,
        "detail": "",
    }
    if manifest_path.exists():
        healthy, detail = verify_checkpoint_manifest(manifest_path)
        state["healthy"] = healthy
        state["detail"] = detail
    return state


def stage_review_inventory(slug: str, stage: str) -> tuple[list[Path], list[Path]]:
    manifest = load_checkpoint_manifest(slug, stage) or {}
    active_reviews: set[Path] = set()
    for review in manifest.get("required_reviews", []):
        active_reviews.add(resolve_stored_path(review["path"], REPO_ROOT).resolve())
    review_files = sorted(reviews_dir(slug).glob(f"{stage}.*.review.md"))
    orphaned = [path for path in review_files if path.resolve() not in active_reviews]
    return review_files, orphaned


def stage_drift_class(stage: str, state: dict[str, object]) -> str:
    if stage in STAGE_ARTIFACT_HEADINGS:
        if not state["artifact_exists"]:
            return "missing-artifact"
        if not state["artifact_meaningful"]:
            return "stub-artifact"
    if not state["manifest_exists"]:
        if stage == "diff" and not state["artifact_exists"]:
            return "not-started"
        if stage == "diff" and state["artifact_exists"]:
            return "missing-manifest"
        if stage in STAGE_ARTIFACT_HEADINGS and state["artifact_meaningful"]:
            return "missing-manifest"
        return "not-started"
    if state["healthy"]:
        return "healthy"
    if stage == "diff" and not state["artifact_exists"]:
        return "missing-artifact"
    return "unhealthy-manifest"


def stage_repairable(slug: str, stage: str, state: dict[str, object]) -> bool:
    if stage in STAGE_ARTIFACT_HEADINGS:
        return bool(state["artifact_meaningful"]) and (not state["manifest_exists"] or not state["healthy"])
    if stage == "diff":
        return bool(state["artifact_exists"]) and (
            not state["manifest_exists"]
            or not state["healthy"]
            or bool(diff_review_budget_state(slug).get("head_mismatch"))
        )
    return False


def stage_status_label(slug: str, stage: str, state: dict[str, object]) -> str:
    drift = stage_drift_class(stage, state)
    if stage == "diff" and stage_repairable(slug, stage, state):
        return "repairable"
    if drift == "healthy":
        return "healthy"
    if stage_repairable(slug, stage, state):
        return "repairable"
    if drift == "not-started":
        return "not-checkpointed"
    return drift


def later_progress_exists(stages: dict[str, dict[str, object]], current_stage: str) -> tuple[bool, str]:
    index = CHECKPOINT_STAGES.index(current_stage)
    for later_stage in CHECKPOINT_STAGES[index + 1 : CHECKPOINT_STAGES.index("diff") + 1]:
        later_state = stages[later_stage]
        # Fresh runs create heading-only spec/plan/tasks files up front. Those stubs should
        # not count as real later-stage progress when deciding the next action.
        if later_stage in STAGE_ARTIFACT_HEADINGS:
            if later_state["artifact_meaningful"] or later_state["manifest_exists"]:
                return True, later_stage
            continue
        if later_state["artifact_exists"] or later_state["manifest_exists"]:
            return True, later_stage
    return False, ""


# ---------------------------------------------------------------------------
# Reconciliation & prerequisites
# ---------------------------------------------------------------------------


def reconciliation_state(slug: str) -> tuple[bool, str]:
    reviews: list[Path] = []
    for manifest in sorted(reviews_dir(slug).glob("*.checkpoint.json")):
        payload, error = read_json_file(manifest)
        if error:
            return False, f"{manifest} is invalid: {error}"
        if not payload:
            continue
        if payload.get("stage") == "closeout":
            continue
        healthy, _ = verify_checkpoint_manifest(manifest)
        if not healthy:
            continue
        for review in payload.get("required_reviews", []):
            reviews.append(resolve_stored_path(review["path"], REPO_ROOT))
    if not reviews:
        return True, ""
    plan_path = workflow_dir(slug) / "plan.md"
    cmd = [sys.executable, str(VERIFY_RECONCILIATION), "--plan", str(plan_path), "--require-terminal"]
    for review in reviews:
        cmd.extend(["--review", str(review)])
    result = subprocess.run(cmd, text=True, capture_output=True)
    detail = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, detail


def prerequisite_failure(slug: str, stage: str) -> str | None:
    """Return a human-readable reason if *stage*'s prerequisites aren't ready."""
    for prereq in STAGE_PREREQUISITES.get(stage, []):
        prereq_state = stage_manifest_state(slug, prereq)
        if not prereq_state["manifest_exists"]:
            return f"{stage} checkpoint requires completed {prereq} checkpoint first"
        if not prereq_state["healthy"]:
            return f"{stage} checkpoint requires a healthy {prereq} checkpoint first"
    # Hard gate: the plan checkpoint requires the reuse audit (no-duplication
    # check) to have produced reuse-report.md. Fails open on runs with no init
    # SHA (test fixtures / pre-gate runs) so only real initialized runs enforce.
    if stage == "plan":
        init_sha = load_workflow_state(slug).get(INIT_HEAD_SHA_KEY, "")
        if init_sha and not reuse_report_meaningful(slug):
            return (
                "plan checkpoint requires the reuse audit first — write "
                f"{workflow_dir(slug).name}/reuse-report.md mapping the feature's "
                "needs to existing modules (reuse / extend / justified-new). "
                "See 'Architecture awareness' in SKILL.md."
            )
    return None


def status_md_changed_since_init(slug: str) -> bool:
    """Return True if STATUS.md was modified after workflow init.

    Uses git diff between the recorded init HEAD SHA and the current working
    tree. Returns True on any error so a missing or invalid SHA never blocks
    the 'done' state.
    """
    state = load_workflow_state(slug)
    init_sha = state.get(INIT_HEAD_SHA_KEY, "")
    if not init_sha:
        return True  # no init sha recorded — can't verify, don't block
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--quiet", init_sha, "--", "STATUS.md"],
        capture_output=True,
    )
    # returncode 0: no diff; 1: diff exists; 128: git error
    # Treat both "diff exists" and "error" as changed so workflow is never
    # blocked by a broken or missing SHA.
    return result.returncode != 0


def reuse_report_meaningful(slug: str) -> bool:
    """Return True if a non-trivial reuse-report.md exists for this run.

    The reuse audit (plan stage) must produce this before the plan checkpoint;
    a stub or empty file does not count.
    """
    path = workflow_dir(slug) / "reuse-report.md"
    if not path.exists():
        return False
    try:
        body = path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    # Strip a lone "# ..." heading the same way stub artifacts are detected.
    non_heading = "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    ).strip()
    return len(non_heading) >= 40


# Where the whole-system living docs live. Platform docs apply to every feature;
# a game's docs apply when the feature touches that game's module.
PLATFORM_DOCS_DIR = "docs/platform"
GAMES_DOCS_DIR = "docs/games"
# Fallback set used only when no docs can be resolved from scope (keeps the gate
# functioning even on an unusual layout).
SYSTEM_DOC_PATHS = (
    "docs/platform/AGENT_LUDUM_DESIGN.md",
    "docs/platform/AGENT_LUDUM_ARCHITECTURE.md",
    "docs/games/hoard-hurt-help/HOARD_HURT_HELP_DESIGN.md",
    "docs/games/hoard-hurt-help/HOARD_HURT_HELP_ARCHITECTURE.md",
)


def _game_slug_from_scope_path(path: str) -> str | None:
    """Return the game module slug if a scope path is under app/games/<slug>/."""
    parts = path.strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "app" and parts[1] == "games":
        return parts[2]
    return None


def _docs_in(rel_dir: str) -> list[str]:
    """Repo-relative .md paths in a docs folder (empty if the folder is absent)."""
    folder = REPO_ROOT / rel_dir
    if not folder.is_dir():
        return []
    return [str(md.relative_to(REPO_ROOT)) for md in sorted(folder.glob("*.md"))]


def scoped_doc_paths(slug: str) -> list[str]:
    """The design/architecture docs relevant to THIS feature's scope.

    Read from the feature's scope.json: any path under ``app/games/<game>/`` pulls
    in that game's docs (``docs/games/<game>/``, module underscores → hyphens);
    any other in-scope path pulls in the platform docs (``docs/platform/``). With
    no scope recorded, defaults to the platform docs. Falls back to the full
    SYSTEM_DOC_PATHS set only if nothing resolves on disk.
    """
    paths = load_scope_manifest(slug).get("paths", []) or []
    docs: list[str] = []
    touches_platform = not paths  # empty scope → treat as platform
    for p in paths:
        game = _game_slug_from_scope_path(p)
        if game:
            docs.extend(_docs_in(f"{GAMES_DOCS_DIR}/{game.replace('_', '-')}"))
        else:
            touches_platform = True
    if touches_platform:
        docs.extend(_docs_in(PLATFORM_DOCS_DIR))
    deduped = sorted(set(docs))
    return deduped or list(SYSTEM_DOC_PATHS)


def arch_docs_resolved(slug: str) -> bool:
    """Return True if the architecture-doc obligation is satisfied for this run.

    Satisfied when any design/architecture doc **in this feature's scope**
    (``scoped_doc_paths``) was modified since init, or the orchestrator explicitly
    acked "no architecture change needed" via the `arch-docs` command. Fails open
    (returns True) when no init SHA is recorded, so test fixtures and runs
    predating the gate are never blocked — mirrors status_md_changed_since_init.
    """
    state = load_workflow_state(slug)
    if state.get(ARCH_DOCS_KEY, {}).get("no_change_acked"):
        return True
    init_sha = state.get(INIT_HEAD_SHA_KEY, "")
    if not init_sha:
        return True  # can't verify — don't block
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--quiet", init_sha, "--", *scoped_doc_paths(slug)],
        capture_output=True,
    )
    # 0: no diff (docs untouched and not acked → not resolved); non-zero: diff or
    # git error → treat as changed so a broken SHA never hard-blocks the workflow.
    return result.returncode != 0


from factory_parallel import parse_p_annotation, parse_parallel_task_groups  # noqa: E402
