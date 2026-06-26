import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_state as FACTORY_STATE  # noqa: E402
import factory_telemetry as TELEMETRY  # noqa: E402

SLUG = "claude-telemetry-test"


class TokensFromSessionJsonlTests(unittest.TestCase):
    def test_sums_usage_fields(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "agent-x.jsonl"
            lines = [
                {"message": {"usage": {
                    "input_tokens": 100,
                    "cache_creation_input_tokens": 20,
                    "cache_read_input_tokens": 500,
                    "output_tokens": 40,
                }}},
                {"message": {"usage": {"input_tokens": 10, "output_tokens": 5}}},
                {"type": "noise-without-usage"},
                {},
            ]
            path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
            totals = TELEMETRY.tokens_from_session_jsonl([path])
            self.assertEqual(totals["input_tokens"], 130)  # 100 + 20 + 10
            self.assertEqual(totals["cache_read_tokens"], 500)
            self.assertEqual(totals["output_tokens"], 45)

    def test_missing_file_raises(self) -> None:
        with self.assertRaises((FileNotFoundError, OSError)):
            TELEMETRY.tokens_from_session_jsonl(["/no/such/agent.jsonl"])


class RecordReviewUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", Path(self._tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        root = FACTORY_STATE.workflow_dir(SLUG)
        root.mkdir(parents=True, exist_ok=True)
        state = FACTORY_STATE._default_workflow_state()
        state["schema_version"] = 2
        FACTORY_STATE.atomic_json_write(FACTORY_STATE.factory_state_path(SLUG), state)

    def test_appends_record_with_cost(self) -> None:
        TELEMETRY.record_review_usage(
            SLUG, "spec", 1, "adversarial_review", "claude-opus-4-8",
            lens="requirements-adversarial",
            input_tokens=1200, output_tokens=300, cache_read_tokens=900,
        )
        usage = FACTORY_STATE.load_workflow_state(SLUG)["token_usage"]
        self.assertEqual(len(usage), 1)
        rec = usage[0]
        self.assertEqual(rec["stage"], "spec")
        self.assertEqual(rec["round"], 1)
        self.assertEqual(rec["lens"], "requirements-adversarial")
        self.assertEqual(rec["model"], "claude-opus-4-8")
        self.assertEqual(rec["activity_type"], "adversarial_review")
        self.assertEqual(rec["input_tokens"], 1200)
        self.assertEqual(rec["output_tokens"], 300)
        self.assertEqual(rec["cache_read_tokens"], 900)
        self.assertIsNotNone(rec["cost_usd_estimate"])

    def test_parse_error_recorded_when_no_tokens(self) -> None:
        TELEMETRY.record_review_usage(
            SLUG, "spec", 0, "adversarial_review", "claude-opus-4-8",
            lens="x", parse_error="no session JSONL provided",
        )
        rec = FACTORY_STATE.load_workflow_state(SLUG)["token_usage"][-1]
        self.assertIsNone(rec["input_tokens"])
        self.assertIsNone(rec["output_tokens"])
        self.assertEqual(rec["parse_error"], "no session JSONL provided")
        self.assertIsNone(rec["cost_usd_estimate"])

    def test_invalid_activity_type_raises(self) -> None:
        with self.assertRaises(ValueError):
            TELEMETRY.record_review_usage(SLUG, "spec", 1, "bogus", "claude-opus-4-8")


if __name__ == "__main__":
    unittest.main()
