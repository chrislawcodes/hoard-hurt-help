#!/usr/bin/env python3
"""command_discover implementation."""
import argparse
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_state import (  # noqa: E402
    DISCOVERY_KEY,
    blocking_unresolved_items,
    discovery_blockers_are_malformed,
    default_discovery_state,
    update_discovery_state,
)

from factory_git import ensure_sync  # noqa: E402

from factory_review import trim_detail  # noqa: E402

from factory_emit import _emit_next_action  # noqa: E402
from factory_mutating import mutates_state  # noqa: E402
from factory_size_estimate import estimate_size  # noqa: E402


@mutates_state("discover")
def command_discover(args: argparse.Namespace) -> int:
    ensure_sync()
    clear = getattr(args, "clear", False)
    force_complete = getattr(args, "force_complete", False)
    if clear and any(
        [
            args.required,
            args.count is not None,
            args.question,
            args.recommendation,
            args.rationale,
            args.assumption,
            args.summary is not None,
            args.complete,
            force_complete,
            getattr(args, "unresolved", None) is not None,
            getattr(args, "resolve", None) is not None,
            getattr(args, "defer", None) is not None,
            getattr(args, "non_goal", None) is not None,
            getattr(args, "acceptance_criteria", None) is not None,
            getattr(args, "clear_non_goals", False),
            getattr(args, "clear_acceptance_criteria", False),
            getattr(args, "answer", None) is not None,
        ]
    ):
        raise SystemExit("discover --clear cannot be combined with other discovery updates")
    if args.count is not None and args.count < 0:
        raise SystemExit("discover requires --count to be zero or greater")
    if any([args.question, args.recommendation, args.rationale]) and not all(
        [args.question, args.recommendation, args.rationale]
    ):
        raise SystemExit("discover requires --question, --recommendation, and --rationale together")
    if not clear and not any(
        [
            args.required,
            args.count is not None,
            args.question,
            args.assumption,
            args.summary is not None,
            args.complete,
            force_complete,
            getattr(args, "unresolved", None) is not None,
            getattr(args, "resolve", None) is not None,
            getattr(args, "defer", None) is not None,
            getattr(args, "non_goal", None) is not None,
            getattr(args, "acceptance_criteria", None) is not None,
            getattr(args, "clear_non_goals", False),
            getattr(args, "clear_acceptance_criteria", False),
            getattr(args, "answer", None) is not None,
        ]
    ):
        raise SystemExit("discover requires at least one update, or use --clear to reset discovery state")

    def mutate(discovery: dict) -> None:
        if clear:
            preserved_required = bool(discovery.get("required"))
            preserved_question_count = discovery.get("question_count", 0)
            if not isinstance(preserved_question_count, int):
                preserved_question_count = 0
            preserved_asked_count = discovery.get("asked_count", 0)
            if not isinstance(preserved_asked_count, int):
                preserved_asked_count = 0
            preserved_questions = discovery.get("questions", [])
            if not isinstance(preserved_questions, list):
                preserved_questions = []
            preserved_assumptions = discovery.get("assumptions", [])
            if not isinstance(preserved_assumptions, list):
                preserved_assumptions = []
            preserved_summary = discovery.get("summary", "")
            if not isinstance(preserved_summary, str):
                preserved_summary = ""
            preserved_answers = discovery.get("answers", {})
            if not isinstance(preserved_answers, dict):
                preserved_answers = {}
            preserved_non_goals = discovery.get("non_goals", [])
            if not isinstance(preserved_non_goals, list):
                preserved_non_goals = []
            preserved_acceptance = discovery.get("acceptance_criteria", [])
            if not isinstance(preserved_acceptance, list):
                preserved_acceptance = []
            discovery.clear()
            discovery.update(default_discovery_state())
            discovery["required"] = (
                preserved_required
                or bool(preserved_question_count)
                or bool(preserved_questions)
                or bool(preserved_assumptions)
            )
            discovery["question_count"] = preserved_question_count
            discovery["asked_count"] = preserved_asked_count
            discovery["questions"] = preserved_questions
            discovery["assumptions"] = preserved_assumptions
            discovery["summary"] = preserved_summary
            discovery["answers"] = preserved_answers
            discovery["non_goals"] = preserved_non_goals
            discovery["acceptance_criteria"] = preserved_acceptance
            discovery["unresolved"] = []
            discovery["complete"] = False
            discovery["updated_at"] = int(time.time())
            return
        if args.required:
            discovery["required"] = True
        if args.count is not None:
            if discovery.get("asked_count", 0) > args.count:
                raise SystemExit(
                    "discover requires --count to stay at or above the number of already asked questions; "
                    "use --clear to restart discovery"
                )
            discovery["question_count"] = args.count
            discovery["required"] = discovery["required"] or args.count > 0
        if args.question:
            questions = list(discovery.get("questions", []))
            questions.append(
                {
                    "question": args.question,
                    "recommendation": args.recommendation,
                    "rationale": args.rationale,
                    "updated_at": int(time.time()),
                }
            )
            discovery["questions"] = questions
            discovery["asked_count"] = len(questions)
            discovery["required"] = True
            if discovery.get("question_count", 0) < len(questions):
                discovery["question_count"] = len(questions)
        if args.assumption:
            assumptions = list(discovery.get("assumptions", []))
            for assumption in args.assumption:
                if assumption not in assumptions:
                    assumptions.append(assumption)
            discovery["assumptions"] = assumptions
            discovery["required"] = discovery["required"] or bool(assumptions)
        if args.summary is not None:
            discovery["summary"] = args.summary
            discovery["required"] = discovery["required"] or bool(args.summary.strip())
        if getattr(args, "answer", None) is not None:
            question_text, answer_text = args.answer
            if not isinstance(discovery.get("answers"), dict):
                discovery["answers"] = {}
            discovery["answers"][question_text] = answer_text
        if getattr(args, "unresolved", None) is not None:
            item_text = args.unresolved
            unresolved = discovery.setdefault("unresolved", [])
            if not any(u["item"] == item_text for u in unresolved):
                unresolved.append({"item": item_text, "deferred": False})
        if getattr(args, "resolve", None) is not None:
            resolve_text = args.resolve
            discovery["unresolved"] = [
                u for u in discovery.get("unresolved", []) if u["item"] != resolve_text
            ]
        if getattr(args, "defer", None) is not None:
            defer_text = args.defer
            for u in discovery.get("unresolved", []):
                if u["item"] == defer_text:
                    u["deferred"] = True
                    break
        # Feature B Slice 2 — append semantics + clear flags.
        # --clear-non-goals runs BEFORE any --non-goal appends in the same invocation.
        if getattr(args, "clear_non_goals", False):
            discovery["non_goals"] = []
        if getattr(args, "clear_acceptance_criteria", False):
            discovery["acceptance_criteria"] = []
        non_goals_in = getattr(args, "non_goal", None) or []
        for raw in non_goals_in:
            stripped = str(raw or "").strip()
            if not stripped:
                raise SystemExit("discover --non-goal cannot be empty or whitespace-only")
            ng = discovery.setdefault("non_goals", [])
            if stripped not in ng:
                ng.append(stripped)
        acceptance_in = getattr(args, "acceptance_criteria", None) or []
        for raw in acceptance_in:
            stripped = str(raw or "").strip()
            if not stripped:
                raise SystemExit("discover --acceptance-criteria cannot be empty or whitespace-only")
            ac = discovery.setdefault("acceptance_criteria", [])
            if stripped not in ac:
                ac.append(stripped)
        blocking = blocking_unresolved_items(discovery)
        if args.complete:
            if blocking:
                if discovery_blockers_are_malformed(discovery):
                    raise SystemExit(
                        "discover cannot mark discovery complete while discovery state is malformed; "
                        "use discover --clear to reset malformed discovery state"
                    )
                raise SystemExit(
                    "discover cannot mark discovery complete while unresolved items remain; "
                    "resolve or defer each item first"
                )
            if not force_complete and int(discovery.get("asked_count", 0)) < int(discovery.get("question_count", 0)):
                raise SystemExit(
                    "discover cannot mark discovery complete before the planned questions are recorded; "
                    "use --force-complete if you intentionally want to override the count"
                )
            discovery["complete"] = True
        elif (
            args.required
            or args.count is not None
            or args.question
            or args.assumption
            or args.summary is not None
            or getattr(args, "answer", None) is not None
            or getattr(args, "unresolved", None) is not None
            or getattr(args, "resolve", None) is not None
            or getattr(args, "defer", None) is not None
            or getattr(args, "non_goal", None) is not None
            or getattr(args, "acceptance_criteria", None) is not None
        ):
            discovery["complete"] = False
        if force_complete:
            if blocking:
                raise SystemExit(
                    "discover cannot force discovery complete while unresolved items remain; "
                    "resolve or defer each item first"
                )
            discovery["complete"] = True
        discovery["updated_at"] = int(time.time())

    state = update_discovery_state(args.slug, mutate)
    discovery = state.get(DISCOVERY_KEY, {})
    remaining = max(int(discovery.get("question_count", 0)) - int(discovery.get("asked_count", 0)), 0)
    print(f"workflow: {args.slug}")
    print("discovery:")
    print(f"- version: {discovery.get('version', 1)}")
    print(f"- required: {'yes' if discovery.get('required') else 'no'}")
    print(f"- complete: {'yes' if discovery.get('complete') else 'no'}")
    print(f"- question-count: {discovery.get('question_count', 0)}")
    print(f"- asked-count: {discovery.get('asked_count', 0)}")
    print(f"- remaining: {remaining}")
    if discovery.get("assumptions"):
        print(f"- assumptions: {len(discovery.get('assumptions', []))}")
        for assumption in discovery.get("assumptions", []):
            print(f"- assumption: {assumption}")
    if discovery.get("summary"):
        print(f"- summary: {trim_detail(str(discovery.get('summary', '')))}")
    if discovery.get("non_goals"):
        print(f"- non-goals: {len(discovery['non_goals'])}")
        for ng in discovery["non_goals"]:
            print(f"  - {ng}")
    if discovery.get("acceptance_criteria"):
        print(f"- acceptance-criteria: {len(discovery['acceptance_criteria'])}")
        for ac in discovery["acceptance_criteria"]:
            print(f"  - {ac}")
    if discovery.get("answers"):
        print(f"- answers: {len(discovery['answers'])}")
    if discovery.get("unresolved"):
        print(f"- unresolved: {len(discovery['unresolved'])}")
        for u in discovery["unresolved"]:
            status = " [deferred]" if u.get("deferred") else ""
            print(f"  - {u['item']}{status}")
    if args.complete or getattr(args, "force_complete", False):
        _emit_next_action(args.slug, "discovery complete")
        print("[workflow] ✓ discovery complete")
        try:
            est = estimate_size(args.slug)
            size_label = est["size"].upper()
            signals = est["signals"]
            scope_count = signals["scope_path_count"]
            summary_chars = signals["summary_chars"]
            diff_lines = signals["diff_lines"]
            diff_note = "no diff yet" if diff_lines is None else f"{diff_lines}-line diff"
            print(
                f"[workflow] size-estimate: {size_label} "
                f"({scope_count} scope path{'s' if scope_count != 1 else ''}, "
                f"{summary_chars}-char summary, {diff_note})"
            )

            # Determine effective recommended path, respecting --force-path.
            force_path = getattr(args, "force_path", "auto")
            effective_path = est["recommended_path"] if force_path == "auto" else force_path

            if force_path not in ("auto", "none") and est["recommended_path"] == "none":
                print(
                    f"[workflow] note: size-estimate recommended 'none' (skip FF) but "
                    f"--force-path {force_path} overrides."
                )

            if effective_path == "none":
                # Louder "skip FF" recommendation for trivial features.
                signal_list = ", ".join(
                    k for k, v in [
                        ("few scope paths", scope_count <= 2),
                        ("short summary", summary_chars < 300),
                        ("small diff", diff_lines is not None and diff_lines < 100),
                        ("few files changed", signals.get("changed_files") is not None and signals["changed_files"] <= 3),
                    ] if v
                ) or est["reasoning"]
                print(f"[ff] This feature looks trivial (size: trivial, signals: {signal_list}).")
                print("[ff] Recommendation: SKIP FF ENTIRELY.")
                print("[ff]   - Write the spec inline (a few sentences in the Codex prompt is fine).")
                print(f"[ff]   - Dispatch Codex directly: codex exec -m gpt-5.4-mini -s workspace-write \"<spec>\"")
                print("[ff]   - Open a PR and merge on green CI.")
                print("[ff]")
                print("[ff] FF adds value when there's enough surface area for adversarial review or")
                print("[ff] checkpoint discipline to catch real risk. For a feature this small, the")
                print("[ff] runner overhead exceeds its protection.")
                print("[ff]")
                print(f"[ff] If you want to use FF anyway (e.g., to keep a state.json record): rerun")
                print(f"[ff] with --force-path quick or --force-path full.")
            elif effective_path == "quick":
                prompt_path_hint = ""
                try:
                    import factory_state as _fs
                    prompt_path_file = _fs.workflow_dir(args.slug) / "prompt.md"
                    if prompt_path_file.exists():
                        prompt_path_hint = f" --prompt-path {prompt_path_file}"
                except Exception:
                    pass
                print(f"[workflow] → recommended: quick --slug {args.slug}{prompt_path_hint}")
                print("[workflow] (override: run author_spec for full workflow)")
            else:
                print("[workflow] → recommended: author_spec")
        except Exception:
            pass  # size estimate is advisory; never fail discover --complete
    return 0
