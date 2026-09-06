#!/usr/bin/env python3
"""prepare-claude-reviews: stage Claude subagent reviews for a checkpoint (spec 020).

The Claude-only path can't dispatch reviewers from a subprocess — they run as
orchestrator-spawned subagents on the subscription. This command does the
deterministic setup so the orchestrator skill can fan them out:

  1. Build the checkpoint manifest with Claude reviewers (same lenses as the
     default Gemini/Codex mix — only *who* reviews changes).
  2. Persist the reviewer choice in workflow state so the later `checkpoint`
     rebuilds a matching Claude manifest without needing FF_REVIEWER set.
  3. Emit one adversarial prompt per lens via run_claude_review --emit-prompt.
  4. Print a JSON plan: one entry per lens with its prompt, the response file the
     subagent should write, and the final review path.

After the subagents write their reviews (assembled with run_claude_review
--assemble), run the normal `checkpoint --stage <stage>`: the pre-assembled,
healthy Claude reviews pass repair/verify unchanged.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_state import (  # noqa: E402
    REPO_ROOT,
    atomic_json_write,
    checkpoint_manifest_path,
    default_artifact_path,
    load_workflow_state,
    normalized_repo_path,
    reviews_dir,
    save_scope_manifest,
    scope_manifest_path,
    update_workflow_state,
)
from factory_review import (  # noqa: E402
    checkpoint_manifest,
    required_reviews,
    _extract_file_paths_from_artifact,
    _AUTO_CONTEXT_MAX_FILES,
)
from factory_review_specs import (  # noqa: E402
    CLAUDE_REVIEWER,
    DIFF_REVIEW_DEFAULT_MIN_CHANGED_LINES,
    count_changed_diff_lines,
)
from factory_mutating import mutates_state  # noqa: E402

REVIEW_LENS_SCRIPTS = _SCRIPT_DIR.parents[1] / "review-lens" / "scripts"
RUN_CLAUDE_REVIEW = REVIEW_LENS_SCRIPTS / "run_claude_review.py"
WRITE_CANONICAL_DIFF = REVIEW_LENS_SCRIPTS / "write_canonical_diff.py"


def _ensure_diff_artifact(slug: str, artifact_path: Path, paths: list[str], base_ref: str | None) -> None:
    """Generate the canonical diff artifact for the diff stage if it is missing.

    The diff stage's artifact is normally produced inside `checkpoint`, but the
    Claude review dance needs it to exist *before* the checkpoint so reviewers can
    read it. Reuse the same write_canonical_diff the checkpoint uses, scoped by the
    given --path values (saved to scope.json) or an existing scope manifest. Writes
    the companion .json (HEAD/base) so a later `checkpoint --use-existing-artifact`
    sees a non-stale artifact. Fails loudly on an empty/failed diff.
    """
    if artifact_path.exists():
        return
    scope = save_scope_manifest(slug, paths) if paths else scope_manifest_path(slug)
    if not scope.exists():
        raise SystemExit(
            "diff prepare requires --path values (or an existing scope manifest) to "
            "scope the canonical diff"
        )
    cmd = [
        sys.executable,
        str(WRITE_CANONICAL_DIFF),
        "--repo",
        str(REPO_ROOT),
        "--output",
        str(artifact_path),
        "--path-manifest",
        str(scope),
    ]
    if base_ref:
        cmd.extend(["--base-ref", base_ref])
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "diff generation failed").strip()
        raise SystemExit(f"Diff generation failed:\n{detail}")


def _policy_from_state(slug: str) -> dict:
    policy = load_workflow_state(slug).get("review_policy")
    if not isinstance(policy, dict):
        policy = {}
    return {
        "sensitive": bool(policy.get("sensitive", False)),
        "large_structural": bool(policy.get("large_structural", False)),
        "performance_sensitive": bool(policy.get("performance_sensitive", False)),
        "extra_gemini_lenses": list(policy.get("extra_gemini_lenses", []) or []),
    }


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)


@mutates_state("prepare-claude-reviews")
def command_prepare_claude_reviews(args: argparse.Namespace) -> int:
    artifact_path = (
        Path(args.artifact).resolve() if args.artifact else default_artifact_path(args.slug, args.stage)
    )

    # The diff stage's artifact is generated here (it doesn't pre-exist like
    # spec/plan/tasks). Size it so the same size-gate the checkpoint uses decides
    # whether a review is warranted.
    diff_changed_lines: int | None = None
    if args.stage == "diff":
        _ensure_diff_artifact(args.slug, artifact_path, args.path, args.base_ref)
        diff_changed_lines = count_changed_diff_lines(artifact_path.read_text(encoding="utf-8"))

    if not artifact_path.exists():
        raise SystemExit(f"Artifact does not exist: {artifact_path}")

    diff_review_threshold = args.diff_review_threshold or DIFF_REVIEW_DEFAULT_MIN_CHANGED_LINES
    policy = _policy_from_state(args.slug)
    reviews = required_reviews(
        args.stage,
        policy["sensitive"],
        policy["large_structural"],
        policy["performance_sensitive"],
        policy["extra_gemini_lenses"],
        diff_changed_lines=diff_changed_lines,
        diff_review_threshold=diff_review_threshold,
        reviewer_override=CLAUDE_REVIEWER,
    )

    # Persist the reviewer choice so the later `checkpoint` builds a matching
    # Claude manifest without FF_REVIEWER in the environment.
    def _set_reviewer(state: dict) -> None:
        state.setdefault("review_policy", {})["reviewer"] = CLAUDE_REVIEWER

    update_workflow_state(args.slug, _set_reviewer)

    if not reviews:
        print(
            json.dumps(
                {"slug": args.slug, "stage": args.stage, "reviews": [], "note": "no default reviews for this stage"},
                indent=2,
            )
        )
        return 0

    # Context: explicit --context plus, for spec/tasks, files the artifact names
    # (parity with checkpoint's auto-context so reviewers verify against real code).
    context_paths = [normalized_repo_path(p, "context path") for p in args.context]
    if args.stage in {"spec", "tasks"}:
        for found in _extract_file_paths_from_artifact(artifact_path, REPO_ROOT):
            if len(context_paths) >= _AUTO_CONTEXT_MAX_FILES:
                break
            if found not in context_paths:
                context_paths.append(found)

    manifest = checkpoint_manifest(
        args.slug,
        args.stage,
        artifact_path,
        args.base_ref,
        context_paths,
        reviews,
        args.max_artifact_chars,
        args.max_context_chars,
        args.max_total_chars,
    )
    manifest_path = checkpoint_manifest_path(args.slug, args.stage)
    atomic_json_write(manifest_path, manifest)

    rev_dir = reviews_dir(args.slug)
    plan: list[dict[str, str]] = []
    for spec in manifest["required_reviews"]:
        lens = spec["lens"]
        output_abs = (REPO_ROOT / spec["path"]).resolve()
        prompt_path = rev_dir / f"{args.stage}.{CLAUDE_REVIEWER}.{lens}.prompt.md"
        response_path = rev_dir / f"{args.stage}.{CLAUDE_REVIEWER}.{lens}.response.md"
        cmd = [
            sys.executable,
            str(RUN_CLAUDE_REVIEW),
            "--mode",
            "emit-prompt",
            "--artifact",
            str(artifact_path),
            "--lens",
            lens,
            "--stage",
            args.stage,
            "--output",
            str(output_abs),
            "--prompt-out",
            str(prompt_path),
            "--workspace-dir",
            str(REPO_ROOT),
            "--max-artifact-chars",
            str(args.max_artifact_chars),
            "--max-context-chars",
            str(args.max_context_chars),
            "--max-total-chars",
            str(args.max_total_chars),
        ]
        if spec.get("model"):
            cmd.extend(["--model", spec["model"]])
        if args.base_ref:
            cmd.extend(["--git-base-ref", args.base_ref])
        for ctx in spec.get("context_paths", []):
            cmd.extend(["--context", str((REPO_ROOT / ctx).resolve())])
        result = subprocess.run(cmd, text=True, capture_output=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "emit-prompt failed").strip()
            raise SystemExit(f"Failed to emit prompt for {args.stage}.{lens}: {detail}")
        plan.append(
            {
                "stage": args.stage,
                "lens": lens,
                "reviewer": CLAUDE_REVIEWER,
                "model": spec.get("model", ""),
                "prompt_path": _rel(prompt_path),
                "response_path": _rel(response_path),
                "review_path": spec["path"],
            }
        )

    print(
        json.dumps(
            {
                "slug": args.slug,
                "stage": args.stage,
                "reviews": plan,
                "manifest_path": _rel(manifest_path),
            },
            indent=2,
        )
    )
    return 0
