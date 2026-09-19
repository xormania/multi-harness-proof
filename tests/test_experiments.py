import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mhproof.cli import main
from mhproof.experiments import compare, history, load_config, run_experiment
from mhproof.workload import FIXTURE, read_fixture


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = self.directory / "proof.json"
        self.raw = {"schema_version": 1, "name": "regression", "mode": "mock", "output_root": "results",
                    "timeout": 4, "checks": {"round_trip_pairs": [["codex", "claude"], ["codex", "grok"]],
                        "busy_pairs": [], "work": True, "burst_per_peer": 3}}

    def write(self, value=None):
        self.config.write_text(json.dumps(self.raw if value is None else value))

    def test_rejects_unknown_keys_duplicates_and_invalid_faults_before_launch(self):
        for update in ({"surprise": True}, {"timeout": True}, {"mode": "other"},
                       {"checks": {"work": False, "round_trip_pairs": [], "busy_pairs": []}},
                       {"scenarios": [{"name": "bad", "faults": [{"peer": "grok", "on": "challenge", "action": "shell"}]}]}):
            self.write({**self.raw, **update})
            with self.subTest(update=update), self.assertRaises(ValueError):
                load_config(self.config)
        self.config.write_text('{"schema_version":1,"mode":"mock","mode":"live"}')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            load_config(self.config)
        self.assertFalse((self.directory / "results").exists())

    def test_paths_defaults_and_null_model_override_are_resolved(self):
        self.raw["agents"] = {"codex": {"model": None, "reasoning": None, "binary": "bin/codex"}}
        self.write()
        _, resolved = load_config(self.config)
        self.assertEqual(resolved["agents"]["codex"]["binary"], str(self.directory / "bin/codex"))
        self.assertIsNone(resolved["agents"]["codex"]["model"])
        self.assertIsNone(resolved["agents"]["codex"]["reasoning"])
        self.assertEqual(resolved["output_root"], str(self.directory / "results"))

    def test_two_peer_live_plan_is_resolved_without_launching(self):
        self.write({"schema_version": 1, "mode": "live", "peers": ["codex", "grok"], "checks": {"work": False}})
        _, resolved = load_config(self.config)
        self.assertEqual(len(resolved["checks"]["round_trip_pairs"]), 2)
        self.assertEqual(resolved["timeout"], 180)
        self.assertFalse(resolved["scenarios"])

    def test_fixture_rejects_ambiguous_attempts_and_missing_overlap(self):
        value = json.loads(FIXTURE.read_text())
        bad = self.directory / "fixture.json"
        value["batches"][0].append(copy.deepcopy(value["batches"][0][0]))
        bad.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            read_fixture(bad)
        bad.write_text(json.dumps({"batches": [[]]}))
        with self.assertRaisesRegex(ValueError, "at least two"):
            read_fixture(bad)

    def test_agent_uses_snapshot_and_explicit_flags_are_recordable_overrides(self):
        (self.directory / "run.json").write_text(json.dumps({"agent_settings": {"codex": {
            "profile": "existing", "binary": "custom-codex", "model": "test-model", "reasoning": "low"}}}))
        with patch("mhproof.cli.run_agent", return_value=0) as launch:
            self.assertEqual(main(["agent", "codex", "--dir", str(self.directory)]), 0)
            self.assertEqual(launch.call_args.args[2:], ("custom-codex", "test-model", "low"))
            main(["agent", "codex", "--dir", str(self.directory), "--model", "override", "--reasoning", "high"])
            self.assertEqual(launch.call_args.args[2:], ("custom-codex", "override", "high"))

    def test_configured_runs_keep_snapshots_and_compare_changed_coordination(self):
        self.raw["scenarios"] = [{"name": "baseline", "expect": "pass", "faults": [
            {"peer": "claude", "on": "challenge", "action": "delay", "seconds": 0.02}]}]
        self.write()
        self.assertEqual(run_experiment(self.config), 0)
        first = next((self.directory / "results").iterdir())
        original = (first / "experiment.json").read_bytes()
        report = json.loads((first / "baseline/run/report.json").read_text())
        self.assertEqual(report["cases_planned"], 11)
        self.assertEqual(report["status"], "pass")
        self.raw["checks"]["burst_per_peer"] = 2
        self.raw["scenarios"] = [{"name": "drop-reply", "expect": "unverified", "faults": [
            {"peer": "codex", "on": "reply", "case_prefix": "round-trip-codex-to-grok", "action": "drop"}]}]
        self.write()
        self.assertEqual(run_experiment(self.config), 0)  # Expected failure, never a passing proof.
        second = next(p for p in (self.directory / "results").iterdir() if p != first)
        self.assertEqual((first / "experiment.json").read_bytes(), original)
        rows = history(self.directory / "results")
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["reports"][0]["status"] for r in rows}, {"pass", "unverified"})
        diff = compare(first, second)
        self.assertTrue(any(c["setting"] == "settings.checks.burst_per_peer" for c in diff["changes"]))
        failed = json.loads((second / "drop-reply/run/report.json").read_text())
        self.assertEqual(failed["cases"][0]["status"], "pass")
        self.assertEqual(failed["cases"][-1]["status"], "unverified")


if __name__ == "__main__":
    unittest.main()
