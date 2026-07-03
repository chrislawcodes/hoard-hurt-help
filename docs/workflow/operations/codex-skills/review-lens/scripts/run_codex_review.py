#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from run_gemini_review import (
    allowed_roots,
    ensure_allowed_path,
    ensure_sections,
    format_stats,
    is_codex_quota_exhaustion,
    normalized_artifact_text,
    prompt_for,
    read_text,
    repo_relative_path,
    resolve_repo_info,
    resolve_workspace_root,
    sha256_text,
    workflow_round_from_paths,
    workflow_slug_from_paths,
    write_failure,
    write_narrowed_artifact,
    write_quota_deferred,
    write_report,
)

FEATURE_FACTORY_SCRIPTS = Path(__file__).resolve().parents[2] / "feature-factory" / "scripts"
if str(FEATURE_FACTORY_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(FEATURE_FACTORY_SCRIPTS))

from codex_stream_runner import run_with_idle_watchdog
from factory_telemetry import record_ai_call
from review_attempts import append_review_attempt, review_attempt_record


def main() -> int:
    started_at = time.monotonic()
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--lens", required=True)
    parser.add_argument("--stage", required=True, choices=["spec", "plan", "tasks", "diff", "closeout"])
    parser.add_argument("--output", required=True)
    parser.add_argument("--artifact-label")
    parser.add_argument("--context", action="append", default=[])
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--workspace-dir")
    parser.add_argument("--git-base-ref")
    # `--timeout-seconds` is now an absolute *backstop* ceiling, not the primary
    # control. The 120s ceiling set in PR #789 was wrong: review-attempts.jsonl
    # shows 46% of Codex reviews hit it, and 7 of 13 *successful* reviews
    # finished AFTER 120s (up to 425s) returning full, parseable verdicts. Plan
    # reviews legitimately run 114-425s (p50 ~297s). The real control is the
    # idle watchdog below: it streams Codex's output and kills the CLI only when
    # it goes *silent* (genuinely hung), so a slow-but-working review runs as
    # long as it needs while a hang fails fast in ~one idle window. The ceiling
    # only catches an "active but never converging" run. Operators can override
    # both per-call.
    parser.add_argument("--timeout-seconds", type=int, default=540)
    # Idle (inactivity) timeout: kill Codex if it emits no output for this long.
    # This is the primary liveness control -- see run_with_idle_watchdog.
    parser.add_argument("--idle-timeout-seconds", type=int, default=90)
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
        "reviewer": "codex",
        "lens": args.lens,
        "stage": args.stage,
        "artifact_path": repo_relative_path(artifact_path, repo_root),
        "artifact_sha256": source_artifact_hash,
        "repo_root": ".",
        "git_head_sha": repo_info["git_head_sha"],
        "git_base_ref": repo_info["git_base_ref"],
        "git_base_sha": repo_info["git_base_sha"],
        "generation_method": "codex-runner",
        "resolution_status": "open",
        "resolution_note": "",
        "raw_output_path": "",
        "narrowed_artifact_path": "",
        "narrowed_artifact_sha256": "",
        "coverage_status": "full",
        "coverage_note": "",
    }

    artifact_text = source_artifact_text
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
                    reviewer="codex",
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

    prompt = "\n".join(
        [
            prompt_for(args.stage, args.lens, artifact_label, artifact_text, extra_context),
            "",
            "Return only markdown with exactly these sections:",
            "## Findings",
            "## Residual Risks",
            "Do not include any other sections. After the Residual Risks section, end "
            "with the required fenced findings JSON block described above.",
        ]
    )

    workflow_slug = workflow_slug_from_paths(output_path, artifact_path) or artifact_path.parent.name
    workflow_round = workflow_round_from_paths(args.stage, output_path, artifact_path)

    raw_path = output_path.with_suffix(output_path.suffix + ".raw.txt")
    stdout_path = output_path.with_suffix(output_path.suffix + ".stdout.txt")
    stderr_path = output_path.with_suffix(output_path.suffix + ".stderr.txt")

    with tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8", suffix=".md", delete=False) as tmp:
        last_message_path = Path(tmp.name)

    cmd = [
        "codex",
        "exec",
        "-C",
        str(workspace_root),
        "--skip-git-repo-check",
        "--output-last-message",
        str(last_message_path),
        "--model",
        args.model,
        prompt,
    ]
    def _call() -> subprocess.CompletedProcess:
        # Stream Codex's output with an idle watchdog instead of a fixed
        # capture-and-wait. Returns 124 on an idle-kill or ceiling-kill so the
        # `result.returncode == 124` branch below still records a timeout.
        return run_with_idle_watchdog(
            cmd,
            hard_ceiling_seconds=args.timeout_seconds,
            idle_timeout_seconds=args.idle_timeout_seconds,
            label=f"codex {args.stage}/{args.lens}",
        )

    result = record_ai_call(
        workflow_slug,
        args.stage,
        workflow_round,
        "adversarial_review",
        args.model,
        _call,
        lens=args.lens,
        prompt_chars=len(prompt),
        prompt_cap=args.max_total_chars,
    )

    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    if result.returncode == 124:
        last_message_path.unlink(missing_ok=True)
        write_failure(
            output_path,
            metadata,
            "Codex review timed out.",
            stdout_path,
            stderr_path,
        )
        log_attempt("timeout", 3, "Codex review timed out")
        return 3
    if result.returncode != 0:
        last_message_path.unlink(missing_ok=True)
        # PR #751 / FF Housekeeping Slice 1: detect Codex quota / usage-limit
        # exhaustion in stderr/stdout and route to deferred (not failed) so the
        # checkpoint doesn't lock up on a quota issue. Plain `rate limit` is
        # not enough — see is_codex_quota_exhaustion for the precise rule.
        if is_codex_quota_exhaustion(result.stderr, result.stdout):
            write_quota_deferred(
                output_path,
                metadata,
                stdout_path,
                stderr_path,
            )
            log_attempt("deferred", 0, "Codex quota exhausted")
            return 0
        write_failure(
            output_path,
            metadata,
            "Codex review failed.",
            stdout_path,
            stderr_path,
        )
        log_attempt("failed", 4, "Codex review failed")
        return 4

    try:
        response = last_message_path.read_text(encoding="utf-8").strip()
        findings, residual = ensure_sections(response)
    except Exception as exc:
        last_message_path.unlink(missing_ok=True)
        write_failure(
            output_path,
            metadata,
            f"Codex output did not match the required review format: {exc}",
            stdout_path,
            stderr_path,
        )
        log_attempt("malformed", 5, f"Codex output did not match the required review format: {exc}")
        return 5

    raw_path.write_text(response, encoding="utf-8")
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
            "## Runner Stats",
            format_stats({}),
            "",
            "## Resolution",
            "- status: open",
            "- note: ",
        ]
    )
    write_report(output_path, metadata, body)
    last_message_path.unlink(missing_ok=True)
    print(str(output_path))
    log_attempt("partial" if metadata.get("coverage_status") == "partial" else "passed", 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
