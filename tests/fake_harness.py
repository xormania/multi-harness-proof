"""Deterministic protocol fixture. NOT a harness/model performance test.

Speaks the native wire protocols, invokes the real relay tools, and maintains
its own memory. Used by test_proof.py and behavior.py; never a real harness.
"""
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import uuid


class Wire:
    def __init__(self, reader, writer, handler):
        self.reader, self.writer, self.handler = reader, writer, handler
        self.lock = threading.Lock()
        self.pending = {}
        self.counter = 10000

    def send(self, packet):
        with self.lock:
            self.writer.write(json.dumps({"jsonrpc": "2.0", **packet}) + "\n")
            self.writer.flush()

    def call(self, method, params):
        with self.lock:
            self.counter += 1
            ident = self.counter
            box = self.pending[ident] = queue.Queue()
        self.send({"id": ident, "method": method, "params": params})
        response = box.get(timeout=15)
        self.pending.pop(ident, None)
        if "error" in response:
            raise RuntimeError(response["error"])
        return response.get("result", {})

    def read(self):
        for line in self.reader:
            packet = json.loads(line)
            if "method" not in packet:
                if packet.get("id") in self.pending:
                    self.pending[packet["id"]].put(packet)
            else:
                self.handler(packet)


class Fake:
    def __init__(self, mode):
        self.mode = mode
        self.session = mode + "-fixture-" + uuid.uuid4().hex
        self.memory = None
        self.active_turn = None
        self.hooks = {}
        self.queue = queue.Queue()
        self.wire = Wire(sys.stdin, sys.stdout, self.handle)
        self.mcp = None
        self.child = None
        self.scenario = os.environ.get("MHPROOF_FAKE_SCENARIO", "happy")
        self.first_call_id = None
        self.discovered_proof_tools = False
        self.channel_registered = False
        self.rules = json.loads(os.environ.get("MHPROOF_FAKE_RULES", "[]"))
        self.rule_hits = [0] * len(self.rules)
        threading.Thread(target=self.work, daemon=True).start()

    def fault(self, name, **fields):
        path = os.environ.get("MHPROOF_FAKE_FAULT_LOG")
        if path:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"time": time.time(), "peer": self.mode,
                    "scenario": self.scenario, "fault": name, "pid": os.getpid(), **fields}) + "\n")

    def connect_mcp(self, command, args):
        self.child = subprocess.Popen([command, *args], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, text=True)
        self.mcp = Wire(self.child.stdout, self.child.stdin, self.channel)
        threading.Thread(target=self.mcp.read, daemon=True).start()
        self.mcp.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "FAKE-OFFLINE-TEST", "version": "0"}})
        self.mcp.send({"method": "notifications/initialized"})

    def channel(self, packet):
        if packet["method"] == "notifications/claude/channel":
            message = self.decode(packet["params"]["content"])
            if not self.channel_registered:
                self.fault("early-channel-notification-dropped", message_id=message["id"])
                return
            if self.scenario == "claude-channel-drop" and message["case_id"] == "startup":
                self.fault("registered-channel-dropped-startup", message_id=message["id"])
                return
            self.queue.put((message, None))

    @staticmethod
    def decode(text):
        return json.loads(text.split("PROOF MESSAGE\n", 1)[1])

    def tool(self, name, args):
        if self.mode == "codex":
            result = self.wire.call("item/tool/call", {
                "threadId": self.session, "turnId": self.active_turn, "callId": uuid.uuid4().hex,
                "tool": name, "arguments": args})
            if not result["success"]:
                raise RuntimeError(result)
            return json.loads(result["contentItems"][0]["text"])
        else:
            if self.mode == "grok":
                is_reply = name == "proof_send" and args.get("kind") == "reply"
                wrapped = self.scenario in {"grok-discovery", "grok-discovery-only", "grok-wrong-dispatch"}
                if wrapped and not self.discovered_proof_tools:
                    discovery_id = uuid.uuid4().hex
                    self.wire.send({"method": "session/update", "params": {"sessionId": self.session,
                        "update": {"sessionUpdate": "tool_call", "toolCallId": discovery_id,
                                   "title": "search_tool", "rawInput": {"query": "coord_proof", "limit": 5},
                                   "_meta": {"x.ai/tool": {"version": 1, "name": "search_tool",
                                       "namespace": "grok_build", "kind": "search_tool", "read_only": False}}}}})
                    catalog = self.mcp.call("tools/list", {})
                    assert name in {tool["name"] for tool in catalog["tools"]}
                    self.wire.send({"method": "session/update", "params": {"sessionId": self.session,
                        "update": {"sessionUpdate": "tool_call_update", "toolCallId": discovery_id,
                                   "status": "completed"}}})
                    self.discovered_proof_tools = True
                    self.fault("native-catalog-discovery-with-real-mcp-tools-list")
                call_id = uuid.uuid4().hex
                self.first_call_id = self.first_call_id or call_id
                if is_reply and self.scenario == "reused-call-id":
                    call_id = self.first_call_id
                    self.fault("reused-call-id")
                identity = {"version": 1, "name": "coord_proof__" + name, "namespace": "mcp",
                            "kind": "other", "label": "MCP tool", "read_only": False}
                packet = {"method": "session/update", "params": {"sessionId": self.session,
                    "update": {"sessionUpdate": "tool_call", "toolCallId": call_id,
                               "title": "coord_proof__" + name, "rawInput": args,
                               "_meta": {"x.ai/tool": identity}}}}
                if wrapped:
                    target = "coord_proof__" + name
                    if is_reply and self.scenario == "grok-wrong-dispatch":
                        target = "other_server__" + name
                        self.fault("wrong-mcp-target-observed-no-dispatch")
                    identity.update(name="use_tool", namespace="grok_build", kind="use_tool")
                    packet["params"]["update"].update(title="use_tool",
                        rawInput={"tool_name": target, "tool_input": args})
                if self.scenario == "prose-title":
                    packet["params"]["update"]["title"] = "Send a coordination message"
                    self.fault("prose-title-with-canonical-identity")
                if is_reply and self.scenario == "session-drift":
                    packet["params"]["sessionId"] += "-changed"
                    self.fault("session-drift")
                if is_reply and self.scenario == "extra-tool":
                    packet["params"]["update"]["title"] = "read_file"
                    identity.update(name="read_file", namespace="grok_build")
                    self.fault("non-proof-tool-observed-no-file-read")
                late = is_reply and self.scenario == "delayed-duplicate"
                missing = (is_reply and self.scenario == "missing-native" or
                           self.scenario == "grok-discovery-only")
                if missing:
                    self.fault("missing-native-tool-notification")
                elif not late:
                    self.wire.send(packet)
                if is_reply and self.scenario == "grok-wrong-dispatch":
                    return {"denied": True}
                if is_reply and self.scenario == "permission-denied":
                    self.fault("permission-request")
                    outcome = self.wire.call("session/request_permission", {"sessionId": self.session,
                        "toolCall": {"toolCallId": call_id, "title": "coord_proof__" + name},
                        "options": [{"kind": "allow_once", "optionId": "once"}]})
                    if outcome["outcome"]["outcome"] != "selected":
                        self.fault("permission-denied-no-mcp-call")
                        return {"denied": True}
            else:
                self.hook("PreToolUse", tool_name="mcp__coord_proof__" + name,
                          tool_input=args, tool_use_id=uuid.uuid4().hex)
            result = self.mcp.call("tools/call", {"name": name, "arguments": args})
            if self.mode == "grok" and late:
                time.sleep(0.08)
                self.wire.send(packet)
                self.wire.send(packet)
                self.fault("late-duplicate-native-observation")
            if result.get("isError"):
                raise RuntimeError(result)
            return json.loads(result["content"][0]["text"])

    def hook(self, event, **fields):
        if event == "PreToolUse" and self.scenario == "missing-hook":
            self.fault("missing-claude-hook")
            return
        transcript = Path.cwd() / (self.session + ".jsonl")
        with transcript.open("a") as handle:
            handle.write(json.dumps({"type": "system", "sessionId": self.session,
                                     "mock_hook": event, "mode": "mock"}) + "\n")
        packet = {"session_id": self.session, "prompt_id": self.active_turn,
                  "transcript_path": str(transcript),
                  "hook_event_name": event, **fields}
        for group in self.hooks.get(event, []):
            for hook in group["hooks"]:
                subprocess.run(hook["command"], shell=True, input=json.dumps(packet),
                               text=True, check=True, timeout=5)

    def react(self, msg):
        msg = dict(msg)
        memory_override = None
        for index, rule in enumerate(self.rules):
            if rule["peer"] != self.mode or rule["on"] != msg["kind"] or not msg["case_id"].startswith(rule["case_prefix"]):
                continue
            self.rule_hits[index] += 1
            if not rule["occurrence"] <= self.rule_hits[index] < rule["occurrence"] + rule["times"]:
                continue
            action = rule["action"]
            self.fault("configured-" + action, rule_index=index, occurrence=self.rule_hits[index],
                       message_id=msg["id"], case_id=msg["case_id"])
            if action == "delay":
                time.sleep(rule["seconds"])
            elif action == "drop":
                return
            elif action == "crash":
                os._exit(72)
            elif action == "wrong-memory":
                memory_override = "configured-wrong-memory"
            elif action == "stale-nonce":
                msg["nonce"] = "configured-stale-nonce"
        if msg["kind"] == "instruction":
            text = msg["text"]
            match = re.search(r"PRIVATE_MEMORY=([a-f0-9]+)", text)
            if match:
                self.memory = match[1]
                self.tool("proof_report", {"phase": "ready", "case_id": "startup", "nonce": "",
                                           "memory": self.memory, "peer": ""})
            elif text.startswith("Call proof_hold"):
                case = re.search(r'case_id="([^"]+)"', text)[1]
                self.tool("proof_hold", {"case_id": case})
            elif text.startswith("Call proof_send once with "):
                args, _ = json.JSONDecoder().raw_decode(text.split("with ", 1)[1])
                self.tool("proof_send", args)
            elif "WORK_BATCH=" in text:
                batch = self.tool("proof_work_next", {})
                # Deliberately unequal speeds; overlap must come from the barrier.
                if self.mode == "grok":
                    time.sleep(0.15)
                latest = {}
                for row in batch["rows"]:
                    if row["job"] not in latest or row["attempt"] > latest[row["job"]]["attempt"]:
                        latest[row["job"]] = row
                failures = [r for r in latest.values() if r["status"] == "FAIL"]
                answer = {"batch": batch["batch"],
                    "failed_jobs": sorted(r["job"] for r in failures),
                    "failed_tests": sum(r["failed_tests"] for r in failures)}
                if self.mode == "grok" and self.scenario == "incorrect-work" and batch["batch"] == 0:
                    answer["failed_tests"] += 1
                    self.fault("incorrect-work-answer")
                self.tool("proof_work_submit", answer)
            elif "WORK_PLAN_JSON=" in text:
                for challenge in json.loads(text.split("WORK_PLAN_JSON=", 1)[1]):
                    self.tool("proof_send", challenge)
        elif msg["kind"] == "challenge":
            if self.mode == "grok":
                if self.scenario in {"queued-only", "interrupted"}:
                    self.fault("queued-without-handling")
                    return
                if self.scenario == "crash":
                    self.fault("native-process-exit-72")
                    os._exit(72)
                if self.scenario == "malformed-stdout":
                    self.fault("non-json-stdout")
                    with self.wire.lock:
                        sys.stdout.write("unexpected startup banner\n")
                        sys.stdout.flush()
                    return
            nonce, memory = msg["nonce"], memory_override or self.memory
            if self.mode == "grok" and self.scenario in {"wrong-memory", "stale-nonce"}:
                self.fault(self.scenario)
                if self.scenario == "wrong-memory":
                    memory = "forgotten-context"
                else:
                    nonce = "previous-challenge"
            self.tool("proof_send", {"to": msg["sender"], "kind": "reply", "case_id": msg["case_id"],
                                     "nonce": nonce, "memory": memory})
        elif msg["kind"] == "reply":
            self.tool("proof_report", {"phase": "received", "case_id": msg["case_id"],
                                       "nonce": msg["nonce"], "memory": msg["memory"], "peer": msg["sender"]})

    def work(self):
        while True:
            msg, ident = self.queue.get()
            turn = uuid.uuid4().hex
            self.active_turn = turn
            if self.mode == "codex":
                self.wire.send({"method": "turn/started", "params": {"threadId": self.session,
                                "turn": {"id": turn, "status": "inProgress"}}})
            self.react(msg)
            # Claude uses later turns. Grok deliberately exercises fallback turns
            # with no session/prompt response for the interjected message.
            while self.mode == "codex" and not self.queue.empty():
                extra, extra_ident = self.queue.get()
                self.react(extra)
                if self.mode == "grok" and extra_ident is not None:
                    self.wire.send({"id": extra_ident, "result": {"stopReason": "end_turn"}})
            if self.mode == "codex":
                self.wire.send({"method": "turn/completed", "params": {"threadId": self.session,
                                "turn": {"id": turn, "status": "completed"}}})
            elif self.mode == "grok":
                completion = {"method": "_x.ai/session/update", "params": {"sessionId": self.session,
                    "update": {"sessionUpdate": "turn_completed", "prompt_id": turn, "stop_reason": "end_turn"}}}
                if self.scenario == "missing-completion":
                    self.fault("missing-native-completion")
                else:
                    self.wire.send(completion)
                    if self.scenario == "delayed-duplicate":
                        self.wire.send(completion)
                if ident is not None:
                    self.wire.send({"id": ident, "result": {"stopReason": "end_turn"}})
            else:
                self.hook("Stop")

    def handle(self, packet):
        method = packet["method"]
        params = packet.get("params", {})
        ident = packet.get("id")
        if method == "initialize":
            result = {"protocolVersion": 1, "authMethods": []}
        elif method == "thread/start":
            assert params["dynamicTools"][0]["type"] == "function"
            result = {"thread": {"id": self.session}}
        elif method == "session/new":
            mcp = params["mcpServers"][0]
            self.connect_mcp(mcp["command"], mcp["args"])
            result = {"sessionId": self.session}
        elif method == "turn/start":
            assert params["threadId"] == self.session
            text = params["toolOutput"]["output"] if "toolOutput" in params else params["input"][0]["text"]
            self.queue.put((self.decode(text), None))
            result = {"turn": {"id": "fixture-turn", "status": "inProgress"}}
        elif method == "session/prompt":
            assert params["sessionId"] == self.session
            self.queue.put((self.decode(params["prompt"][0]["text"]), ident))
            return
        elif method == "_x.ai/interject":
            assert params["sessionId"] == self.session
            if self.scenario == "unsupported-interject":
                self.fault("interject-method-not-found")
                self.wire.send({"id": ident, "error": {"code": -32601, "message": "Injected missing interject"}})
                return
            self.queue.put((self.decode(params["text"]), None))
            result = {"status": "queued"}
        elif ident is None:
            return
        else:
            self.wire.send({"id": ident, "error": {"code": -32601, "message": "Unsupported fixture method"}})
            return
        if method == "turn/start" and self.scenario == "delayed-duplicate":
            def late_ack():
                time.sleep(0.2)
                self.wire.send({"id": ident, "result": result})
            self.fault("late-turn-start-acknowledgement")
            threading.Thread(target=late_ack, daemon=True).start()
            return
        self.wire.send({"id": ident, "result": result})

    def run(self):
        try:
            if self.mode == "claude":
                self.session = sys.argv[sys.argv.index("--session-id") + 1]
                self.hooks = json.loads(Path(sys.argv[sys.argv.index("--settings") + 1]).read_text())["hooks"]
                self.hook("SessionStart")
                cfg = json.loads(Path(sys.argv[sys.argv.index("--mcp-config") + 1]).read_text())
                mcp = cfg["mcpServers"]["coord_proof"]
                self.connect_mcp(mcp["command"], mcp["args"])
                # Initial launch turn completion is NOT channel registration.
                self.hook("Stop")
                if self.scenario == "claude-channel-delayed":
                    time.sleep(0.6)
                    self.fault("delayed-channel-handler-registration")
                self.channel_registered = True
                from datetime import datetime, timezone
                stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                marker = 'MCP server "coord_proof": Channel notifications registered'
                if self.scenario == "claude-channel-unobserved":
                    marker = 'MCP server "coord_proof": future unrecognized readiness format'
                    self.fault("unobserved-channel-registration")
                Path(sys.argv[sys.argv.index("--debug-file") + 1]).write_text(stamp + " [DEBUG] " + marker + "\n")
                self.child.wait()
            else:
                self.wire.read()
        finally:
            if self.child:
                self.child.terminate()
                self.child.wait(timeout=5)


if __name__ == "__main__":
    if "--version" in sys.argv:
        print("FAKE-OFFLINE-FIXTURE 0")
    elif "generate-json-schema" in sys.argv:
        if "--help" in sys.argv:
            print("--out DIR --experimental")
        else:
            destination = Path(sys.argv[sys.argv.index("--out") + 1]) / "TurnStartParams.json"
            destination.write_text(json.dumps({"title": "TurnStartParams", "properties": {
                "threadId": {}, "input": {}, "toolOutput": {}}}))
    else:
        Fake(sys.argv[1]).run()
