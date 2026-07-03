#!/usr/bin/env python3
import argparse
import hashlib
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[5]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from review_findings import classify_review_text
from workflow_utils import artifact_hash_matches, normalized_artifact_hash, resolve_stored_path


REQUIRED_KEYS = {
    "reviewer",
    "lens",
    "stage",
    "artifact_path",
    "artifact_sha256",
    "repo_root",
    "git_head_sha",
    "git_base_ref",
    "git_base_sha",
    "resolution_status",
    "resolution_note",
    "raw_output_path",
}

NONEMPTY_KEYS = {
    "reviewer",
    "lens",
    "stage",
    "artifact_path",
    "artifact_sha256",
    "repo_root",
    "git_head_sha",
    "git_base_ref",
    "git_base_sha",
}

REQUIRED_SECTIONS = {
    "# Review:",
    "## Findings",
    "## Residual Risks",
    "## Resolution",
}


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


def missing_sections(body: str) -> list[str]:
    return [section for section in REQUIRED_SECTIONS if section not in body]


def resolution_block_values(body: str) -> tuple[str, str] | None:
    if "## Resolution" not in body:
        return None
    block = body.split("## Resolution", 1)[1].strip().splitlines()
    status = ""
    note = ""
    for line in block:
        line = line.strip()
        if line.startswith("- status:"):
            status = line.split(":", 1)[1].strip()
        elif line.startswith("- note:"):
            note = line.split(":", 1)[1].strip()
        elif line.startswith("## "):
            break
    return status, note


def narrowed_meta_ok(review_path: Path, data: dict[str, str]) -> str | None:
    narrowed_path = data.get("narrowed_artifact_path", "")
    narrowed_sha = data.get("narrowed_artifact_sha256", "")
    if not narrowed_path and not narrowed_sha:
        return None
    if not narrowed_path or not narrowed_sha:
        return f"{review_path} has incomplete narrowed-artifact metadata"
    narrowed_file = resolve_stored_path(narrowed_path, REPO_ROOT, data.get("repo_root", ""))
    if not narrowed_file.exists():
        return f"{review_path} narrowed artifact is missing: {narrowed_path}"
    if hashlib.sha256(narrowed_file.read_bytes()).hexdigest() != narrowed_sha:
        return f"{review_path} narrowed artifact sha does not match metadata"
    narrowed_meta = review_path.with_suffix(review_path.suffix + ".narrowed.json")
    if not narrowed_meta.exists():
        return f"{review_path} is missing narrowed-artifact provenance: {narrowed_meta}"
    return None


def reviews_from_manifest(manifest_path: Path) -> tuple[Path, list[dict[str, str]]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact_path = resolve_stored_path(payload["artifact_path"], REPO_ROOT)
    reviews = payload.get("required_reviews", [])
    return artifact_path, reviews


def reviews_from_args(artifact_raw: str, review_paths: list[str]) -> tuple[Path, list[dict[str, str]]]:
    artifact_path = resolve_stored_path(artifact_raw, REPO_ROOT)
    reviews = [{"path": str(resolve_stored_path(raw, REPO_ROOT))} for raw in review_paths]
    return artifact_path, reviews


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact")
    parser.add_argument("--required-review", action="append", default=[])
    parser.add_argument("--checkpoint-manifest")
    args = parser.parse_args()

    if bool(args.artifact) == bool(args.checkpoint_manifest):
        raise SystemExit("Provide exactly one of --artifact or --checkpoint-manifest")

    if args.checkpoint_manifest:
        artifact_path, required_reviews = reviews_from_manifest(Path(args.checkpoint_manifest).resolve())
    else:
        artifact_path, required_reviews = reviews_from_args(args.artifact, args.required_review)

    current_hash = ""
    errors: list[str] = []

    for spec in required_reviews:
        review_path = resolve_stored_path(spec["path"], REPO_ROOT)
        if not review_path.exists():
            errors.append(f"missing review file: {review_path}")
            continue

        try:
            data, body = parse_frontmatter(review_path)
        except Exception as exc:
            errors.append(f"invalid review file {review_path}: {exc}")
            continue

        expected_reviewer = spec.get("reviewer")
        expected_lens = spec.get("lens")
        expected_stage = spec.get("stage")
        current_hash = current_hash or normalized_artifact_hash(expected_stage or data.get("stage", ""), artifact_path)

        missing_keys = sorted(REQUIRED_KEYS - set(data))
        if missing_keys:
            errors.append(f"{review_path} missing keys: {', '.join(missing_keys)}")

        empty_keys = sorted(key for key in NONEMPTY_KEYS if not data.get(key))
        if empty_keys:
            errors.append(f"{review_path} has empty required values: {', '.join(empty_keys)}")

        if expected_reviewer and data.get("reviewer") != expected_reviewer:
            errors.append(f"{review_path} reviewer mismatch: expected {expected_reviewer}, got {data.get('reviewer')}")
        if expected_lens and data.get("lens") != expected_lens:
            errors.append(f"{review_path} lens mismatch: expected {expected_lens}, got {data.get('lens')}")
        if expected_stage and data.get("stage") != expected_stage:
            errors.append(f"{review_path} stage mismatch: expected {expected_stage}, got {data.get('stage')}")

        if data.get("resolution_status") in {"failed", "insufficient"}:
            errors.append(f"{review_path} has unsatisfied status: {data.get('resolution_status')}")

        if not artifact_hash_matches(expected_stage or data.get("stage", ""), artifact_path, data):
            errors.append(f"{review_path} is stale for {artifact_path}")

        recorded_artifact_path = resolve_stored_path(data.get("artifact_path", ""), REPO_ROOT, data.get("repo_root", ""))
        if recorded_artifact_path != artifact_path:
            errors.append(f"{review_path} points to a different artifact: {data.get('artifact_path')}")

        missing = missing_sections(body)
        if missing:
            errors.append(f"{review_path} is missing required sections: {', '.join(missing)}")

        # Fail closed on unparseable findings: a review whose findings cannot be
        # read (malformed findings JSON block, or a non-trivial body with no
        # valid JSON block and no recognizable finding lines) must never satisfy
        # a checkpoint as if it were clean. Failed/deferred runner outputs have
        # trivial templated bodies, so they classify as legacy-clean and are
        # governed by their resolution_status instead.
        classification = classify_review_text(body)
        if classification.is_unparseable:
            errors.append(
                f"{review_path} findings are unparseable ({classification.detail}) — "
                "this review cannot be auto-accepted; re-run this review lens"
            )

        resolution_values = resolution_block_values(body)
        if resolution_values is None:
            errors.append(f"{review_path} is missing a parseable resolution block")
        else:
            status, note = resolution_values
            if status != data.get("resolution_status", ""):
                errors.append(
                    f'Review file {review_path} has body Resolution status="{status}" '
                    f'but frontmatter resolution_status="{data.get("resolution_status", "")}". '
                    "These got out of sync — re-run `reconcile` instead of editing the file by hand."
                )
            if note != data.get("resolution_note", ""):
                errors.append(
                    f'Review file {review_path} has body Resolution note="{note}" '
                    f'but frontmatter resolution_note="{data.get("resolution_note", "")}". '
                    "These got out of sync — re-run `reconcile` instead of editing the file by hand."
                )

        raw_output = data.get("raw_output_path")
        if data.get("reviewer") == "gemini":
            if not raw_output:
                errors.append(f"{review_path} is missing raw_output_path")
            elif not resolve_stored_path(raw_output, REPO_ROOT, data.get("repo_root", "")).exists():
                errors.append(f"{review_path} raw_output_path is missing: {raw_output}")
        elif data.get("reviewer") == "codex" and data.get("generation_method") not in {"codex-session", "codex-runner"}:
            errors.append(f"{review_path} has unsupported codex generation_method: {data.get('generation_method')}")

        narrowed_error = narrowed_meta_ok(review_path, data)
        if narrowed_error:
            errors.append(narrowed_error)

        if data.get("coverage_status") == "partial":
            errors.append(f"{review_path} is a partial review and cannot satisfy the checkpoint")

    if errors:
        for error in errors:
            print(error)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
