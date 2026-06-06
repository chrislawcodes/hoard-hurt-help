import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]

STATE_SPEC = importlib.util.spec_from_file_location(
    "factory_state",
    SCRIPT_DIR / "factory_state.py",
)
assert STATE_SPEC and STATE_SPEC.loader
FACTORY_STATE = importlib.util.module_from_spec(STATE_SPEC)
sys.modules[STATE_SPEC.name] = FACTORY_STATE
STATE_SPEC.loader.exec_module(FACTORY_STATE)

STAGES_SPEC = importlib.util.spec_from_file_location(
    "factory_stages",
    SCRIPT_DIR / "factory_stages.py",
)
assert STAGES_SPEC and STAGES_SPEC.loader
FACTORY_STAGES = importlib.util.module_from_spec(STAGES_SPEC)
sys.modules[STAGES_SPEC.name] = FACTORY_STAGES
STAGES_SPEC.loader.exec_module(FACTORY_STAGES)

NEXT_ACTION_SPEC = importlib.util.spec_from_file_location(
    "factory_next_action",
    SCRIPT_DIR / "factory_next_action.py",
)
assert NEXT_ACTION_SPEC and NEXT_ACTION_SPEC.loader
NEXT_ACTION = importlib.util.module_from_spec(NEXT_ACTION_SPEC)
sys.modules[NEXT_ACTION_SPEC.name] = NEXT_ACTION
NEXT_ACTION_SPEC.loader.exec_module(NEXT_ACTION)


def _base_stage_state(artifact_exists: bool, manifest_exists: bool = True, healthy: bool = True) -> dict[str, object]:
    return {
        "artifact_path": Path("placeholder.md"),
        "artifact_exists": artifact_exists,
        "artifact_meaningful": artifact_exists,
        "manifest_path": Path("placeholder.checkpoint.json"),
        "manifest_exists": manifest_exists,
        "healthy": healthy,
        "detail": "",
    }


def _base_state(adversarial_rounds: int = 0) -> dict:
    state = FACTORY_STATE._default_workflow_state()
    state["discovery"]["required"] = False
    state["discovery"]["complete"] = True
    state["blocked"]["active"] = False
    state["stages"] = {
        "spec": {
            "adversarial_rounds": adversarial_rounds,
            "annotations": [],
            "adversarial_sha_history": [],
            "initial_sha": "",
        }
    }
    state["spec_adversarial_rounds"] = adversarial_rounds
    return state


def _stages(plan_exists: bool = False) -> dict[str, dict[str, object]]:
    stages = {}
    for stage in FACTORY_STAGES.CHECKPOINT_STAGES:
        if stage == "spec":
            stages[stage] = _base_stage_state(True, True, True)
        elif stage == "plan":
            stages[stage] = _base_stage_state(plan_exists, plan_exists, plan_exists)
        else:
            stages[stage] = _base_stage_state(False, False, False)
    return stages


class FactoryNextActionBannerRenameTests(unittest.TestCase):
    def test_returns_run_spec_checkpoint_when_spec_manifest_missing(self) -> None:
        state = _base_state(adversarial_rounds=0)
        stages = _stages(plan_exists=False)
        stages["spec"] = _base_stage_state(True, False, False)
        action = NEXT_ACTION.recommended_next_action("banner-spec", state, stages, True)
        self.assertEqual(action, "run_spec_checkpoint")

    def test_returns_run_plan_checkpoint_when_plan_manifest_missing(self) -> None:
        state = _base_state(adversarial_rounds=0)
        stages = _stages(plan_exists=True)
        stages["plan"] = _base_stage_state(True, False, False)
        action = NEXT_ACTION.recommended_next_action("banner-plan", state, stages, True)
        self.assertEqual(action, "run_plan_checkpoint")

    def test_returns_run_tasks_checkpoint_when_tasks_manifest_missing(self) -> None:
        state = _base_state(adversarial_rounds=0)
        state["parallel_analysis"] = {"reviewed": True}
        stages = _stages(plan_exists=True)
        stages["plan"] = _base_stage_state(True, True, True)
        stages["tasks"] = _base_stage_state(True, False, False)
        action = NEXT_ACTION.recommended_next_action("banner-tasks", state, stages, True)
        self.assertEqual(action, "run_tasks_checkpoint")

    def test_returns_run_diff_checkpoint_when_diff_manifest_missing(self) -> None:
        state = _base_state(adversarial_rounds=0)
        state["parallel_analysis"] = {"reviewed": True}
        stages = _stages(plan_exists=True)
        stages["plan"] = _base_stage_state(True, True, True)
        stages["tasks"] = _base_stage_state(True, True, True)
        stages["diff"] = _base_stage_state(True, False, False)
        action = NEXT_ACTION.recommended_next_action("banner-diff", state, stages, True)
        self.assertEqual(action, "run_diff_checkpoint")

    def test_returns_run_closeout_checkpoint_when_closeout_unhealthy(self) -> None:
        state = _base_state(adversarial_rounds=0)
        state["parallel_analysis"] = {"reviewed": True}
        state["delivery"] = {
            "pr_url": "https://example.com/pr/1",
            "checks_summary": "pass",
            "head_mismatch": False,
            "merge_state_status": "clean",
        }
        stages = _stages(plan_exists=True)
        stages["plan"] = _base_stage_state(True, True, True)
        stages["tasks"] = _base_stage_state(True, True, True)
        stages["diff"] = _base_stage_state(True, True, True)
        stages["closeout"] = _base_stage_state(True, True, False)
        action = NEXT_ACTION.recommended_next_action("banner-closeout", state, stages, True)
        self.assertEqual(action, "run_closeout_checkpoint")


class NextActionLabelCoverageTests(unittest.TestCase):
    """Regression: NEXT_ACTION_LABELS must label the run_*_checkpoint actions.

    factory_emit does ``NEXT_ACTION_LABELS.get(action, action)``, so a missing
    key silently prints the raw action id (e.g. 'run_plan_checkpoint') instead
    of a readable label. The labels dict had drifted to stale
    'repair_*_checkpoint' keys while recommended_next_action returns 'run_*'.
    """

    CHECKPOINT_ACTIONS = (
        "run_spec_checkpoint",
        "run_plan_checkpoint",
        "run_tasks_checkpoint",
        "run_diff_checkpoint",
        "run_closeout_checkpoint",
    )

    def test_checkpoint_actions_have_readable_labels(self) -> None:
        labels = FACTORY_STAGES.NEXT_ACTION_LABELS
        for action in self.CHECKPOINT_ACTIONS:
            self.assertIn(action, labels, f"{action} missing from NEXT_ACTION_LABELS")
            self.assertNotEqual(labels[action], action, f"{action} label is the raw key")


if __name__ == "__main__":
    unittest.main()
