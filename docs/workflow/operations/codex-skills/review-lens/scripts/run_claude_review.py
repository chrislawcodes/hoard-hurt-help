#!/usr/bin/env python3
"""Claude-only review worker (spec 020 — Claude-only Feature Factory).

This script does NOT call an LLM. In the Claude-only path, reviews run as
orchestrator-spawned subagents on the Claude subscription, so there is no CLI to
launch and no API key. This worker only does the two deterministic ends of a
review:

  --emit-prompt : build the exact adversarial prompt a reviewer should receive
                  (identical narrowing/wording to the Codex/Gemini runners) and
                  write it to a file for the orchestrator to hand a subagent.

  --assemble    : take the markdown a subagent returned and assemble it into a
                  checkpoint-compatible <stage>.<reviewer>.<lens>.review.md, then
                  record the review's token usage from the subagent's session
                  transcript JSONL (the subscription has no per-call usage object).

It reuses run_gemini_review's helpers so the assembled file is byte-compatible
with the Gemini/Codex review files and passes verify_review_checkpoint unchanged
(verify has no reviewer allowlist; a reviewer: "claude" file is accepted as long
as the keys, sections, and artifact hash are correct).
"""
import argparse
import sys
from pathlib import Path

from run_gemini_review import (
    allowed_roots,
    ensure_allowed_path,
    ensure_sections,
    format_stats,
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
    write_report,
)

FEATURE_FACTORY_SCRIPTS = Path(__file__).resolve().parents[2] / "feature-factory" / "scripts"
if str(FEATURE_FACTORY_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(FEATURE_FACTORY_SCRIPTS))

from factory_telemetry import record_review_usage, tokens_from_session_jsonl

REVIEWER = "claude"
GENERATION_METHOD = "claude-subagent"
DEFAULT_CLAUDE_MODEL = "claude-opus-4-8"
PROMPT_SUFFIX = (
    "",
    "Return only markdown with exactly these sections:",
    "## Findings",
    "## Residual Risks",
    "Do not include any other sections.",
)


def build_context(args: argparse.Namespace) -> dict:
    """Resolve paths, narrow oversized artifact/context, and return the review context.

    Mirrors run_codex_review's preparation so --emit-prompt and --assemble agree on
    the artifact text, coverage status, metadata, and the prompt the reviewer sees.
    """
    workspace_root = resolve_workspace_root(args.workspace_dir)
    roots = allowed_roots(workspace_root)
    artifact_path = ensure_allowed_path(args.artifact, "artifact", roots, must_exist=True)
    output_path = ensure_allowed_path(args.output, "output", roots, must_exist=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    source_artifact_text = normalized_artifact_text(args.stage, artifact_path)
    source_artifact_hash = sha256_text(source_artifact_text)
    artifact_label = args.artifact_label or artifact_path.name
    repo_info = resolve_repo_info(artifact_path, args.git_base_ref)
    repo_root = (
        Path(repo_info["repo_root"]).resolve()
        if repo_info["repo_root"]
        else artifact_path.parents[0]
    )

    metadata = {
        "reviewer": REVIEWER,
        "lens": args.lens,
        "stage": args.stage,
        "artifact_path": repo_relative_path(artifact_path, repo_root),
        "artifact_sha256": source_artifact_hash,
        "repo_root": ".",
        "git_head_sha": repo_info["git_head_sha"],
        "git_base_ref": repo_info["git_base_ref"],
        "git_base_sha": repo_info["git_base_sha"],
        "generation_method": GENERATION_METHOD,
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
        ctx_path = ensure_allowed_path(raw, "context", roots, must_exist=True)
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

    prompt = "\n".join(
        [prompt_for(args.stage, args.lens, artifact_label, artifact_text, extra_context), *PROMPT_SUFFIX]
    )
    return {
        "artifact_path": artifact_path,
        "output_path": output_path,
        "repo_root": repo_root,
        "metadata": metadata,
        "prompt": prompt,
        "total_chars": len(artifact_text) + total_context_chars,
    }


def _derive_output_tokens(totals: dict, subagent_total_tokens: int | None) -> int:
    """Best output-token count for a subagent review.

    A subagent's own transcript only carries a streaming-start usage snapshot, so
    its ``output_tokens`` is ~1 and not trustworthy. The orchestrator, however,
    sees the subagent's authoritative grand total (input + cache + output). When
    that total is provided we recover output as total - billed_input - cache_read;
    otherwise we fall back to the (under-counted) transcript value. ``max`` guards
    against a total that excludes some component.
    """
    jsonl_output = int(totals["output_tokens"])
    if subagent_total_tokens is None:
        return jsonl_output
    derived = subagent_total_tokens - int(totals["input_tokens"]) - int(totals["cache_read_tokens"])
    return max(jsonl_output, derived)


def _record_tokens(args: argparse.Namespace, ctx: dict) -> None:
    """Attribute the subagent review's token usage.

    Input and cache-read come from the subagent's session JSONL (accurate). Output
    is derived from the orchestrator-provided authoritative total when available,
    because the subagent transcript only records a streaming-start output snapshot.
    This is advisory: a missing/unreadable transcript records null tokens with a
    parse_error note and never blocks an otherwise-complete review.
    """
    slug = workflow_slug_from_paths(ctx["output_path"], ctx["artifact_path"]) or ctx[
        "artifact_path"
    ].parent.name
    round_no = workflow_round_from_paths(args.stage, ctx["output_path"], ctx["artifact_path"])
    try:
        if not args.session_jsonl:
            record_review_usage(
                slug,
                args.stage,
                round_no,
                "adversarial_review",
                args.model,
                lens=args.lens,
                total_tokens=args.subagent_total_tokens,
                prompt_chars=len(ctx["prompt"]),
                prompt_cap=args.max_total_chars,
                parse_error="no session JSONL provided for Claude subagent review",
            )
            return
        totals = tokens_from_session_jsonl(args.session_jsonl)
        record_review_usage(
            slug,
            args.stage,
            round_no,
            "adversarial_review",
            args.model,
            lens=args.lens,
            input_tokens=totals["input_tokens"],
            output_tokens=_derive_output_tokens(totals, args.subagent_total_tokens),
            cache_read_tokens=totals["cache_read_tokens"],
            total_tokens=args.subagent_total_tokens,
            prompt_chars=len(ctx["prompt"]),
            prompt_cap=args.max_total_chars,
        )
    except Exception as exc:
        # fail-open: telemetry is advisory and must never block a completed review.
        print(f"warning: failed to record Claude review telemetry: {exc}", file=sys.stderr)


def command_emit_prompt(args: argparse.Namespace, ctx: dict) -> int:
    if ctx["total_chars"] > args.max_total_chars:
        print(
            f"combined prompt content exceeds max_total_chars "
            f"({ctx['total_chars']} > {args.max_total_chars}); split the scope or raise the cap.",
            file=sys.stderr,
        )
        return 2
    prompt_out = Path(args.prompt_out)
    prompt_out.parent.mkdir(parents=True, exist_ok=True)
    prompt_out.write_text(ctx["prompt"], encoding="utf-8")
    print(str(prompt_out))
    return 0


def command_assemble(args: argparse.Namespace, ctx: dict) -> int:
    output_path = ctx["output_path"]
    metadata = ctx["metadata"]
    repo_root = ctx["repo_root"]

    if ctx["total_chars"] > args.max_total_chars:
        write_failure(
            output_path,
            metadata,
            f"Combined prompt content exceeds max_total_chars "
            f"({ctx['total_chars']} > {args.max_total_chars}).",
        )
        return 2

    response_path = Path(args.response_file)
    if not response_path.exists():
        raise SystemExit(f"response file does not exist: {response_path}")
    response = response_path.read_text(encoding="utf-8").strip()
    if not response:
        write_failure(output_path, metadata, "Claude subagent returned an empty review.")
        return 5

    try:
        findings, residual = ensure_sections(response)
    except Exception as exc:
        write_failure(
            output_path,
            metadata,
            f"Claude subagent output did not match the required review format: {exc}",
        )
        return 5

    raw_path = output_path.with_suffix(output_path.suffix + ".raw.txt")
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
    _record_tokens(args, ctx)
    print(str(output_path))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--mode", required=True, choices=["emit-prompt", "assemble"])
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--lens", required=True)
    parser.add_argument("--stage", required=True, choices=["spec", "plan", "tasks", "diff", "closeout"])
    parser.add_argument("--output", required=True, help="Path to the .review.md to assemble.")
    parser.add_argument("--artifact-label")
    parser.add_argument("--context", action="append", default=[])
    parser.add_argument("--model", default=DEFAULT_CLAUDE_MODEL)
    parser.add_argument("--workspace-dir")
    parser.add_argument("--git-base-ref")
    parser.add_argument("--max-artifact-chars", type=int, default=50000)
    parser.add_argument("--max-context-chars", type=int, default=60000)
    parser.add_argument("--max-total-chars", type=int, default=250000)
    # emit-prompt
    parser.add_argument("--prompt-out", help="Where to write the reviewer prompt (emit-prompt mode).")
    # assemble
    parser.add_argument("--response-file", help="Subagent's returned review markdown (assemble mode).")
    parser.add_argument(
        "--session-jsonl",
        action="append",
        default=[],
        help="Subagent session transcript JSONL for token attribution (assemble mode).",
    )
    parser.add_argument(
        "--subagent-total-tokens",
        type=int,
        default=None,
        help=(
            "Authoritative total tokens the orchestrator observed for the review "
            "subagent (assemble mode). Used to recover true output tokens, since the "
            "subagent transcript only records a streaming-start output snapshot."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "emit-prompt" and not args.prompt_out:
        raise SystemExit("--prompt-out is required for --mode emit-prompt")
    if args.mode == "assemble" and not args.response_file:
        raise SystemExit("--response-file is required for --mode assemble")

    try:
        ctx = build_context(args)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if args.mode == "emit-prompt":
        return command_emit_prompt(args, ctx)
    return command_assemble(args, ctx)


if __name__ == "__main__":
    raise SystemExit(main())
