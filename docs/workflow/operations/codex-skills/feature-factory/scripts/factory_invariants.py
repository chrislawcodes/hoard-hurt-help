#!/usr/bin/env python3
"""Post-run invariant checks for the feature factory runner.

Run after every state-mutating command to catch contradictions between the
judge panel's verdict and the next-action decision tree — the class of bug
that produced the run-033 loop. The check writes any contradiction to
``state.invariant_warnings`` and emits a single-line warning to stdout or
stderr (see :func:`_emit_target`).

This module is intentionally narrow: one invariant today (FR-010), with room
to add more. Failures inside the check itself never abort the caller.
"""
from __future__ import annotations

import sys
import time
from typing import Callable, Iterable

from factory_state import INVARIANT_WARNINGS_KEY, _INVARIANT_WARNINGS_CAP


# Warnings always emit to stderr. Gemini requirements-adversarial round 2
# flagged conditional stdout/stderr routing as a risk — automated tools
# parsing stdout would miss contradiction warnings. Unix convention is
# warnings → stderr; terminals show both streams so interactive operators
# still see it. JSON_MODE is preserved for back-compat and tests but has
# no behavioral effect on the emit target.
JSON_MODE: bool = False


# Stages whose judge_next_action field we inspect for the contradiction check.
_CHECKPOINT_STAGES: tuple[str, ...] = ("spec", "plan", "tasks", "diff", "closeout")


def _emit_target():
    return sys.stderr


def _append_warning(state: dict, entry: dict) -> None:
    warnings = state.setdefault(INVARIANT_WARNINGS_KEY, [])
    warnings.append(entry)
    # Keep the most recent _INVARIANT_WARNINGS_CAP entries to bound state.json size.
    if len(warnings) > _INVARIANT_WARNINGS_CAP:
        del warnings[: len(warnings) - _INVARIANT_WARNINGS_CAP]


def _is_repair_action_for_stage(action: str, stage: str) -> bool:
    """Return True if *action* is a repair action targeting *stage*."""
    if not isinstance(action, str) or not action:
        return False
    return action == f"repair_{stage}_checkpoint"


def check_judge_advance_vs_recommended(
    state: dict,
    recommended: str,
) -> list[dict]:
    """FR-010 — the core invariant.

    Return a list of per-stage contradiction dicts. Each dict has keys
    ``stage`` and ``detail``. Callers wrap these into state-level warnings
    with timestamp + command via :func:`run_invariant_checks`.
    """
    contradictions: list[dict] = []
    stages = state.get("stages") or {}
    for stage in _CHECKPOINT_STAGES:
        stage_state = stages.get(stage) or {}
        if stage_state.get("judge_next_action") != "advance":
            continue
        if _is_repair_action_for_stage(recommended, stage):
            contradictions.append(
                {
                    "stage": stage,
                    "detail": (
                        f"judge_next_action=advance for stage={stage} "
                        f"but recommended_next_action={recommended}"
                    ),
                }
            )
    return contradictions


# Public alias so tests can point at the list of invariants if we grow more.
_INVARIANTS: tuple[Callable[[dict, str], list[dict]], ...] = (
    check_judge_advance_vs_recommended,
)


def run_invariant_checks(
    state: dict,
    command: str,
    recommended: str,
    invariants: Iterable[Callable[[dict, str], list[dict]]] | None = None,
) -> list[dict]:
    """Run all invariants against *state*; append warnings; emit messages.

    The function never raises — any invariant that itself errors is swallowed
    and reported as a self-failure warning. Returns the list of newly appended
    warning dicts (may be empty).

    ``recommended`` is the current output of
    :func:`factory_next_action.recommended_next_action` for this slug.
    Callers compute it once after applying their state mutation.
    """
    if not isinstance(state, dict):
        return []
    appended: list[dict] = []
    invariant_fns = list(invariants) if invariants is not None else list(_INVARIANTS)
    now = int(time.time())
    for fn in invariant_fns:
        try:
            findings = fn(state, recommended)
        except Exception as exc:  # noqa: BLE001 — invariant failure must not abort the caller
            findings = [
                {
                    "stage": "",
                    "detail": f"invariant {fn.__name__} raised {type(exc).__name__}: {exc}",
                }
            ]
        for finding in findings or []:
            entry = {
                "at": now,
                "command": command,
                "stage": finding.get("stage", ""),
                "detail": finding.get("detail", ""),
            }
            _append_warning(state, entry)
            appended.append(entry)
            _print_warning(entry)
    return appended


def _print_warning(entry: dict) -> None:
    """Emit a single-line warning to stdout or stderr per JSON_MODE."""
    detail = entry.get("detail", "")
    target = _emit_target()
    try:
        target.write(
            "⚠ state contradiction detected: "
            f"{detail} — see docs/workflow/plans/feature-factory-runner-fixes.md\n"
        )
        target.flush()
    except Exception:  # noqa: BLE001 — output failure must not abort the caller
        pass


def set_json_mode(enabled: bool) -> None:
    """Toggle JSON_MODE for back-compat callers.

    The flag has no behavioral effect on emit target — warnings always go
    to stderr — but existing callers that set it would otherwise error. The
    FF_INVARIANT_EMIT env var is not consulted; kept here only so a stale
    reference never silently re-enables conditional routing.
    """
    global JSON_MODE
    JSON_MODE = bool(enabled)
