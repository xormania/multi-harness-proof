import asyncio
import copy
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
from unittest.mock import AsyncMock
from types import SimpleNamespace
from urllib.error import HTTPError

from mhproof.adapters import Codex, Grok
from mhproof.contract import validate_tool
from mhproof.compat import ClaudeChannelLog, supports_tool_output
from mhproof.evidence import claude_hook, grok_tool_identity, grok_tool_observation, tool_audit
from mhproof.relay import Client, State, serve, write_json
from mhproof.rpc import RPC, RPCError
from mhproof.suite import Suite, case_diagnostics, case_evidence
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

    def test_work_requires_controller_release_before_later_batches(self):
        client = self.clients["grok"]
        client.post("/register", {"session_id": "work-session"})
        batch = client.tool("proof_work_next", {})
        client.tool("proof_work_submit", {"batch": 0, **expected(batch["rows"])})
        with self.assertRaises(HTTPError):
            client.tool("proof_work_next", {})
        self.assertFalse(any(e["event"] == "work_completed" for e in self.state.snapshot()))
        with self.state.lock:
            self.state.workload.release_batch(1)
        self.assertEqual(client.tool("proof_work_next", {})["batch"], 1)

    def test_trace_and_bundle_exclude_credentials(self):
        with patch.dict(os.environ, {"XAI_API_KEY": "example-test-api-key"}):
            trace = Trace(self.directory, "grok")
            trace.record("rpc_in", {"access_token": "new-server-token", "text": "example-test-api-key",
                                    "nested": {"Authorization": "Bearer another-token"}, "nonce": "keep-this"})
            trace.close()
            write_json(self.directory / "report.json", {"status": "unverified"})
            (self.directory / "claude-mcp.json").write_text("DO NOT INCLUDE")
            (self.directory / "claude-settings.json").write_text("DO NOT INCLUDE EITHER")
            (self.directory / "claude-debug.log").write_text("example-test-api-key " + self.state.tokens["grok"])
            archive_path = bundle(self.directory, self.directory / "diag.zip")
            import zipfile
            with zipfile.ZipFile(archive_path) as archive:
                self.assertNotIn("diagnostics/run.json", archive.namelist())
                self.assertNotIn("diagnostics/claude-mcp.json", archive.namelist())
                self.assertNotIn("diagnostics/claude-settings.json", archive.namelist())
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
                native = [e for e in events if e["peer"] == peer and e["event"] == "native_tool"]
                self.assertTrue(native)
                self.assertEqual({e["native_id"] for e in native}, {self.state.sessions[peer]["session_id"]})
            self.assertEqual(tool_audit(events, self.state.sessions)["status"], "pass")
            barrier = next(e["seq"] for e in events if e["event"] == "work_burst_submitted")
            self.assertTrue(all(e["seq"] > barrier for e in events
                                if e["event"] == "work_batch_issued" and e["batch"] > 0))
            self.assertTrue(any(e["event"] == "submitted" and e.get("transport") == "grok/_x.ai/interject" for e in events))
            claude_busy = next(r for r in suite.results if r["check"] == "busy" and r["target"] == "claude")
            self.assertFalse(claude_busy["timing"]["reply_in_hold_turn"])
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
            e.update(seq=i + 1, time=i, registered_session_id=sessions[e["peer"]]["session_id"])
        for index in (0, 2, 4):
            event = events[index]
            name = "proof_send" if index < 4 else "proof_report"
            args = ({k: event["message"][k] for k in ("to", "kind", "case_id", "nonce", "memory")}
                    if index < 4 else event["report"])
            events.append({"event": "native_tool", "peer": event["peer"], "tool": name,
                           "arguments": copy.deepcopy(args), "native_id": sessions[event["peer"]]["session_id"],
                           "origin": "codex/item/tool/call" if event["peer"] == "codex" else "grok/session/update",
                           "call_id": str(index), "turn_id": "reply-turn", "seq": index + 0.5})
        return events, sessions

    def check(self, events, sessions, busy=False):
        return case_evidence(events, "codex", "grok", "t", "n", "secret", sessions, busy)

    def test_requires_full_round_trip(self):
        events, sessions = self.fixture()
        self.assertIsNotNone(self.check(events, sessions))
        for i in range(len(events)):
            self.assertIsNone(self.check(events[:i] + events[i + 1:], sessions))

    def test_submission_cannot_precede_queue_but_ack_can_follow_reply(self):
        events, sessions = self.fixture()
        events[1]["seq"] = 0
        self.assertIsNone(self.check(events, sessions))
        events[1]["seq"] = 10
        events[3]["seq"] = 11
        result = self.check(events, sessions)
        self.assertTrue(result["timing"]["challenge_ack_after_reply"])
        self.assertTrue(result["timing"]["reply_ack_after_receipt"])

    def test_nonce_memory_and_session_must_match(self):
        for key, value in [("nonce", "stale"), ("memory", "forgotten")]:
            events, sessions = self.fixture()
            events[2]["message"][key] = value
            self.assertIsNone(self.check(events, sessions))
        events, sessions = self.fixture()
        events[-2]["native_id"] = "new-session"
        self.assertIsNone(self.check(events, sessions))

    def test_diagnostics_distinguish_wrong_context_from_missing_delivery(self):
        events, _ = self.fixture()
        events[2]["message"]["memory"] = "wrong"
        diagnostic = case_diagnostics(events, {"source": "codex", "target": "grok", "case_id": "t", "nonce": "n"}, "secret")
        self.assertEqual(diagnostic["challenge_submission_seqs"], [2])
        self.assertFalse(diagnostic["reply_observations"][0]["memory_matches"])
        self.assertTrue(diagnostic["reply_observations"][0]["nonce_matches"])

    def test_busy_requires_observed_overlap(self):
        events, sessions = self.fixture()
        self.assertIsNone(self.check(events, sessions, True))
        start = {"event": "hold_started", "peer": "grok", "case_id": "t", "seq": 0}
        finish = {"event": "hold_finished", "peer": "grok", "case_id": "t", "seq": 6, "released": True}
        events.append({"event": "native_tool", "peer": "grok", "tool": "proof_hold", "arguments": {"case_id": "t"},
                       "native_id": "g", "origin": "grok/session/update", "call_id": "held", "seq": -1,
                       "turn_id": "hold-turn"})
        self.assertIsNotNone(self.check([start, *events, finish], sessions, True))
        evidence = self.check([start, *events, finish], sessions, True)
        self.assertTrue(evidence["timing"]["reply_before_hold_finished"])
        self.assertFalse(evidence["timing"]["reply_in_hold_turn"])
        finish["seq"] = 1
        self.assertIsNone(self.check([start, finish, *events], sessions, True))

    def test_registered_labels_without_native_observations_cannot_pass(self):
        events, sessions = self.fixture()
        self.assertIsNone(self.check(events[:5], sessions))

    def test_audit_requires_independent_observation_for_every_call(self):
        events, sessions = self.fixture()
        observed = events[-1]
        called = {"event": "tool_called", "peer": observed["peer"], "tool": observed["tool"],
                  "arguments": observed["arguments"], "seq": 10}
        self.assertEqual(tool_audit([*events, called], sessions)["status"], "pass")
        self.assertEqual(tool_audit([*events, called, called, observed], sessions)["unmatched_relay_calls"], 1)

    def test_conflicting_call_id_invalidates_case_and_audit(self):
        events, sessions = self.fixture()
        conflict = {**events[-1], "arguments": {"unexpected": "different call"}, "seq": 99}
        self.assertIsNone(self.check([*events, conflict], sessions))
        self.assertEqual(tool_audit([*events, conflict], sessions)["conflicting_call_seqs"], [99])

    def test_discovery_cannot_replace_native_proof_evidence(self):
        events, sessions = self.fixture()
        observed = events[-2]
        events.append({"event": "tool_called", "peer": "grok", "tool": observed["tool"],
                       "arguments": observed["arguments"], "seq": 10})
        observed.update(event="native_discovery", tool="search_tool", arguments={"query": "coord_proof"})
        self.assertIsNone(self.check(events, sessions))
        audit = tool_audit(events, sessions)
        self.assertEqual(audit["status"], "unverified")
        self.assertEqual(audit["native_discovery_calls"], 1)
        self.assertEqual(audit["unmatched_relay_calls"], 1)

    def test_discovery_call_id_cannot_be_reused_as_proof_evidence(self):
        events, sessions = self.fixture()
        discovery = {**events[-2], "event": "native_discovery", "tool": "search_tool",
                     "arguments": {"query": "coord_proof"}, "seq": 99}
        for ordered in ([*events, discovery], [discovery, *events]):
            with self.subTest(discovery_first=ordered[0] is discovery):
                self.assertIsNone(self.check(ordered, sessions))
                self.assertTrue(tool_audit(ordered, sessions)["conflicting_call_seqs"])

    def test_changed_outer_dispatch_arguments_conflict_even_with_same_inner_call(self):
        events, sessions = self.fixture()
        observed = events[-2]
        observed.update(wire_tool="use_tool", wire_arguments={
            "tool_name": "coord_proof__" + observed["tool"], "tool_input": observed["arguments"]})
        conflict = {**observed, "wire_arguments": {"tool_name": "other_server__proof_send",
                    "tool_input": observed["arguments"]}, "seq": 99}
        self.assertIsNone(self.check([*events, conflict], sessions))
        self.assertEqual(tool_audit([*events, conflict], sessions)["conflicting_call_seqs"], [99])

    def test_grok_identity_prefers_versioned_metadata_and_never_guesses_prose(self):
        meta = {"version": 1, "name": "coord_proof__proof_send", "namespace": "mcp"}
        packet = {"title": "Send the reply", "_meta": {"x.ai/tool": meta}}
        self.assertEqual(grok_tool_identity(packet)[0], "coord_proof__proof_send")
        self.assertEqual(grok_tool_identity({"title": "coord_proof__proof_send"})[0], "coord_proof__proof_send")
        for change in ({"version": 2}, {"version": True}, {"namespace": "grok_build"},
                       {"name": "other_server__proof_send"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                grok_tool_identity({**packet, "_meta": {"x.ai/tool": {**meta, **change}}})
        with self.assertRaises(ValueError):
            grok_tool_identity({**packet, "title": "coord_proof__proof_hold"})

    def test_schema_probe_rejects_missing_or_unrelated_tool_output(self):
        old = {"definitions": {"TurnStartParams": {"properties": {"input": {}, "threadId": {}}}}}
        self.assertFalse(supports_tool_output([old, {"properties": {"toolOutput": {}}}]))
        old["definitions"]["TurnStartParams"]["properties"]["toolOutput"] = {}
        self.assertTrue(supports_tool_output([old]))
        self.assertIsNone(supports_tool_output([{"properties": {"toolOutput": {}}}]))


class ClaudeChannelLogTests(unittest.TestCase):
    marker = b'2026-09-19T22:46:26.177Z [DEBUG] MCP server "coord_proof": Channel notifications registered\n'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "claude-debug.log"
        self.reader = ClaudeChannelLog(self.path)

    def test_missing_file_and_partial_line_do_not_release_gate(self):
        self.assertIsNone(self.reader.poll())
        self.path.write_bytes(self.marker[:-1])
        self.assertIsNone(self.reader.poll())
        with self.path.open("ab") as stream:
            stream.write(b"\n")
        observed = self.reader.poll()
        self.assertEqual(observed["source_line"], 1)
        self.assertEqual(observed["native_timestamp"], "2026-09-19T22:46:26.177Z")
        self.assertEqual(self.reader.poll(), observed)

    def test_other_server_quoted_content_and_connection_are_not_registration(self):
        lines = [self.marker.replace(b'"coord_proof"', b'"other_server"'),
                 b'quoted: ' + self.marker, self.marker.replace(b'[DEBUG]', b'[INFO]'),
                 self.marker.replace(b'Channel notifications registered', b'Successfully connected'),
                 self.marker.replace(b'Channel notifications registered', b'future ready signal')]
        self.path.write_bytes(b''.join(lines))
        self.assertIsNone(self.reader.poll())
        with self.path.open("ab") as stream:
            stream.write(self.marker)
        self.assertEqual(self.reader.poll()["source_line"], 6)

    def test_oversized_partial_line_cannot_supply_a_marker_suffix(self):
        self.path.write_bytes(b'x' * 140000 + self.marker + self.marker)
        observed = None
        for _ in range(4):
            observed = self.reader.poll()
            self.assertLessEqual(len(self.reader.pending), 65536)
            if observed:
                break
        self.assertEqual(observed["source_line"], 2)

    def test_log_truncation_is_explicit_failure(self):
        self.path.write_bytes(b'unrelated line\n')
        self.assertIsNone(self.reader.poll())
        self.path.write_bytes(b'')
        with self.assertRaisesRegex(ValueError, "truncated"):
            self.reader.poll()


class GrokToolContractTests(unittest.TestCase):
    @staticmethod
    def packet(name, args, canonical=True):
        update = {"sessionUpdate": "tool_call", "toolCallId": "call", "title": name, "rawInput": args}
        if canonical:
            update["_meta"] = {"x.ai/tool": {"version": 1, "name": name,
                "namespace": "grok_build", "kind": name, "read_only": False}}
        return update

    def test_catalog_discovery_with_canonical_or_exact_title_identity(self):
        for canonical in (True, False):
            for args in ({"query": "coord_proof"}, {"query": "proof tools", "limit": 5},
                         {"query": "coord_proof", "limit": None}):
                with self.subTest(canonical=canonical, args=args):
                    observation = grok_tool_observation(self.packet("search_tool", args, canonical))
                    self.assertEqual(observation["event"], "native_discovery")
                    self.assertEqual(observation["arguments"], args)
                    self.assertEqual(observation["wire_tool"], "search_tool")

    def test_discovery_rejects_malformed_inputs(self):
        for args in (None, [], {}, {"query": 1}, {"query": "coord_proof", "url": "unexpected"},
                     {"query": "coord_proof", "limit": True}, {"query": "coord_proof", "limit": -1},
                     {"query": "coord_proof", "limit": 256}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                grok_tool_observation(self.packet("search_tool", args))

    def test_dispatch_preserves_wire_input_and_ignores_metadata_projection(self):
        args = {"tool_name": "coord_proof__proof_hold", "tool_input": {"case_id": "case"}}
        for canonical in (True, False):
            update = self.packet("use_tool", args, canonical)
            if canonical:
                update["title"] = "Hold for a coordination message"
                update["_meta"]["x.ai/tool"]["input"] = {"case_id": "display-only"}
            observation = grok_tool_observation(update)
            self.assertEqual(observation["event"], "native_tool")
            self.assertEqual(observation["tool"], "proof_hold")
            self.assertEqual(observation["arguments"], {"case_id": "case"})
            self.assertEqual(observation["wire_tool"], "use_tool")
            self.assertEqual(observation["wire_arguments"], args)

    def test_dispatch_rejects_unqualified_other_server_and_nonproof_targets(self):
        for target in ("proof_hold", "mcp__coord_proof__proof_hold", "other_server__proof_hold",
                       "coord_proof__read_file", "coord_proof__proof_unknown", "read_file", "use_tool", None, {}):
            with self.subTest(target=target), self.assertRaises(ValueError):
                grok_tool_observation(self.packet("use_tool", {"tool_name": target,
                    "tool_input": {"case_id": "case"}}))

    def test_dispatch_requires_actual_schema_valid_object_arguments(self):
        for args in (None, {}, {"tool_name": "coord_proof__proof_hold"},
                     {"tool_name": "coord_proof__proof_hold", "tool_input": '{"case_id":"case"}'},
                     {"tool_name": "coord_proof__proof_hold", "tool_input": {}},
                     {"tool_name": "coord_proof__proof_hold", "tool_input": {"case_id": 2}},
                     {"tool_name": "coord_proof__proof_hold", "tool_input": {"case_id": "case"}, "extra": True}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                grok_tool_observation(self.packet("use_tool", args))

    def test_machinery_metadata_and_exact_titles_must_agree(self):
        for name, args in (("search_tool", {"query": "coord_proof"}),
                           ("use_tool", {"tool_name": "coord_proof__proof_work_next", "tool_input": {}})):
            for change in ({"namespace": "mcp"}, {"namespace": "other"}, {"version": 2}, {"version": True}):
                update = self.packet(name, args)
                update["_meta"]["x.ai/tool"].update(change)
                with self.subTest(name=name, change=change), self.assertRaises(ValueError):
                    grok_tool_observation(update)
            for title in ({"search_tool", "use_tool", "coord_proof__proof_hold"} - {name}):
                update = self.packet(name, args)
                update["title"] = title
                with self.subTest(name=name, title=title), self.assertRaises(ValueError):
                    grok_tool_observation(update)

    def test_no_prose_guessing_or_other_builtin_exception(self):
        for name in ("Search for proof tools", "read_file", "web_search", "run_shell_command"):
            for canonical in (True, False):
                with self.subTest(name=name, canonical=canonical), self.assertRaises(ValueError):
                    grok_tool_observation(self.packet(name, {"query": "coord_proof"}, canonical))


class AdapterEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        write_json(self.directory / "run.json", {"tokens": {"grok": "test-only"}})
        self.events = []
        def event(kind, **fields):
            self.events.append({"event": kind, "seq": len(self.events) + 1, **fields})
        self.client = SimpleNamespace(directory=self.directory, peer="grok", config={"timeout": 180}, event=event)
        self.adapter = Grok(self.client, "unused")
        self.adapter.session = "native-session"
        self.adapter.rpc = SimpleNamespace(request=AsyncMock(return_value={"status": "queued"}))

    async def asyncTearDown(self):
        self.adapter.trace.close()
        self.temp.cleanup()

    def message(self):
        return {"id": "message", "case_id": "case", "sender": "codex", "kind": "challenge", "nonce": "nonce"}

    async def completion(self, turn):
        await self.adapter.notification("_x.ai/session/update", {"sessionId": "native-session", "update": {
            "sessionUpdate": "turn_completed", "prompt_id": turn, "stop_reason": "end_turn"}})

    async def test_grok_fallback_remains_pending_until_native_handling_and_completion(self):
        self.adapter.busy = self.adapter.native_busy = True
        await self.adapter.deliver(self.message())
        await self.completion("old-turn")
        self.assertTrue(self.adapter.busy)
        await self.adapter.notification("session/update", {"sessionId": "native-session", "update": {
            "sessionUpdate": "tool_call", "toolCallId": "reply-call", "title": "coord_proof__proof_send",
            "rawInput": {"case_id": "case", "kind": "reply", "nonce": "nonce"}}})
        self.assertFalse(self.adapter.pending_interjections)
        self.assertTrue(self.adapter.busy)
        await self.completion("fallback-turn")
        self.assertFalse(self.adapter.busy)
        # Duplicate terminal signals must not clear a later active turn.
        await self.adapter.notification("session/update", {"sessionId": "native-session", "update": {
            "sessionUpdate": "agent_thought_chunk", "content": {"text": ""}}})
        await self.completion("fallback-turn")
        self.assertTrue(self.adapter.busy)

    async def test_grok_timeout_is_unknown_activity_and_uses_work_budget(self):
        self.adapter.rpc.request.side_effect = asyncio.TimeoutError
        self.adapter.prompts_in_flight = 1
        await self.adapter.prompt(self.message())
        self.assertEqual(self.adapter.rpc.request.call_args.kwargs["timeout"], 570)
        self.assertTrue(self.adapter.busy)
        self.assertEqual(self.events[-1]["state"], "unknown")
        self.assertFalse(any(e["event"] == "turn_completed" for e in self.events))

    async def test_grok_discovery_keeps_delivery_pending_then_wrapped_reply_clears_it(self):
        self.adapter.busy = self.adapter.native_busy = True
        await self.adapter.deliver(self.message())
        await self.completion("old-turn")
        search = GrokToolContractTests.packet("search_tool", {"query": "coord_proof"})
        await self.adapter.notification("session/update", {"sessionId": "native-session", "update": search})
        self.assertEqual(len(self.adapter.pending_interjections), 1)
        self.assertTrue(any(e["event"] == "native_discovery" for e in self.events))
        self.assertFalse(any(e["event"] in {"native_tool", "tool_violation"} for e in self.events))
        args = {"to": "codex", "case_id": "case", "kind": "reply", "nonce": "nonce", "memory": "secret"}
        update = GrokToolContractTests.packet("use_tool", {"tool_name": "coord_proof__proof_send", "tool_input": args})
        update["toolCallId"] = "wrapped-reply"
        await self.adapter.notification("session/update", {"sessionId": "native-session", "update": update})
        self.assertFalse(self.adapter.pending_interjections)
        observed = next(e for e in self.events if e["event"] == "native_tool")
        self.assertEqual(observed["arguments"], args)
        self.assertEqual(observed["wire_arguments"], update["rawInput"])
        self.assertTrue(self.adapter.busy)
        await self.completion("fallback-turn")
        self.assertFalse(self.adapter.busy)
        called = {"event": "tool_called", "seq": len(self.events) + 1, "tool": "proof_send", "arguments": args}
        audit = tool_audit([{**e, "peer": "grok"} for e in [*self.events, called]],
                           {"grok": {"session_id": "native-session"}})
        self.assertEqual(audit["status"], "pass")
        self.assertEqual(audit["native_discovery_calls"], 1)
        self.assertEqual(audit["native_tool_calls"], 1)

    async def test_grok_invalid_dispatch_is_violation_without_proof_evidence(self):
        self.adapter.busy = self.adapter.native_busy = True
        await self.adapter.deliver(self.message())
        update = GrokToolContractTests.packet("use_tool", {"tool_name": "other_server__proof_send", "tool_input": {
            "to": "codex", "case_id": "case", "kind": "reply", "nonce": "nonce", "memory": "secret"}})
        await self.adapter.notification("session/update", {"sessionId": "native-session", "update": update})
        self.assertTrue(self.adapter.pending_interjections)
        self.assertFalse(any(e["event"] == "native_tool" for e in self.events))
        self.assertEqual(self.events[-1]["event"], "tool_violation")

    async def test_grok_rpc_response_without_native_completion_does_not_prove_idle(self):
        self.adapter.prompts_in_flight = 1
        self.adapter.native_busy = True
        await self.adapter.prompt(self.message())
        self.assertTrue(self.adapter.busy)
        self.assertEqual(self.events[-1]["state"], "active")
        self.assertFalse(any(e["event"] == "turn_completed" for e in self.events))

    async def test_grok_rejects_native_session_change(self):
        with self.assertRaises(ValueError):
            await self.adapter.notification("session/update", {"sessionId": "different", "update": {}})
        self.assertEqual(self.events[-1]["event"], "native_identity_error")

    async def test_permission_answers_are_serialized(self):
        active, maximum = 0, 0
        async def answer():
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.02)
            active -= 1
            return "y"
        self.adapter.permission_answer = answer
        results = await asyncio.gather(*(self.adapter.request("session/request_permission", {
            "sessionId": "native-session", "toolCall": {"title": str(i)},
            "options": [{"kind": "allow_once", "optionId": str(i)}]}) for i in range(3)))
        self.assertEqual(maximum, 1)
        self.assertEqual([r["outcome"]["optionId"] for r in results], ["0", "1", "2"])

    async def test_permission_without_allow_once_cannot_select_allow_always(self):
        self.adapter.permission_answer = AsyncMock(return_value="y")
        result = await self.adapter.request("session/request_permission", {
            "sessionId": "native-session", "toolCall": {"title": "proof tool"},
            "options": [{"kind": "allow_always", "optionId": "persistent"}]})
        self.adapter.permission_answer.assert_not_awaited()
        self.assertEqual(result["outcome"]["outcome"], "cancelled")
        self.assertEqual(self.events[-1]["event"], "permission_denied")

    async def test_codex_read_command_invalidates_cooperative_evidence(self):
        self.client.peer = "codex"
        codex = Codex(self.client, "unused")
        codex.session = "native-session"
        try:
            await codex.notification("item/started", {"threadId": "native-session",
                "item": {"type": "commandExecution", "id": "read-answer-key"}})
            self.assertEqual(self.events[-1]["event"], "tool_violation")
            with self.assertRaises(ValueError):
                await codex.request("item/tool/call", {"threadId": "changed", "tool": "proof_report"})
        finally:
            codex.trace.close()

    async def test_codex_without_required_schema_never_starts_a_session(self):
        self.client.peer = "codex"
        codex = Codex(self.client, "unused")
        codex.spawn = AsyncMock()
        try:
            with patch("mhproof.adapters.codex_capability", return_value={"status": "unsupported"}):
                with self.assertRaises(ValueError):
                    await codex.start()
            codex.spawn.assert_not_awaited()
            self.assertTrue(any(e["event"] == "unsupported" for e in self.events))
        finally:
            codex.trace.close()

    async def test_claude_hook_records_actual_identity_and_blocks_nonproof_tools(self):
        result = claude_hook(self.client, {"session_id": "native-claude", "prompt_id": "p",
            "hook_event_name": "PreToolUse", "tool_name": "mcp__coord_proof__proof_hold",
            "tool_input": {"case_id": "held"}, "tool_use_id": "native-call"})
        self.assertEqual(result, 0)
        self.assertEqual(self.events[-1]["native_id"], "native-claude")
        result = claude_hook(self.client, {"session_id": "native-claude", "hook_event_name": "PreToolUse",
                                          "tool_name": "Read", "tool_input": {}})
        self.assertEqual(result, 2)
        self.assertEqual(self.events[-1]["event"], "tool_violation")


class RPCTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_response_after_timeout_cannot_satisfy_next_request(self):
        program = '''import sys,json,time
first=json.loads(sys.stdin.readline())
time.sleep(0.12)
print(json.dumps({"id":first["id"],"result":{"which":"expired"}}),flush=True)
second=json.loads(sys.stdin.readline())
print(json.dumps({"id":second["id"],"result":{"which":"current"}}),flush=True)
'''
        proc = await asyncio.create_subprocess_exec(sys.executable, "-c", program,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        rpc = RPC(proc, AsyncMock(), AsyncMock())
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await rpc.request("first", {}, timeout=0.02)
            result = await rpc.request("second", {}, timeout=3)
            self.assertEqual(result, {"which": "current"})
            self.assertFalse(rpc.pending)
        finally:
            await rpc.close()

    async def test_non_json_stdout_is_a_diagnostic_protocol_failure(self):
        proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "print('startup banner', flush=True)",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        rpc = RPC(proc, AsyncMock(), AsyncMock())
        await asyncio.wait_for(rpc.dead.wait(), 3)
        self.assertIn("Non-JSON data on native protocol stdout", rpc.failure)
        await rpc.close()

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
