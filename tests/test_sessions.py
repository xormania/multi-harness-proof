"""Managed lifecycle tests use actual proof processes and only fake harnesses."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from behavior import run_scenario
from mhproof.capture import capture_claude
from mhproof.experiments import agent_settings
from mhproof.relay import write_json
from mhproof.reporting import summarize
from mhproof import sessions

ROOT = Path(__file__).resolve().parents[1]


def args(**values):
    return SimpleNamespace(**dict(dict(config=None, previous=None, dir=None, label=None, timeout=None,
                                      startup_timeout=None, no_work=False, detach=True), **values))


class ProcessTmux:
    """Simulate pane ownership/lifetime; launch real adapters on fake binaries."""
    def __init__(self, job, fail_peer=None, cancel=False):
        self.job, self.fail_peer, self.cancel = job, fail_peer, cancel
        self.processes, self.handles, self.closed = {}, {}, []

    def owned(self, record):
        return True

    def launch(self, record, peer, argv):
        if peer == self.fail_peer:
            raise RuntimeError("injected pane launch failure")
        out = (self.job / (peer + "-test.log")).open("w")
        self.handles[peer] = out
        self.processes[peer] = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT,
                                               stdin=subprocess.DEVNULL, start_new_session=True)
        if self.cancel:
            (self.job / "stop.request").touch()
        return peer

    def select(self, record, peer):
        pass

    def finish_peer(self, record, peer, pane, directory):
        child = self.processes[peer]
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
        self.handles[peer].close()
        self.closed.append(peer)
        return {"peer": peer, "status": "closed"}


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run = self.root / "run"
        self.run.mkdir()
        self.source = self.root / "native-session.jsonl"
        write_json(self.run / "claude-session.json", {"session_id": "native-session"})
        write_json(self.run / "run.json", {"tokens": {"claude": "proof-secret-value"}})

    def tearDown(self):
        self.temp.cleanup()

    def packet(self):
        return {"session_id": "native-session", "transcript_path": str(self.source)}

    def test_capture_redacts_and_keeps_native_source_untouched(self):
        raw = json.dumps({"sessionId": "native-session", "type": "user", "content": "proof-secret-value"}) + "\n"
        self.source.write_text(raw)
        result = capture_claude(self.run, self.packet())
        self.assertEqual(result["status"], "captured")
        self.assertEqual(self.source.read_text(), raw)
        self.assertNotIn("proof-secret-value", (self.run / "claude-transcript.jsonl").read_text())
        self.assertEqual((self.run / "claude-transcript.jsonl").stat().st_mode & 0o777, 0o600)

    def test_partial_native_write_is_explicit_and_completed_later(self):
        self.source.write_text('{"sessionId":"native-session"}\n{"type":')
        self.assertEqual(capture_claude(self.run, self.packet())["status"], "partial")
        self.source.write_text('{"sessionId":"native-session"}\n{"type":"system"}\n')
        result = capture_claude(self.run)
        self.assertEqual((result["status"], result["records"]), ("captured", 2))

    def test_unrelated_session_and_symlink_are_rejected(self):
        self.source.write_text('{"sessionId":"another-session"}\n')
        self.assertEqual(capture_claude(self.run, self.packet())["status"], "error")
        self.assertFalse((self.run / "claude-transcript.jsonl").exists())
        self.source.unlink()
        other = self.root / "other.jsonl"
        other.write_text('{}\n')
        self.source.symlink_to(other)
        self.assertEqual(capture_claude(self.run, self.packet())["status"], "error")

    def test_changed_hook_identity_cannot_select_a_transcript(self):
        self.source.write_text('{}\n')
        packet = {**self.packet(), "session_id": "other"}
        self.assertEqual(capture_claude(self.run, packet)["status"], "error")
        self.assertFalse((self.run / "claude-transcript-source.json").exists())


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="proof sessions space ")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def prepared(self, name="one"):
        return sessions.prepare(args(dir=str(self.root / name), timeout=4, startup_timeout=12))

    def fake_job(self, name="one"):
        job, settings, record = self.prepared(name)
        settings["mode"] = "mock"
        for peer in settings["peers"]:
            shim = job / ("fake-" + peer)
            shim.write_text(f"#!{sys.executable}\nimport runpy,sys\nsys.argv.insert(1, {peer!r})\n"
                            f"runpy.run_path({str(ROOT / 'tests/fake_harness.py')!r}, run_name='__main__')\n")
            shim.chmod(0o700)
            settings["agents"][peer]["binary"] = str(shim)
        write_json(job / "settings.json", settings)
        return job, record

    def test_saved_plan_defaults_and_rerun_preserve_fixture_and_overrides(self):
        job, settings, _ = self.prepared()
        self.assertEqual([settings["agents"][p]["model"] for p in settings["peers"]],
                         ["gpt-5.6-luna", "sonnet", "grok-4.5"])
        self.assertTrue(all(v["reasoning"] == "low" for v in settings["agents"].values()))
        original = (job / "plan.json").read_bytes()
        nxt, changed, _ = sessions.prepare(args(previous=str(job), dir=str(self.root / "two"),
                                               codex_reasoning="medium"))
        self.assertEqual((job / "plan.json").read_bytes(), original)
        self.assertEqual((nxt / "fixture.json").read_bytes(), (job / "fixture.json").read_bytes())
        self.assertEqual(changed["agents"]["codex"]["reasoning"], "medium")
        with self.assertRaises(FileExistsError):
            self.prepared()

    def test_automatic_names_are_fresh_and_labels_validated(self):
        cfg = self.root / "config.json"
        write_json(cfg, {"schema_version": 1, "mode": "live", "output_root": str(self.root / "runs")})
        a, _, _ = sessions.prepare(args(config=str(cfg), label="fixture"))
        b, _, _ = sessions.prepare(args(config=str(cfg), label="fixture"))
        self.assertNotEqual(a, b)
        self.assertTrue(a.name.startswith("fixture-"))
        with self.assertRaises(ValueError):
            sessions.prepare(args(config=str(cfg), label="../escape"))

    def test_supervisor_runs_real_adapters_collects_and_closes_only_its_peers(self):
        job, _ = self.fake_job()
        tmux = ProcessTmux(job)
        with redirect_stdout(io.StringIO()):
            result = sessions.supervise(job, tmux)
        self.assertEqual(result, 0, (job / "controller.log").read_text())
        self.assertEqual(set(tmux.closed), {"codex", "claude", "grok"})
        self.assertTrue(all(p.poll() is not None for p in tmux.processes.values()))
        summary = json.loads((job / "summary.json").read_text())
        self.assertEqual((summary["mode"], summary["status"], summary["message_passes"]), ("mock", "pass", 15))
        self.assertEqual(summary["transcript"]["status"], "captured")
        archives = list((job / "archives").glob("*.zip"))
        self.assertEqual(len(archives), 1)
        with zipfile.ZipFile(archives[0]) as archive:
            self.assertIn("diagnostics/claude-transcript.jsonl", archive.namelist())
            self.assertIn("diagnostics/controller.log", archive.namelist())
            self.assertNotIn("diagnostics/run.json", archive.namelist())
            self.assertNotIn("diagnostics/claude-transcript-source.json", archive.namelist())
        sessions.collect(job)
        self.assertEqual(len(list((job / "archives").glob("*.zip"))), 2)

    def test_partial_launch_failure_finalizes_and_cleans_up(self):
        job, _ = self.fake_job()
        tmux = ProcessTmux(job, fail_peer="claude")
        with redirect_stdout(io.StringIO()):
            result = sessions.supervise(job, tmux)
        self.assertEqual(result, 1)
        self.assertEqual(tmux.closed, ["codex"])
        self.assertTrue(list((job / "archives").glob("*.zip")))
        self.assertEqual(json.loads((job / "session.json").read_text())["state"], "error")

    def test_stop_request_preserves_unverified_report_and_archive(self):
        job, _ = self.fake_job()
        with redirect_stdout(io.StringIO()):
            result = sessions.supervise(job, ProcessTmux(job, cancel=True))
        self.assertEqual(result, 1)
        report = json.loads((job / "run/report.json").read_text())
        self.assertEqual(report["status"], "unverified")
        self.assertEqual(report["detail"], "Interrupted by operator")
        self.assertTrue(list((job / "archives").glob("*.zip")))

    def test_preflight_failure_is_archived_without_starting_harnesses(self):
        with patch.object(sessions, "preflight", return_value=(False, {"codex": {"binary": None}})), \
                patch.object(sessions.Tmux, "close"), redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError):
                sessions.start(args(dir=str(self.root / "failed")))
        job = self.root / "failed"
        self.assertFalse((job / "run/run.json").exists())
        self.assertTrue(list((job / "archives").glob("*.zip")))

    def test_cleanup_refuses_foreign_tmux_owner(self):
        tmux = sessions.Tmux()
        record = {"session": "mhproof-ours", "owner": "expected"}
        with patch.object(tmux, "call", return_value="someone-else") as call:
            tmux.close(record)
            self.assertFalse(any(args[0] == "kill-session" for args, _ in call.call_args_list))
            self.assertEqual(tmux.finish_peer(record, "claude", "%5", self.root)["status"],
                             "already-closed-or-unowned")

    def test_missing_tmux_does_not_mask_error_or_skip_collection(self):
        with patch.object(sessions, "preflight", side_effect=ValueError("tmux is not installed")), \
                patch.object(sessions.subprocess, "run", side_effect=FileNotFoundError("tmux")), \
                redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "tmux is not installed"):
                sessions.start(args(dir=str(self.root / "no-tmux")))
        job = self.root / "no-tmux"
        self.assertTrue(list((job / "archives").glob("*.zip")))
        self.assertEqual(json.loads((job / "summary.json").read_text())["status"], "incomplete")

    def test_controller_crash_before_report_is_incomplete_and_archived(self):
        job, _ = self.fake_job()
        write_json(job / "settings.json", {"mode": "mock"})
        with redirect_stdout(io.StringIO()):
            result = sessions.supervise(job, ProcessTmux(job))
        self.assertEqual(result, 1)
        summary = json.loads((job / "summary.json").read_text())
        self.assertEqual(summary["status"], "incomplete")
        self.assertFalse(summary["finished"])
        self.assertTrue(list((job / "archives").glob("*.zip")))

    def test_work_burst_failure_preserves_other_verified_exchanges(self):
        config = {"name": "dropped-claude-burst", "preset": "happy", "expect": "unverified", "faults": [
            {"peer": "claude", "on": "challenge", "case_prefix": "work-codex-to-claude-",
             "action": "drop", "occurrence": 1, "times": 2}]}
        result = run_scenario(self.root / "burst", "happy", scenario_config=config)
        self.assertEqual(result["status"], "matched", result)
        run = self.root / "burst/run"
        report = json.loads((run / "report.json").read_text())
        self.assertEqual(report["phase"], "work message verification")
        self.assertEqual(sum(c["status"] == "pass" for c in report["cases"]), 13)
        self.assertEqual(sum(w["status"] == "pass" for w in report["work"]), 3)
        self.assertTrue(all(len(w["scores"]) == 3 for w in report["work"]))


if __name__ == "__main__":
    unittest.main()
