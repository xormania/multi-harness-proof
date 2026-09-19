import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from mhproof.adapters import Codex, Grok
from mhproof.contract import validate_tool
from mhproof.relay import Client, State, serve, write_json
from mhproof.rpc import RPC, RPCError
from mhproof.suite import Suite, case_evidence
from mhproof.telemetry import Redactor, Trace, bundle
from mhproof.workload import expected

ROOT = Path(__file__).resolve().parents[1]


class RelayFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.state = State(self.directory, ["codex", "claude", "grok"], hold_timeout=3)
        self.server = serve(self.state)
        write_json(self.directory / "run.json", {"tokens": self.state.tokens,
                   "url": f"http://127.0.0.1:{self.server.server_address[1]}", "timeout": 5})
        self.clients = {p: Client(self.directory, p) for p in self.state.peers}

    def tearDown(self):
        self.state.close()
        self.server.shutdown()
        self.server.server_close()
        self.state.log.close()
        self.temp.cleanup()

    def test_identity_bound_to_credential_and_no_cross_inbox(self):
        c = self.clients["codex"]
        c.post("/register", {"session_id": "c1"})
        with self.assertRaises(HTTPError):
            c.post("/register", {"session_id": "changed"})
        c.tool("proof_send", {"to": "grok", "kind": "challenge", "case_id": "test", "nonce": "n", "memory": ""})
        msg = self.clients["grok"].post("/poll", {})["message"]
        self.assertEqual(msg["sender"], "codex")
        self.assertIsNone(self.clients["claude"].post("/poll", {})["message"])
        with self.assertRaises(HTTPError):
            c.post("/event", {"event": "submitted", "peer": "grok"})
        c.token = "invalid"
        with self.assertRaises(HTTPError):
            c.post("/poll", {})

    def test_validator_rejects_sender_override(self):
        with self.assertRaises(ValueError):
            validate_tool("proof_send", {"sender": "grok"})

    def test_work_fixture_grades_once_and_does_not_return_answers(self):
        client = self.clients["grok"]
        client.post("/register", {"session_id": "work-session"})
        batch = client.tool("proof_work_next", {})
        with self.assertRaises(HTTPError):
            client.tool("proof_work_next", {})
        result = client.tool("proof_work_submit", {"batch": batch["batch"], "failed_jobs": [], "failed_tests": 0})
        self.assertNotIn("expected", result)
        self.assertFalse(next(e for e in self.state.snapshot() if e["event"] == "work_scored")["correct"])
        with self.assertRaises(HTTPError):
            client.tool("proof_work_submit", {"batch": batch["batch"], "failed_jobs": [], "failed_tests": 0})

    def test_trace_and_bundle_exclude_credentials(self):
        with patch.dict(os.environ, {"XAI_API_KEY": "example-test-api-key"}):
            trace = Trace(self.directory, "grok")
            trace.record("rpc_in", {"access_token": "new-server-token", "text": "example-test-api-key",
                                    "nested": {"Authorization": "Bearer another-token"}, "nonce": "keep-this"})
            trace.close()
            write_json(self.directory / "report.json", {"status": "unverified"})
            (self.directory / "claude-mcp.json").write_text("DO NOT INCLUDE")
            (self.directory / "claude-debug.log").write_text("example-test-api-key " + self.state.tokens["grok"])
            archive_path = bundle(self.directory, self.directory / "diag.zip")
            import zipfile
            with zipfile.ZipFile(archive_path) as archive:
                self.assertNotIn("diagnostics/run.json", archive.namelist())
                self.assertNotIn("diagnostics/claude-mcp.json", archive.namelist())
                data = "\n".join(archive.read(name).decode() for name in archive.namelist())
                for secret in ["new-server-token", "example-test-api-key", self.state.tokens["grok"], "another-token"]:
                    self.assertNotIn(secret, data)
                self.assertIn("keep-this", data)
            with self.assertRaises(FileExistsError):
                bundle(self.directory, archive_path)

    def test_mcp_channel_keeps_flowing_during_held_tool(self):
        self.clients["claude"].post("/register", {"session_id": "cl1"})
        proc = subprocess.Popen([sys.executable, str(ROOT / "proof.py"), "mcp", "--dir", str(self.directory),
                                 "--peer", "claude", "--channel"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        import queue
        packets = queue.Queue()
        thread = threading.Thread(target=lambda: [packets.put(json.loads(line)) for line in proc.stdout], daemon=True)
        thread.start()
        def send(packet):
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", **packet}) + "\n")
            proc.stdin.flush()
        try:
            send({"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
            init = packets.get(timeout=3)
            self.assertIn("claude/channel", init["result"]["capabilities"]["experimental"])
            send({"method": "notifications/initialized"})
            send({"id": 2, "method": "tools/call", "params": {"name": "proof_hold", "arguments": {"case_id": "busy"}}})
            deadline = time.monotonic() + 3
            while not any(e["event"] == "hold_started" for e in self.state.snapshot()):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.02)
            self.state.enqueue("codex", "claude", kind="challenge", case_id="busy", nonce="n", memory="")
            event = packets.get(timeout=3)
            self.assertEqual(event["method"], "notifications/claude/channel")
            self.state.release("claude", "busy")
            self.assertEqual(packets.get(timeout=3)["id"], 2)
        finally:
            proc.stdin.close()
            proc.wait(timeout=5)
            proc.stdout.close()
            proc.stderr.close()
            thread.join(timeout=1)

    def test_all_three_adapter_paths_and_busy_delivery_offline(self):
        """Real relay, real MCP, real adapters; fake model/harness processes."""
        children = []
        logs = []
        for peer in self.state.peers:
            shim = self.directory / ("fake-" + peer)
            shim.write_text(f"#!{sys.executable}\nimport runpy,sys\nsys.argv.insert(1, {peer!r})\n"
                            f"runpy.run_path({str(ROOT / 'tests/fake_harness.py')!r}, run_name='__main__')\n")
            shim.chmod(0o700)
            log = (self.directory / (peer + "-test.log")).open("w+")
            logs.append(log)
            children.append(subprocess.Popen([sys.executable, str(ROOT / "proof.py"), "agent", peer,
                "--dir", str(self.directory), "--binary", str(shim)], stdout=log, stderr=log,
                start_new_session=True))
        try:
            suite = Suite(self.state, timeout=8, startup_timeout=10)
            # No arbitrary two-second quiet delay is needed in deterministic fixtures.
            real_sleep = time.sleep
            with patch("mhproof.suite.time.sleep", wraps=real_sleep) as sleeper:
                sleeper.side_effect = lambda seconds: real_sleep(min(seconds, 0.03))
                suite.run()
            self.assertEqual(len(suite.results), 15)
            self.assertTrue(all(r["status"] == "pass" for r in suite.results))
            self.assertTrue(all(r["status"] == "pass" for r in suite.work_results))
            events = self.state.snapshot()
            self.assertEqual(len([e for e in events if e["event"] == "registered"]), 3)
            for peer in self.state.peers:
                self.assertEqual(len({e["session_id"] for e in events if e["peer"] == peer and "session_id" in e}), 1)
        except Exception:
            for log in logs:
                log.seek(0)
                print(log.read(), file=sys.stderr)
            raise
        finally:
            self.state.close()
            import signal
            for child in children:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            for child in children:
                child.wait(timeout=5)
            for log in logs:
                log.close()


class VerifierTests(unittest.TestCase):
    def test_work_oracle_ignores_stale_attempts_and_warnings(self):
        rows = [{"job": "a", "attempt": 2, "status": "PASS", "failed_tests": 0},
                {"job": "a", "attempt": 1, "status": "FAIL", "failed_tests": 10},
                {"job": "b", "attempt": 1, "status": "WARN", "failed_tests": 5},
                {"job": "c", "attempt": 1, "status": "FAIL", "failed_tests": 2}]
        self.assertEqual(expected(rows), {"failed_jobs": ["c"], "failed_tests": 2})

    def fixture(self):
        sessions = {"codex": {"session_id": "c"}, "grok": {"session_id": "g"}}
        events = [
            {"event": "queued", "peer": "codex", "message": {"id": "q", "to": "grok", "case_id": "t", "kind": "challenge", "nonce": "n", "memory": ""}},
            {"event": "submitted", "peer": "grok", "message_id": "q"},
            {"event": "queued", "peer": "grok", "message": {"id": "r", "to": "codex", "case_id": "t", "kind": "reply", "nonce": "n", "memory": "secret"}},
            {"event": "submitted", "peer": "codex", "message_id": "r"},
            {"event": "report", "peer": "codex", "report": {"phase": "received", "case_id": "t", "nonce": "n", "memory": "secret", "peer": "grok"}},
        ]
        for i, e in enumerate(events):
            e.update(seq=i + 1, time=i, session_id=sessions[e["peer"]]["session_id"])
        return events, sessions

    def check(self, events, sessions, busy=False):
        return case_evidence(events, "codex", "grok", "t", "n", "secret", sessions, busy)

    def test_requires_full_round_trip(self):
        events, sessions = self.fixture()
        self.assertIsNotNone(self.check(events, sessions))
        for i in range(len(events)):
            self.assertIsNone(self.check(events[:i] + events[i + 1:], sessions))

    def test_nonce_memory_and_session_must_match(self):
        for key, value in [("nonce", "stale"), ("memory", "forgotten")]:
            events, sessions = self.fixture()
            events[2]["message"][key] = value
            self.assertIsNone(self.check(events, sessions))
        events, sessions = self.fixture()
        events[2]["session_id"] = "new-session"
        self.assertIsNone(self.check(events, sessions))

    def test_busy_requires_observed_overlap(self):
        events, sessions = self.fixture()
        self.assertIsNone(self.check(events, sessions, True))
        start = {"event": "hold_started", "peer": "grok", "case_id": "t", "seq": 0}
        finish = {"event": "hold_finished", "peer": "grok", "case_id": "t", "seq": 6, "released": True}
        self.assertIsNotNone(self.check([start, *events, finish], sessions, True))
        finish["seq"] = 1
        self.assertIsNone(self.check([start, finish, *events], sessions, True))


class RPCTests(unittest.IsolatedAsyncioTestCase):
    async def test_eof_and_rpc_errors_are_not_success(self):
        async def handler(*args):
            return {}
        proc = await asyncio.create_subprocess_exec(sys.executable, "-c",
            'import sys,json; m=json.loads(sys.stdin.readline()); print(json.dumps({"id":m["id"],"error":{"code":-32601,"message":"missing"}}),flush=True)',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        rpc = RPC(proc, handler, handler)
        with self.assertRaises(RPCError) as error:
            await rpc.request("missing", {})
        self.assertEqual(error.exception.code, -32601)
        await rpc.dead.wait()
        with self.assertRaises(ConnectionError):
            await rpc.request("after-eof", {})
        await rpc.close()


if __name__ == "__main__":
    unittest.main()
