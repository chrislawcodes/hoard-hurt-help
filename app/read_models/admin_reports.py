"""Read models for platform-admin reporting pages."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from math import ceil, floor
from statistics import mean, median

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match import GameState, Match
from app.models.turn import Turn, TurnSubmission

_TEST_NAME_PREFIX = "prod smoke"

_BUCKETS: list[tuple[str, float, float | None]] = [
    ("0-10s", 0.0, 10.0),
    ("10-20s", 10.0, 20.0),
    ("20-30s", 20.0, 30.0),
    ("30-45s", 30.0, 45.0),
    ("45-60s", 45.0, 60.0),
    ("60-90s", 60.0, 90.0),
    ("90-120s", 90.0, 120.0),
    ("120s+", 120.0, None),
]


@dataclass(frozen=True)
class TurnTimingBucket:
    """One bucket in the response-time histogram."""

    label: str
    count: int


@dataclass(frozen=True)
class TurnTimingMatchRow:
    """Per-match response-time summary."""

    game: str
    match_id: str
    name: str
    completed_at: datetime | None
    turn_count: int
    sample_count: int
    defaulted_count: int
    mean_seconds: float | None
    median_seconds: float | None
    p95_seconds: float | None
    max_seconds: float | None


@dataclass(frozen=True)
class TurnTimingReport:
    """Response-time summary for platform admins."""

    matches_scanned: int
    matches_with_samples: int
    turn_count: int
    sample_count: int
    defaulted_count: int
    min_seconds: float | None
    mean_seconds: float | None
    median_seconds: float | None
    p90_seconds: float | None
    p95_seconds: float | None
    max_seconds: float | None
    buckets: list[TurnTimingBucket]
    matches: list[TurnTimingMatchRow]


def _is_test_match(name: str) -> bool:
    return name.strip().lower().startswith(_TEST_NAME_PREFIX)


def _percentile(values: list[float], percentile_rank: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile_rank
    lower = floor(position)
    upper = ceil(position)
    if lower == upper:
        return ordered[lower]
    lower_value = ordered[lower]
    upper_value = ordered[upper]
    return lower_value + ((upper_value - lower_value) * (position - lower))


def _bucket_label(seconds: float) -> str:
    for label, lower, upper in _BUCKETS:
        if seconds < lower:
            continue
        if upper is None or seconds < upper:
            return label
    return _BUCKETS[-1][0]


async def load_turn_timing_report(db: AsyncSession) -> TurnTimingReport:
    """Load the platform-admin response-time report."""

    matches = list(
        (
            await db.execute(
                select(Match)
                .where(
                    Match.state == GameState.COMPLETED,
                    ~Match.name.ilike(f"{_TEST_NAME_PREFIX}%"),
                )
                .order_by(Match.completed_at.desc(), Match.scheduled_start.desc())
            )
        )
        .scalars()
        .all()
    )
    if not matches:
        return TurnTimingReport(
            matches_scanned=0,
            matches_with_samples=0,
            turn_count=0,
            sample_count=0,
            defaulted_count=0,
            min_seconds=None,
            mean_seconds=None,
            median_seconds=None,
            p90_seconds=None,
            p95_seconds=None,
            max_seconds=None,
            buckets=[TurnTimingBucket(label=label, count=0) for label, _, _ in _BUCKETS],
            matches=[],
        )

    match_ids = [match.id for match in matches]
    turns = list(
        (
            await db.execute(
                select(Turn)
                .where(Turn.match_id.in_(match_ids))
                .order_by(Turn.match_id, Turn.round, Turn.turn, Turn.id)
            )
        )
        .scalars()
        .all()
    )
    turn_by_id: dict[int, Turn] = {turn.id: turn for turn in turns}
    turns_by_match: dict[str, list[Turn]] = defaultdict(list)
    for turn in turns:
        turns_by_match[turn.match_id].append(turn)

    submissions: list[TurnSubmission] = []
    if turns:
        submissions = list(
            (
                await db.execute(
                    select(TurnSubmission)
                    .where(TurnSubmission.turn_id.in_([turn.id for turn in turns]))
                    .order_by(TurnSubmission.turn_id, TurnSubmission.id)
                )
            )
            .scalars()
            .all()
        )

    samples_by_match: dict[str, list[float]] = defaultdict(list)
    defaulted_by_match: dict[str, int] = defaultdict(int)
    all_samples: list[float] = []
    for submission in submissions:
        if submission.turn_id not in turn_by_id:
            continue
        turn = turn_by_id[submission.turn_id]
        if submission.was_defaulted or submission.submitted_at is None:
            defaulted_by_match[turn.match_id] += 1
            continue
        seconds = max(0.0, (submission.submitted_at - turn.opened_at).total_seconds())
        samples_by_match[turn.match_id].append(seconds)
        all_samples.append(seconds)

    bucket_counts = {label: 0 for label, _, _ in _BUCKETS}
    for sample in all_samples:
        bucket_counts[_bucket_label(sample)] += 1

    match_rows: list[TurnTimingMatchRow] = []
    for match in matches:
        samples = sorted(samples_by_match.get(match.id, []))
        match_rows.append(
            TurnTimingMatchRow(
                game=match.game,
                match_id=match.id,
                name=match.name,
                completed_at=match.completed_at,
                turn_count=len(turns_by_match.get(match.id, [])),
                sample_count=len(samples),
                defaulted_count=defaulted_by_match.get(match.id, 0),
                mean_seconds=mean(samples) if samples else None,
                median_seconds=median(samples) if samples else None,
                p95_seconds=_percentile(samples, 0.95),
                max_seconds=samples[-1] if samples else None,
            )
        )
    match_rows.sort(
        key=lambda row: (
            row.mean_seconds is None,
            -(row.mean_seconds or 0.0),
            -(row.sample_count),
            -(row.completed_at.timestamp() if row.completed_at else 0.0),
        )
    )

    return TurnTimingReport(
        matches_scanned=len(matches),
        matches_with_samples=sum(1 for row in match_rows if row.sample_count > 0),
        turn_count=len(turns),
        sample_count=len(all_samples),
        defaulted_count=sum(defaulted_by_match.values()),
        min_seconds=min(all_samples) if all_samples else None,
        mean_seconds=mean(all_samples) if all_samples else None,
        median_seconds=median(all_samples) if all_samples else None,
        p90_seconds=_percentile(all_samples, 0.90),
        p95_seconds=_percentile(all_samples, 0.95),
        max_seconds=max(all_samples) if all_samples else None,
        buckets=[TurnTimingBucket(label=label, count=bucket_counts[label]) for label, _, _ in _BUCKETS],
        matches=match_rows,
    )
