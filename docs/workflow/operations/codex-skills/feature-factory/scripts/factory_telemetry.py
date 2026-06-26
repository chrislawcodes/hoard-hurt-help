#!/usr/bin/env python3
"""Telemetry helpers for AI subprocess calls."""
from __future__ import annotations

import builtins
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_state import load_workflow_state, with_locked_state  # noqa: E402


VALID_ACTIVITY_TYPES = frozenset(
    {
        "authoring",
        "reconciliation",
        "adversarial_review",
        "judge_panel",
        "implementation",
        "implementation_review",
        "orchestration",
    }
)

_PRICING_PATH = Path(__file__).resolve().parents[1] / "pricing.json"
_PRICING_CACHE: dict[str, object] | None = None
# Legacy JSON format (Codex < 0.120). Kept for back-compat against any
# captures that pre-date the format change.
_CODEX_TOKEN_JSON_RE = re.compile(
    r'"totalTokens":\s*\{.*?"prompt":\s*(\d+).*?"candidates":\s*(\d+)',
    re.DOTALL,
)
# Current Codex (v0.124+): emits a single trailing block on stderr like:
#     tokens used
#     6,062
# We capture the total. Per PR #789 analysis, the CLI no longer exposes
# input/output split, so we record total in input_tokens and 0 in
# output_tokens. Cost estimation still works; the input/output ratio
# becomes unknowable from CLI output alone.
_CODEX_TOKEN_TOTAL_RE = re.compile(
    r"^\s*tokens used\s*\n\s*([\d,]+)\s*$",
    re.MULTILINE,
)
# Claude --output-format text doesn't emit JSON token counts on stderr.
# Claude --output-format json (which we should switch to for telemetry)
# emits a final JSON object with usage.input_tokens and usage.output_tokens.
# Match either the older `"input_tokens": N` literal or the `usage` block.
_CLAUDE_INPUT_RE = re.compile(r'"input_tokens":\s*(\d+)')
_CLAUDE_OUTPUT_RE = re.compile(r'"output_tokens":\s*(\d+)')


def _now_iso8601_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _stage_state_for_slug(slug: str, stage: str) -> dict[str, object]:
    state = load_workflow_state(slug)
    stages = state.get("stages", {})
    if not isinstance(stages, dict):
        return {}
    stage_state = stages.get(stage, {})
    return stage_state if isinstance(stage_state, dict) else {}


def _artifact_sha_at_time(slug: str, stage: str) -> str | None:
    stage_state = _stage_state_for_slug(slug, stage)
    history = stage_state.get("adversarial_sha_history", [])
    if isinstance(history, list):
        for item in reversed(history):
            if isinstance(item, str) and item.strip():
                return item
    initial_sha = stage_state.get("initial_sha", "")
    if isinstance(initial_sha, str) and initial_sha.strip():
        return initial_sha
    return None


def _resolve_agent_id() -> str | None:
    for env_name in ("AGENT_ID", "CODEx_AGENT_ID", "CLAUDE_AGENT_ID", "GEMINI_AGENT_ID"):
        value = os.environ.get(env_name)
        if value:
            return value
    return None


def _load_pricing() -> dict[str, object]:
    global _PRICING_CACHE
    if _PRICING_CACHE is None:
        _PRICING_CACHE = json.loads(_PRICING_PATH.read_text(encoding="utf-8"))
    return _PRICING_CACHE


def lookup_pricing(model: str) -> dict[str, object] | None:
    pricing = _load_pricing()
    entry = pricing.get(model)
    if not isinstance(entry, dict):
        return None
    return dict(entry)


def _estimate_cost_usd(input_tokens: int, output_tokens: int, pricing: dict[str, object]) -> float | None:
    try:
        input_price = Decimal(str(pricing["input_usd_per_1k_tokens"]))
        output_price = Decimal(str(pricing["output_usd_per_1k_tokens"]))
    except Exception:
        return None
    total = (
        (Decimal(input_tokens) / Decimal(1000)) * input_price
        + (Decimal(output_tokens) / Decimal(1000)) * output_price
    )
    return float(total)


def parse_tokens_codex(result: subprocess.CompletedProcess) -> Optional[dict]:
    """Extract token counts from Codex CLI stderr.

    Tries the legacy JSON format first (Codex < 0.120, kept for
    back-compat); falls back to the current `tokens used\\n<N>` trailing
    block (v0.124+).  The current CLI does not expose input/output split,
    so we record the total in `input_tokens` and 0 in `output_tokens`.
    Cost estimation still works (treats 0 output as $0 output cost), but
    callers that care about the ratio will see it as collapsed.
    """
    stderr = _text(getattr(result, "stderr", ""))
    json_matches = list(_CODEX_TOKEN_JSON_RE.finditer(stderr))
    if json_matches:
        prompt, candidates = json_matches[-1].groups()
        return {"input_tokens": int(prompt), "output_tokens": int(candidates)}
    total_matches = list(_CODEX_TOKEN_TOTAL_RE.finditer(stderr))
    if total_matches:
        # Strip thousands separators from `6,062` -> 6062.
        total_str = total_matches[-1].group(1).replace(",", "")
        return {"input_tokens": int(total_str), "output_tokens": 0}
    return None


def _find_gemini_token_payload(value: object) -> dict[str, object] | None:
    """Walk a parsed Gemini JSON payload looking for the token block.

    The Gemini CLI emits a stats JSON at end of stdout; the token block
    can live under several keys depending on version:
      - `tokens` (current — verified PR #790 against captured stdout)
      - `tokenStats` (older)
      - `totalTokens` (older)
    Walks recursively into dicts/lists. Returns the first match.
    """
    if isinstance(value, dict):
        for key in ("tokens", "tokenStats", "totalTokens"):
            candidate = value.get(key)
            if isinstance(candidate, dict):
                return candidate
        for nested in value.values():
            found = _find_gemini_token_payload(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_gemini_token_payload(item)
            if found is not None:
                return found
    return None


def parse_tokens_gemini(result: subprocess.CompletedProcess) -> Optional[dict]:
    """Extract token counts from Gemini CLI stdout.

    Gemini stdout is mixed: free-form prose followed by a JSON stats
    block at the end. Locate the first `{` that opens a balanced JSON
    object, attempt to parse from there. If that fails, scan for any
    embedded JSON object with `"tokens": {...}`.
    """
    stdout = _text(getattr(result, "stdout", ""))
    payload = None
    # Try parsing the whole stdout (works if stdout is pure JSON).
    try:
        payload = json.loads(stdout)
    except Exception:
        # Stdout has prose before the JSON block; find the last opening
        # brace at column 0 (or a JSON object that contains "tokens").
        for start in range(len(stdout)):
            if stdout[start] != "{":
                continue
            try:
                payload, _ = json.JSONDecoder().raw_decode(stdout, start)
                break
            except Exception:
                continue
    if payload is None:
        return None
    token_payload = _find_gemini_token_payload(payload)
    if not isinstance(token_payload, dict):
        return None
    # Current Gemini schema: tokens.input, tokens.candidates (output),
    # tokens.total. Earlier schemas used tokens.prompt for the same value
    # as input. Try multiple keys for robustness.
    prompt = token_payload.get("input")
    if not isinstance(prompt, int):
        prompt = token_payload.get("prompt")
    candidates = token_payload.get("candidates")
    if not isinstance(candidates, int):
        candidates = token_payload.get("output")
    if not isinstance(prompt, int) or not isinstance(candidates, int):
        return None
    return {"input_tokens": prompt, "output_tokens": candidates}


def parse_tokens_claude(result: subprocess.CompletedProcess) -> Optional[dict]:
    combined = _text(getattr(result, "stderr", "")) + "\n" + _text(getattr(result, "stdout", ""))
    input_match = _CLAUDE_INPUT_RE.search(combined)
    output_match = _CLAUDE_OUTPUT_RE.search(combined)
    if not input_match or not output_match:
        return None
    return {
        "input_tokens": int(input_match.group(1)),
        "output_tokens": int(output_match.group(1)),
    }


def _parser_for_model(model: str):
    if model.startswith("gpt-"):
        return parse_tokens_codex
    if model.startswith("claude-"):
        return parse_tokens_claude
    if model.startswith("gemini-"):
        return parse_tokens_gemini
    return None


def _parse_error(model: str) -> str:
    if model.startswith("gpt-"):
        return "no Codex token block found in stderr"
    if model.startswith("claude-"):
        return "no Claude token counts found in stdout or stderr"
    if model.startswith("gemini-"):
        return "no Gemini token stats found in stdout"
    return f"unsupported model prefix: {model}"


def tokens_from_session_jsonl(paths: list[str | Path]) -> dict[str, int]:
    """Sum Claude token usage across one or more session-transcript JSONL files.

    Reads the same usage fields the experiment bench uses (.claude/skills/experiment).
    ``input_tokens`` folds in cache-creation tokens (the billed-input convention);
    ``cache_read_tokens`` is reported separately because cache reads are ~10x cheaper
    and would otherwise balloon the headline figure. Used to attribute a Claude
    subagent review's cost on the subscription, where no per-call usage object exists.

    Raises FileNotFoundError / OSError if a path is unreadable and ValueError if a
    line is not valid JSON — callers decide whether that is advisory (telemetry) or
    fatal. It never silently returns zero for a missing file.
    """
    billed_input = cache_read = output = 0
    for raw in paths:
        path = Path(raw)
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                msg = json.loads(stripped)
                usage = (msg.get("message") or {}).get("usage") or {}
                billed_input += int(usage.get("input_tokens", 0) or 0)
                billed_input += int(usage.get("cache_creation_input_tokens", 0) or 0)
                cache_read += int(usage.get("cache_read_input_tokens", 0) or 0)
                output += int(usage.get("output_tokens", 0) or 0)
    return {
        "input_tokens": billed_input,
        "cache_read_tokens": cache_read,
        "output_tokens": output,
    }


def record_review_usage(
    slug: str,
    stage: str,
    round: int,
    activity_type: str,
    model: str,
    *,
    lens: Optional[str] = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    duration_seconds: float | None = None,
    prompt_chars: int | None = None,
    prompt_cap: int | None = None,
    parse_error: str | None = None,
) -> None:
    """Append a token_usage record whose tokens were measured out-of-band.

    Unlike record_ai_call (which runs a subprocess and parses its stdout/stderr),
    this records usage already extracted elsewhere — e.g. a Claude subagent review
    whose counts come from the session-transcript JSONL. The record uses the same
    shape as record_ai_call so analyze-reviews and closeout treat it identically;
    ``cache_read_tokens`` is an additive field the report generators ignore today.
    """
    if activity_type not in VALID_ACTIVITY_TYPES:
        raise ValueError(f"Unknown activity type: {activity_type}")

    now = _now_iso8601_utc()
    cost_usd_estimate = None
    if input_tokens is not None and output_tokens is not None:
        pricing = lookup_pricing(model)
        if pricing is not None:
            cost_usd_estimate = _estimate_cost_usd(int(input_tokens), int(output_tokens), pricing)

    record: dict[str, object] = {
        "stage": stage,
        "round": int(round),
        "activity_type": activity_type,
        "model": model,
        "lens": lens,
        "prompt_chars": prompt_chars,
        "prompt_cap": prompt_cap,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd_estimate": cost_usd_estimate,
        "timestamp": now,
        "started_at": now,
        "ended_at": now,
        "duration_seconds": duration_seconds,
        "agent_id": _resolve_agent_id(),
        "artifact_sha_at_time": _artifact_sha_at_time(slug, stage),
    }
    if cache_read_tokens is not None:
        record["cache_read_tokens"] = cache_read_tokens
    if parse_error is not None:
        record["parse_error"] = parse_error
    if input_tokens is not None and output_tokens is not None:
        if int(input_tokens) + int(output_tokens) < 2000:
            record["activity_subtype"] = "micro"

    with with_locked_state(slug) as state:
        usage = state.get("token_usage", [])
        if not isinstance(usage, list):
            usage = []
        usage = list(usage)
        usage.append(record)
        state["token_usage"] = usage


def record_ai_call(
    slug: str,
    stage: str,
    round: int,
    activity_type: str,
    model: str,
    callable_fn: Callable[[], subprocess.CompletedProcess],
    lens: Optional[str] = None,
    prompt_chars: int | None = None,
    prompt_cap: int | None = None,
) -> subprocess.CompletedProcess:
    """Record an AI subprocess call into state.token_usage.

    `lens` (e.g., 'feasibility-adversarial', 'completeness-judge') was
    added in PR #790: the analyzer (PR #789) showed 1186 of 1446 records
    had no unambiguous lens attribution. Callers SHOULD pass it; older
    callers that don't will record `lens: null` (back-compatible).

    `prompt_chars` and `prompt_cap` were added next so the analyzer can
    measure prompt-cap pressure directly from recorded calls. Callers
    that do not pass them record `null` for both fields.
    """
    if activity_type not in VALID_ACTIVITY_TYPES:
        raise ValueError(f"Unknown activity type: {activity_type}")

    started_at = _now_iso8601_utc()
    start_monotonic = time.perf_counter()
    result = callable_fn()
    ended_at = _now_iso8601_utc()
    duration_seconds = builtins.round(time.perf_counter() - start_monotonic, 6)

    parser = _parser_for_model(model)
    parsed = parser(result) if parser is not None else None
    pricing = lookup_pricing(model)
    parse_error = None
    cost_usd_estimate = None
    if parsed is None:
        parse_error = _parse_error(model)
    else:
        cost_usd_estimate = _estimate_cost_usd(
            int(parsed["input_tokens"]),
            int(parsed["output_tokens"]),
            pricing if pricing is not None else {},
        ) if pricing is not None else None

    record: dict[str, object] = {
        "stage": stage,
        "round": int(round),
        "activity_type": activity_type,
        "model": model,
        "lens": lens,
        "prompt_chars": prompt_chars,
        "prompt_cap": prompt_cap,
        "input_tokens": parsed["input_tokens"] if parsed else None,
        "output_tokens": parsed["output_tokens"] if parsed else None,
        "cost_usd_estimate": cost_usd_estimate,
        "timestamp": ended_at,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": duration_seconds,
        "agent_id": _resolve_agent_id(),
        "artifact_sha_at_time": _artifact_sha_at_time(slug, stage),
    }
    if parse_error is not None:
        record["parse_error"] = parse_error
    if parsed is not None:
        total_tokens = int(parsed["input_tokens"]) + int(parsed["output_tokens"])
        if total_tokens < 2000:
            record["activity_subtype"] = "micro"

    with with_locked_state(slug) as state:
        usage = state.get("token_usage", [])
        if not isinstance(usage, list):
            usage = []
        usage = list(usage)
        usage.append(record)
        state["token_usage"] = usage
    return result
