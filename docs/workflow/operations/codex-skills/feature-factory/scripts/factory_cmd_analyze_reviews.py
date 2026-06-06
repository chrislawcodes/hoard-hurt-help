#!/usr/bin/env python3
"""Read-only telemetry analyzer for Feature Factory review and judge calls."""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import factory_state
from factory_mutating import readonly_command


_RELEVANT_ACTIVITY_TYPES = frozenset({"judge_panel", "review", "adversarial_review", "implementation_review"})
_REASONABLE_TIMESTAMP_MIN = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _default_output_path() -> Path:
    return (
        factory_state.REPO_ROOT
        / "docs"
        / "workflow"
        / "analysis"
        / f"review-performance-{_utc_today()}.md"
    )


def _resolve_output_path(raw: str | None) -> Path:
    if not raw:
        return _default_output_path()
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    return (factory_state.REPO_ROOT / candidate).resolve()


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


def _coerce_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            number = float(stripped)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        if stripped.endswith("Z"):
            return datetime.fromisoformat(stripped.replace("Z", "+00:00"))
        parsed = datetime.fromisoformat(stripped)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _normalize_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: max(limit - 3, 0)].rstrip()}..."


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (percentile / 100.0) * (len(ordered) - 1)
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return ordered[lower]
    fraction = rank - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * fraction)


def _format_duration(value: float) -> str:
    return f"{value:.1f}"


def _format_cost(value: float) -> str:
    return f"${value:,.2f}"


def _format_percent(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0.0%"
    return f"{(100.0 * numerator / denominator):.1f}%"


def _format_pct_of_cap(value: float) -> str:
    return f"{value:.0f}%"


def _escape_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _markdown_table(headers: list[str], rows: Iterable[Iterable[object]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_escape_cell(cell) for cell in row) + " |")
    return lines


def _is_relevant_activity(activity_type: object) -> bool:
    if not isinstance(activity_type, str):
        return False
    normalized = activity_type.strip()
    return normalized in _RELEVANT_ACTIVITY_TYPES or "review" in normalized


def _provider_key_from_model(model: str) -> str:
    lowered = model.lower()
    if lowered.startswith("gemini-"):
        return "gemini"
    if lowered.startswith("claude-"):
        return "claude"
    if lowered.startswith("gpt-") or lowered.startswith("gpt"):
        return "codex"
    return lowered


def _scan_review_metadata(
    slug_dir: Path,
    state_blob: dict[str, object],
    data_quality: dict[str, object],
) -> tuple[set[tuple[str, int, str]], dict[tuple[str, str, str], list[str]], int]:
    deferred_keys: set[tuple[str, int, str]] = set()
    lens_candidates: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    judge_cap_count = 0

    stages = state_blob.get("stages", {})
    if isinstance(stages, dict):
        for stage_name, stage_state in stages.items():
            if not isinstance(stage_state, dict):
                continue
            judge_rounds = _coerce_int(stage_state.get("judge_rounds")) or 0
            if judge_rounds >= 3:
                judge_cap_count += 1

    reviews_dir = slug_dir / "reviews"
    if not reviews_dir.exists():
        return deferred_keys, lens_candidates, judge_cap_count

    deferred_inference_failures = _coerce_int(data_quality.get("deferred_inference_failures")) or 0
    malformed_review_files = _coerce_int(data_quality.get("malformed_review_files")) or 0

    for review_path in sorted(reviews_dir.glob("*.review.md")):
        try:
            metadata, _ = factory_state.parse_review_frontmatter(review_path)
        except Exception:
            malformed_review_files += 1
            continue

        stage = str(metadata.get("stage", "") or "").strip()
        reviewer = str(metadata.get("reviewer", "") or "").strip().lower()
        lens = str(metadata.get("lens", "") or "").strip()
        if not stage or not reviewer or not lens:
            continue

        activity_type = "judge_panel" if lens.endswith("-judge") or review_path.name.startswith("judge.") else "adversarial_review"
        if activity_type == "judge_panel":
            provider_key = reviewer
        else:
            provider_key = reviewer
            if reviewer.startswith("gpt-") or reviewer.startswith("gpt"):
                provider_key = "codex"
            elif reviewer.startswith("claude-"):
                provider_key = "claude"
            elif reviewer.startswith("gemini-"):
                provider_key = "gemini"

        key = (stage, activity_type, provider_key)
        if lens not in lens_candidates[key]:
            lens_candidates[key].append(lens)

        if str(metadata.get("resolution_status", "") or "").strip() != "deferred":
            continue

        stage_state = stages.get(stage, {}) if isinstance(stages, dict) else {}
        if not isinstance(stage_state, dict):
            deferred_inference_failures += 1
            continue
        round_field = "judge_rounds" if activity_type == "judge_panel" else "adversarial_rounds"
        inferred_round = _coerce_int(stage_state.get(round_field))
        if inferred_round is None or inferred_round <= 0:
            deferred_inference_failures += 1
            continue
        deferred_keys.add((stage, inferred_round, activity_type))

    data_quality["deferred_inference_failures"] = deferred_inference_failures
    data_quality["malformed_review_files"] = malformed_review_files
    return deferred_keys, lens_candidates, judge_cap_count


def _load_records(top_n: int) -> tuple[list[dict[str, object]], dict[str, object]]:
    del top_n  # caller owns output shaping; keep signature stable for tests
    records: list[dict[str, object]] = []
    data_quality: dict[str, object] = {
        "dropped_fields": Counter(),
        "partial_fields": Counter(),
        "skipped_slugs": [],
        "token_usage_missing": [],
        "bad_timestamps": 0,
        "records_seen": 0,
        "dropped_records": 0,
        "deferred_inference_failures": 0,
        "malformed_review_files": 0,
        "unattributed_lens_records": 0,
    }

    runs_root = factory_state.FACTORY_RUNS_ROOT
    for state_path in sorted(runs_root.glob("*/state.json")):
        slug = state_path.parent.name
        try:
            raw_state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            _warn(f"skipping malformed state.json for {slug}: {exc}")
            cast_skipped = data_quality["skipped_slugs"]
            assert isinstance(cast_skipped, list)
            cast_skipped.append(slug)
            continue

        token_usage = raw_state.get("token_usage")
        if not isinstance(token_usage, list):
            _warn(f"skipping {slug}: token_usage missing or not a list")
            cast_missing = data_quality["token_usage_missing"]
            assert isinstance(cast_missing, list)
            cast_missing.append(slug)
            continue

        deferred_keys, lens_candidates, judge_cap_count = _scan_review_metadata(state_path.parent, raw_state, data_quality)

        for raw_record in token_usage:
            data_quality["records_seen"] = int(data_quality["records_seen"]) + 1
            if not isinstance(raw_record, dict):
                cast_dropped = data_quality["dropped_fields"]
                assert isinstance(cast_dropped, Counter)
                cast_dropped["record_not_object"] += 1
                data_quality["dropped_records"] = int(data_quality["dropped_records"]) + 1
                continue

            activity_type = raw_record.get("activity_type")
            if not _is_relevant_activity(activity_type):
                continue

            model = raw_record.get("model")
            duration_seconds = _coerce_float(raw_record.get("duration_seconds"))
            if not isinstance(model, str) or not model.strip():
                cast_dropped = data_quality["dropped_fields"]
                assert isinstance(cast_dropped, Counter)
                cast_dropped["model"] += 1
                data_quality["dropped_records"] = int(data_quality["dropped_records"]) + 1
                continue
            if duration_seconds is None:
                cast_dropped = data_quality["dropped_fields"]
                assert isinstance(cast_dropped, Counter)
                cast_dropped["duration_seconds"] += 1
                data_quality["dropped_records"] = int(data_quality["dropped_records"]) + 1
                continue

            stage = str(raw_record.get("stage", "") or "").strip() or "unknown"
            round_number = _coerce_int(raw_record.get("round"))
            timestamp_text = str(raw_record.get("timestamp", "") or "").strip()
            timestamp = _parse_timestamp(timestamp_text)
            if timestamp is None and timestamp_text:
                cast_partial = data_quality["partial_fields"]
                assert isinstance(cast_partial, Counter)
                cast_partial["timestamp_unparseable"] += 1
            if timestamp is not None and (
                timestamp < _REASONABLE_TIMESTAMP_MIN
                or timestamp > datetime.now(timezone.utc) + timedelta(days=1)
            ):
                data_quality["bad_timestamps"] = int(data_quality["bad_timestamps"]) + 1

            input_tokens = _coerce_int(raw_record.get("input_tokens"))
            output_tokens = _coerce_int(raw_record.get("output_tokens"))
            prompt_chars = _coerce_int(raw_record.get("prompt_chars"))
            prompt_cap = _coerce_int(raw_record.get("prompt_cap"))
            cost_usd = _coerce_float(raw_record.get("cost_usd_estimate")) or 0.0
            parse_error = _normalize_text(raw_record.get("parse_error"))

            for optional_field, value in (
                ("round", round_number),
                ("timestamp", timestamp_text or None),
                ("input_tokens", input_tokens),
                ("output_tokens", output_tokens),
            ):
                if value is None:
                    cast_partial = data_quality["partial_fields"]
                    assert isinstance(cast_partial, Counter)
                    cast_partial[optional_field] += 1

            activity_name = str(activity_type).strip()
            provider_key = model.strip().lower() if activity_name == "judge_panel" else _provider_key_from_model(model.strip())
            candidate_lenses = lens_candidates.get((stage, activity_name, provider_key), [])
            lens = candidate_lenses[0] if len(candidate_lenses) == 1 else ""
            if not lens:
                data_quality["unattributed_lens_records"] = int(data_quality["unattributed_lens_records"]) + 1

            record = {
                "slug": slug,
                "stage": stage,
                "round": round_number,
                "activity_type": activity_name,
                "model": model.strip(),
                "duration_seconds": duration_seconds,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd_estimate": cost_usd,
                "timestamp_text": timestamp_text,
                "timestamp": timestamp,
                "parse_error": parse_error,
                "prompt_chars": prompt_chars,
                "prompt_cap": prompt_cap,
                "judge_cap_count": judge_cap_count,
                "is_deferred_round": bool(round_number is not None and (stage, round_number, activity_name) in deferred_keys),
                "lens": lens,
            }
            records.append(record)

    return records, data_quality


def _summarize_grouped(records: list[dict[str, object]], group_keys: tuple[str, ...]) -> list[list[object]]:
    grouped: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for record in records:
        grouped[tuple(record.get(key) for key in group_keys)].append(record)

    rows: list[list[object]] = []
    for key, items in grouped.items():
        durations = [float(item["duration_seconds"]) for item in items]
        parse_error_count = sum(1 for item in items if item.get("parse_error"))
        row = list(key)
        row.extend(
            [
                len(items),
                _format_duration(sum(durations)),
                _format_duration(_percentile(durations, 50)),
                _format_duration(_percentile(durations, 95)),
                _format_duration(_percentile(durations, 99)),
                _format_duration(max(durations)),
                parse_error_count,
                _format_percent(parse_error_count, len(items)),
            ]
        )
        rows.append(row)

    rows.sort(key=lambda row: float(str(row[len(group_keys) + 1])), reverse=True)
    return rows


def _top_slowest_rows(records: list[dict[str, object]], top_n: int) -> list[list[object]]:
    ordered = sorted(records, key=lambda item: float(item["duration_seconds"]), reverse=True)[:top_n]
    rows: list[list[object]] = []
    for record in ordered:
        rows.append(
            [
                record["slug"],
                record["stage"],
                record.get("round", ""),
                record["activity_type"],
                record["model"],
                _format_duration(float(record["duration_seconds"])),
                record.get("input_tokens", ""),
                record.get("output_tokens", ""),
                _truncate(str(record.get("parse_error", "") or ""), 80),
                record.get("timestamp_text", ""),
            ]
        )
    return rows


def _parse_error_rows(records: list[dict[str, object]]) -> list[list[object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        normalized = _truncate(_normalize_text(record.get("parse_error")), 80)
        if normalized:
            grouped[normalized].append(record)

    rows: list[list[object]] = []
    for pattern, items in grouped.items():
        models = sorted({str(item["model"]) for item in items})
        model_list = ", ".join(models[:4])
        if len(models) > 4:
            model_list = f"{model_list}, +{len(models) - 4} more"
        rows.append([pattern, len(items), model_list, items[0]["slug"]])
    rows.sort(key=lambda row: int(row[1]), reverse=True)
    return rows


def _correlation_rows(records: list[dict[str, object]]) -> list[list[object]]:
    by_model: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        if record.get("input_tokens") is None:
            continue
        by_model[str(record["model"])].append(record)

    rows: list[list[object]] = []
    for model in sorted(by_model):
        ordered = sorted(by_model[model], key=lambda item: int(item["input_tokens"]))  # type: ignore[arg-type]
        if not ordered:
            continue
        count = len(ordered)
        emitted_index = 0
        for index in range(5):
            start = (index * count) // 5
            end = ((index + 1) * count) // 5
            if end <= start:
                continue
            emitted_index += 1
            bucket = ordered[start:end]
            durations = [float(item["duration_seconds"]) for item in bucket]
            min_tokens = int(bucket[0]["input_tokens"])  # type: ignore[arg-type]
            max_tokens = int(bucket[-1]["input_tokens"])  # type: ignore[arg-type]
            rows.append(
                [
                    model,
                    f"q{emitted_index} [{min_tokens}-{max_tokens}]",
                    len(bucket),
                    _format_duration(_percentile(durations, 50)),
                    _format_duration(_percentile(durations, 95)),
                ]
            )
    return rows


def _slug_rows(records: list[dict[str, object]]) -> list[list[object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        grouped[str(record["slug"])].append(record)

    rows: list[list[object]] = []
    for slug, items in grouped.items():
        review_duration = sum(
            float(item["duration_seconds"])
            for item in items
            if str(item["activity_type"]) != "judge_panel"
        )
        judge_duration = sum(
            float(item["duration_seconds"])
            for item in items
            if str(item["activity_type"]) == "judge_panel"
        )
        deferred_duration = sum(
            float(item["duration_seconds"])
            for item in items
            if bool(item.get("is_deferred_round"))
        )
        rounds = {
            (str(item["stage"]), int(item["round"]))
            for item in items
            if item.get("round") is not None
        }
        judge_caps = max(int(item.get("judge_cap_count", 0) or 0) for item in items)
        rows.append(
            [
                slug,
                _format_duration(review_duration),
                _format_duration(judge_duration),
                _format_duration(deferred_duration),
                len(rounds),
                judge_caps,
            ]
        )
    rows.sort(key=lambda row: float(str(row[1])) + float(str(row[2])), reverse=True)
    return rows[:20]


def _artifact_size_records() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    stage_files = {
        "spec": "spec.md",
        "plan": "plan.md",
        "tasks": "tasks.md",
        "diff": "reviews/implementation.diff.patch",
    }
    runs_root = factory_state.FACTORY_RUNS_ROOT
    if not runs_root.exists():
        return records
    for slug_dir in sorted(path for path in runs_root.iterdir() if path.is_dir()):
        for stage, relative_path in stage_files.items():
            artifact_path = slug_dir / relative_path
            if not artifact_path.exists():
                continue
            records.append(
                {
                    "slug": slug_dir.name,
                    "stage": stage,
                    "char_count": len(artifact_path.read_text(encoding="utf-8")),
                }
            )
    return records


def _artifact_distribution_rows(artifact_records: list[dict[str, object]]) -> list[list[object]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for record in artifact_records:
        grouped[str(record["stage"])].append(int(record["char_count"]))

    rows: list[list[object]] = []
    for stage in ("spec", "plan", "tasks", "diff"):
        counts = grouped.get(stage, [])
        if not counts:
            continue
        rows.append(
            [
                stage,
                len(counts),
                int(round(_percentile([float(value) for value in counts], 50))),
                int(round(_percentile([float(value) for value in counts], 95))),
                max(counts),
                sum(1 for value in counts if value > 50000),
                sum(1 for value in counts if value > 100000),
            ]
        )
    return rows


def _largest_artifact_rows(artifact_records: list[dict[str, object]]) -> list[list[object]]:
    ordered = sorted(
        artifact_records,
        key=lambda item: int(item["char_count"]),
        reverse=True,
    )[:10]
    return [[item["slug"], item["stage"], item["char_count"]] for item in ordered]


def _per_feature_rows(top_n: int = 20) -> list[list[object]]:
    """Return rows for the per-feature metrics rollup section."""
    rows: list[list[object]] = []
    runs_root = factory_state.FACTORY_RUNS_ROOT
    if not runs_root.exists():
        return rows

    for state_path in sorted(runs_root.glob("*/state.json")):
        slug = state_path.parent.name
        try:
            raw_state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        command_telemetry = raw_state.get("command_telemetry", [])
        if not isinstance(command_telemetry, list):
            command_telemetry = []

        token_usage = raw_state.get("token_usage", [])
        if not isinstance(token_usage, list):
            token_usage = []

        total_wall_seconds = 0.0
        ttl_crossings = 0
        command_count = 0
        for entry in command_telemetry:
            if not isinstance(entry, dict):
                continue
            command_count += 1
            ws = entry.get("wall_seconds")
            if isinstance(ws, (int, float)) and not isinstance(ws, bool):
                total_wall_seconds += float(ws)
            if entry.get("ttl_crossed") is True:
                ttl_crossings += 1

        codex_tokens = 0
        gemini_tokens = 0
        for entry in token_usage:
            if not isinstance(entry, dict):
                continue
            model = entry.get("model", "")
            if not isinstance(model, str):
                continue
            model_lower = model.lower()
            in_tok = _coerce_int(entry.get("input_tokens")) or 0
            out_tok = _coerce_int(entry.get("output_tokens")) or 0
            total = in_tok + out_tok
            if model_lower.startswith("gpt-"):
                codex_tokens += total
            elif model_lower.startswith("gemini-"):
                gemini_tokens += total

        rows.append([
            slug,
            round(total_wall_seconds, 1),
            codex_tokens,
            gemini_tokens,
            ttl_crossings,
            command_count,
        ])

    rows.sort(key=lambda row: float(str(row[1])), reverse=True)
    return rows[:top_n]


def _cap_pressure_rows(records: list[dict[str, object]]) -> list[list[object]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for record in records:
        prompt_chars = _coerce_int(record.get("prompt_chars"))
        prompt_cap = _coerce_int(record.get("prompt_cap"))
        if prompt_chars is None or prompt_cap is None or prompt_cap <= 0:
            continue
        normalized = dict(record)
        normalized["prompt_chars"] = prompt_chars
        normalized["prompt_cap"] = prompt_cap
        grouped[(str(record["model"]), str(record["activity_type"]))].append(normalized)

    rows: list[list[object]] = []
    for (model, activity_type), items in grouped.items():
        pct_values = [
            (100.0 * int(item["prompt_chars"])) / int(item["prompt_cap"])
            for item in items
        ]
        rows.append(
            [
                model,
                activity_type,
                len(items),
                _format_pct_of_cap(_percentile(pct_values, 50)),
                _format_pct_of_cap(_percentile(pct_values, 95)),
                sum(
                    1
                    for item in items
                    if int(item["prompt_chars"]) >= (0.9 * int(item["prompt_cap"]))
                ),
                sum(
                    1
                    for item in items
                    if int(item["prompt_chars"]) > int(item["prompt_cap"])
                ),
            ]
        )
    rows.sort(key=lambda row: (str(row[0]), str(row[1])))
    return rows


def _lens_rows(records: list[dict[str, object]]) -> list[list[object]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for record in records:
        lens = str(record.get("lens", "") or "").strip()
        if not lens:
            continue
        grouped[(lens, str(record["model"]))].append(record)

    rows: list[list[object]] = []
    for (lens, model), items in grouped.items():
        durations = [float(item["duration_seconds"]) for item in items]
        rows.append(
            [
                lens,
                model,
                len(items),
                _format_duration(sum(durations)),
                _format_duration(_percentile(durations, 50)),
                _format_duration(max(durations)),
                sum(1 for item in items if item.get("parse_error")),
            ]
        )
    rows.sort(key=lambda row: float(str(row[3])), reverse=True)
    return rows[:20]


def _render_report(records: list[dict[str, object]], data_quality: dict[str, object], top_n: int) -> str:
    total_duration = sum(float(record["duration_seconds"]) for record in records)
    total_cost = sum(float(record["cost_usd_estimate"]) for record in records)
    total_parse_errors = sum(1 for record in records if record.get("parse_error"))
    artifact_records = _artifact_size_records()
    timestamps = [record["timestamp"] for record in records if isinstance(record.get("timestamp"), datetime)]
    date_range = "unknown"
    if timestamps:
        ordered = sorted(timestamps)
        date_range = f"{ordered[0].isoformat().replace('+00:00', 'Z')} to {ordered[-1].isoformat().replace('+00:00', 'Z')}"

    lines: list[str] = [
        "# FF Review Performance Analysis",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}",
        "",
        "## 1. Headline Numbers",
        "",
        f"- Total reviewer + judge calls measured: {len(records)}",
        f"- Total wall-clock seconds: {_format_duration(total_duration)}",
        f"- Total estimated USD cost: {_format_cost(total_cost)}",
        f"- Calls with token parse errors: {total_parse_errors} ({_format_percent(total_parse_errors, len(records))})",
        f"- Date range covered: {date_range}",
        f"- Distinct slugs: {len({str(record['slug']) for record in records})}",
        "",
        "## 2. Per (model x activity_type) Summary",
        "",
    ]

    section_2_rows = _summarize_grouped(records, ("model", "activity_type"))
    if section_2_rows:
        lines.extend(
            _markdown_table(
                [
                    "model",
                    "activity_type",
                    "count",
                    "total_duration_s",
                    "p50_s",
                    "p95_s",
                    "p99_s",
                    "max_s",
                    "parse_error_count",
                    "parse_error_rate",
                ],
                section_2_rows,
            )
        )
    else:
        lines.append("No relevant records found.")
    lines.extend(["", "## 3. Per (model x stage) Summary", ""])

    section_3_rows = _summarize_grouped(records, ("model", "stage"))
    if section_3_rows:
        lines.extend(
            _markdown_table(
                [
                    "model",
                    "stage",
                    "count",
                    "total_duration_s",
                    "p50_s",
                    "p95_s",
                    "p99_s",
                    "max_s",
                    "parse_error_count",
                    "parse_error_rate",
                ],
                section_3_rows,
            )
        )
    else:
        lines.append("No relevant records found.")

    lens_rows = _lens_rows(records)
    lines.extend(["", "## 3a. Best-Effort Lens Summary", ""])
    if lens_rows:
        lines.extend(
            _markdown_table(
                ["lens", "model", "count", "total_duration_s", "p50_s", "max_s", "parse_error_count"],
                lens_rows,
            )
        )
    else:
        lines.append("No unambiguous lens attributions found.")

    lines.extend(["", f"## 4. Top {top_n} Slowest Individual Calls", ""])
    slowest_rows = _top_slowest_rows(records, top_n)
    if slowest_rows:
        lines.extend(
            _markdown_table(
                [
                    "slug",
                    "stage",
                    "round",
                    "activity_type",
                    "model",
                    "duration_s",
                    "input_tokens",
                    "output_tokens",
                    "parse_error",
                    "timestamp",
                ],
                slowest_rows,
            )
        )
    else:
        lines.append("No relevant records found.")

    lines.extend(["", "## 5. Parse Error Patterns", ""])
    error_rows = _parse_error_rows(records)
    if error_rows:
        lines.extend(_markdown_table(["error pattern", "count", "models affected", "example slug"], error_rows))
    else:
        lines.append("No parse errors found.")

    lines.extend(["", "## 6. Duration vs Input Tokens Correlation", ""])
    correlation_rows = _correlation_rows(records)
    if correlation_rows:
        lines.extend(
            _markdown_table(
                ["model", "input_token_bin", "count", "p50_duration_s", "p95_duration_s"],
                correlation_rows,
            )
        )
    else:
        lines.append("No records with input token counts found.")

    lines.extend(["", "## 7. Wall Clock by Slug (Top 20)", ""])
    slug_rows = _slug_rows(records)
    if slug_rows:
        lines.extend(
            _markdown_table(
                [
                    "slug",
                    "total_review_duration_s",
                    "total_judge_duration_s",
                    "total_deferred_round_duration_s",
                    "rounds",
                    "stages_reaching_judge_cap",
                ],
                slug_rows,
            )
        )
    else:
        lines.append("No relevant records found.")

    per_feature_rows = _per_feature_rows(top_n)
    lines.extend(["", "## 7a. Per-Feature Metrics Rollup (Top 20 by Wall Seconds)", ""])
    if per_feature_rows:
        lines.extend(
            _markdown_table(
                [
                    "slug",
                    "total_wall_seconds",
                    "codex_tokens",
                    "gemini_tokens",
                    "ttl_crossings",
                    "command_count",
                ],
                per_feature_rows,
            )
        )
    else:
        lines.append("No command telemetry found.")
    lines.extend([
        "",
        "**Note on Claude token measurement.** This table aggregates Codex and Gemini token usage "
        "(those tools' CLIs report token counts the runner captures). It does NOT include Claude "
        "orchestrator session tokens — Claude Code does not expose those to the FF runner. For "
        "session-level Claude usage, run `/cost` in Claude Code. The `ttl_crossings` column is a "
        "proxy for cache pressure: each crossing means the orchestrator's prompt cache likely "
        "expired between commands, requiring an uncached re-read.",
        "",
    ])

    artifact_distribution_rows = _artifact_distribution_rows(artifact_records)
    largest_artifact_rows = _largest_artifact_rows(artifact_records)
    lines.extend(["", "## 8. Artifact Sizes", ""])
    if artifact_distribution_rows:
        lines.extend(["### Table 8a — Per-stage artifact-size distribution", ""])
        lines.extend(
            _markdown_table(
                [
                    "stage",
                    "count",
                    "p50_chars",
                    "p95_chars",
                    "max_chars",
                    "over_50k_count",
                    "over_100k_count",
                ],
                artifact_distribution_rows,
            )
        )
        lines.extend(["", "### Table 8b — Top 10 largest artifacts", ""])
        lines.extend(
            _markdown_table(
                ["slug", "stage", "char_count"],
                largest_artifact_rows,
            )
        )
    else:
        lines.append("No workflow artifacts found.")

    cap_pressure_rows = _cap_pressure_rows(records)
    lines.extend(["", "## 9. Prompt-Cap Pressure", ""])
    if cap_pressure_rows:
        lines.extend(["### Table 9a — Cap-pressure summary", ""])
        lines.extend(
            _markdown_table(
                [
                    "model",
                    "activity_type",
                    "count_with_size_data",
                    "p50_pct_of_cap",
                    "p95_pct_of_cap",
                    "within_90pct_cap",
                    "over_cap",
                ],
                cap_pressure_rows,
            )
        )
    else:
        lines.append("No prompt-size data available yet — re-run after the next 2-3 feature runs.")

    dropped_fields = data_quality["dropped_fields"]
    partial_fields = data_quality["partial_fields"]
    skipped_slugs = data_quality["skipped_slugs"]
    token_usage_missing = data_quality["token_usage_missing"]
    assert isinstance(dropped_fields, Counter)
    assert isinstance(partial_fields, Counter)
    assert isinstance(skipped_slugs, list)
    assert isinstance(token_usage_missing, list)

    lines.extend(
        [
            "",
            "## 10. Data Quality",
            "",
            f"- Dropped records: {int(data_quality['dropped_records'])}",
            f"- Dropped-record field breakdown: {', '.join(f'{field}={count}' for field, count in sorted(dropped_fields.items())) or 'none'}",
            f"- Partial-but-kept field breakdown: {', '.join(f'{field}={count}' for field, count in sorted(partial_fields.items())) or 'none'}",
            f"- Slugs with malformed state.json: {', '.join(skipped_slugs) or 'none'}",
            f"- Slugs with missing or non-list token_usage: {', '.join(token_usage_missing) or 'none'}",
            f"- Records with timestamps outside reasonable range: {int(data_quality['bad_timestamps'])}",
            f"- Malformed review files skipped during deferred-round scan: {int(data_quality['malformed_review_files'])}",
            f"- Deferred-round inference failures: {int(data_quality['deferred_inference_failures'])}",
            f"- Records without unambiguous lens attribution: {int(data_quality['unattributed_lens_records'])}",
            f"- Estimated USD cost is a lower bound because {total_parse_errors} calls had parse errors and null costs were treated as $0.",
            "- Lens attribution is best-effort only. Current token_usage records do not reliably carry a per-call lens id across repeated rounds.",
        ]
    )
    return "\n".join(lines) + "\n"


@readonly_command("analyze-reviews")
def command_analyze_reviews(args: argparse.Namespace) -> int:
    top_n = int(getattr(args, "top_n", 20) or 20)
    output_path = _resolve_output_path(getattr(args, "out", None))

    records, data_quality = _load_records(top_n)
    report = _render_report(records, data_quality, top_n)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(str(output_path))
    return 0
