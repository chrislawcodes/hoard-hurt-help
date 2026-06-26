import argparse
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_state as FACTORY_STATE  # noqa: E402
import factory_invariants as FACTORY_INVARIANTS  # noqa: E402
import factory_mutating as FACTORY_MUTATING  # noqa: E402
import run_factory as RUN_FACTORY  # noqa: E402


EXPECTED_SUBCOMMANDS = {
    "init",
    "doctor",
    "status",
    "repair",
    "checkpoint",
    "reconcile",
    "block",
    "arch-docs",
    "advance",
    "dispatch-codex",
    "discover",
    "parallel",
    "implement",
    "deliver",
    "closeout",
    "review-extract",
    "check-isolation",
    "analyze-reviews",
    "quick",
    "audit",
    "autopilot",
    "prepare-claude-reviews",
}

EXPECTED_MUTATING = {
    "init",
    "checkpoint",
    "reconcile",
    "implement",
    "deliver",
    "block",
    "arch-docs",
    "advance",
    "dispatch-codex",
    "repair",
    "closeout",
    "discover",
    "parallel",
    "autopilot",
    "prepare-claude-reviews",
}

EXPECTED_READONLY = {"status", "doctor", "review-extract", "check-isolation", "analyze-reviews", "quick", "audit"}


def _assert_registry_is_classified(parser: argparse.ArgumentParser) -> None:
    handlers = list(FACTORY_MUTATING.enumerate_subparser_handlers(parser))
    handler_names = {name for name, _ in handlers}
    assert handler_names == EXPECTED_SUBCOMMANDS, handler_names
    for name, handler in handlers:
        assert handler.__name__ != "<lambda>", f"{name} uses lambda handler"
    mutating, readonly, undecorated = FACTORY_MUTATING.all_classified_names(handler for _, handler in handlers)
    assert mutating == EXPECTED_MUTATING, mutating
    assert readonly == EXPECTED_READONLY, readonly
    assert undecorated == set(), undecorated


class MutatingRegistryTests(unittest.TestCase):
    def test_build_parser_handlers_are_decorated(self) -> None:
        parser = RUN_FACTORY.build_parser()
        _assert_registry_is_classified(parser)

    def test_direct_decorator_attributes_are_present(self) -> None:
        mutating_handlers = {
            "init": RUN_FACTORY.command_init,
            "checkpoint": RUN_FACTORY.command_checkpoint,
            "reconcile": RUN_FACTORY.command_reconcile,
            "implement": RUN_FACTORY.command_implement,
            "deliver": RUN_FACTORY.command_deliver,
            "block": RUN_FACTORY.command_block,
            "arch-docs": RUN_FACTORY.command_arch_docs,
            "advance": RUN_FACTORY.command_advance,
            "dispatch-codex": RUN_FACTORY.command_dispatch_codex,
            "repair": RUN_FACTORY.command_repair,
            "closeout": RUN_FACTORY.command_closeout,
            "discover": RUN_FACTORY.command_discover,
            "parallel": RUN_FACTORY.command_parallel,
            "autopilot": RUN_FACTORY.command_autopilot,
        }
        for name, handler in mutating_handlers.items():
            self.assertEqual(getattr(handler, "__ff_mutates_state__"), name)

        self.assertEqual(getattr(RUN_FACTORY.command_status, "__ff_readonly_command__"), "status")
        self.assertEqual(getattr(RUN_FACTORY.command_doctor, "__ff_readonly_command__"), "doctor")
        self.assertEqual(getattr(RUN_FACTORY.command_review_extract, "__ff_readonly_command__"), "review-extract")
        self.assertEqual(getattr(RUN_FACTORY.command_check_workflow_isolation, "__ff_readonly_command__"), "check-isolation")
        self.assertEqual(getattr(RUN_FACTORY.command_analyze_reviews, "__ff_readonly_command__"), "analyze-reviews")
        self.assertEqual(getattr(RUN_FACTORY.command_quick, "__ff_readonly_command__"), "quick")

    def test_fake_undecorated_handler_fails_with_subcommand_name(self) -> None:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command", required=True)
        fake = subparsers.add_parser("fake")

        def command_fake(args: argparse.Namespace) -> int:
            return 0

        fake.set_defaults(func=command_fake)

        with self.assertRaises(AssertionError) as ctx:
            _assert_registry_is_classified(parser)

        self.assertIn("fake", str(ctx.exception))

    def test_init_safety_does_not_flag_empty_stage_state(self) -> None:
        state = FACTORY_STATE._default_workflow_state()
        state["schema_version"] = 2
        self.assertEqual(FACTORY_INVARIANTS.check_judge_advance_vs_recommended(state, "repair_spec_checkpoint"), [])


if __name__ == "__main__":
    unittest.main()
