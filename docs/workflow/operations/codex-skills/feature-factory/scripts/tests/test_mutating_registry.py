import argparse
import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]

STATE_SPEC = importlib.util.spec_from_file_location("factory_state", SCRIPT_DIR / "factory_state.py")
assert STATE_SPEC and STATE_SPEC.loader
FACTORY_STATE = importlib.util.module_from_spec(STATE_SPEC)
sys.modules[STATE_SPEC.name] = FACTORY_STATE
STATE_SPEC.loader.exec_module(FACTORY_STATE)

INVARIANTS_SPEC = importlib.util.spec_from_file_location("factory_invariants", SCRIPT_DIR / "factory_invariants.py")
assert INVARIANTS_SPEC and INVARIANTS_SPEC.loader
FACTORY_INVARIANTS = importlib.util.module_from_spec(INVARIANTS_SPEC)
sys.modules[INVARIANTS_SPEC.name] = FACTORY_INVARIANTS
INVARIANTS_SPEC.loader.exec_module(FACTORY_INVARIANTS)

MUTATING_SPEC = importlib.util.spec_from_file_location("factory_mutating", SCRIPT_DIR / "factory_mutating.py")
assert MUTATING_SPEC and MUTATING_SPEC.loader
FACTORY_MUTATING = importlib.util.module_from_spec(MUTATING_SPEC)
sys.modules[MUTATING_SPEC.name] = FACTORY_MUTATING
MUTATING_SPEC.loader.exec_module(FACTORY_MUTATING)

RUN_FACTORY_SPEC = importlib.util.spec_from_file_location("run_factory", SCRIPT_DIR / "run_factory.py")
assert RUN_FACTORY_SPEC and RUN_FACTORY_SPEC.loader
RUN_FACTORY = importlib.util.module_from_spec(RUN_FACTORY_SPEC)
sys.modules[RUN_FACTORY_SPEC.name] = RUN_FACTORY
RUN_FACTORY_SPEC.loader.exec_module(RUN_FACTORY)


EXPECTED_SUBCOMMANDS = {
    "init",
    "doctor",
    "status",
    "repair",
    "checkpoint",
    "reconcile",
    "block",
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
}

EXPECTED_MUTATING = {
    "init",
    "checkpoint",
    "reconcile",
    "implement",
    "deliver",
    "block",
    "advance",
    "dispatch-codex",
    "repair",
    "closeout",
    "discover",
    "parallel",
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
            "advance": RUN_FACTORY.command_advance,
            "dispatch-codex": RUN_FACTORY.command_dispatch_codex,
            "repair": RUN_FACTORY.command_repair,
            "closeout": RUN_FACTORY.command_closeout,
            "discover": RUN_FACTORY.command_discover,
            "parallel": RUN_FACTORY.command_parallel,
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
