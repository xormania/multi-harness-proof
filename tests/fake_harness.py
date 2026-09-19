"""Deterministic protocol fixture. NOT a harness/model performance test.

Speaks the native wire protocols, invokes the real relay tools, and maintains
its own memory. Run only through test_live_wires.py, in temporary directories.
"""
import json
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
        self.queue = queue.Queue()
        self.wire = Wire(sys.stdin, sys.stdout, self.handle)
        self.mcp = None
        self.child = None
        threading.Thread(target=self.work, daemon=True).start()

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
            self.queue.put((self.decode(packet["params"]["content"]), None))

    @staticmethod
    def decode(text):
        return json.loads(text.split("PROOF MESSAGE\n", 1)[1])

    def tool(self, name, args):
        if self.mode == "codex":
            result = self.wire.call("item/tool/call", {
                "threadId": self.session, "turnId": "fixture-turn", "callId": uuid.uuid4().hex,
                "tool": name, "arguments": args})
            if not result["success"]:
                raise RuntimeError(result)
            return json.loads(result["contentItems"][0]["text"])
        else:
            result = self.mcp.call("tools/call", {"name": name, "arguments": args})
            if result.get("isError"):
                raise RuntimeError(result)
            return json.loads(result["content"][0]["text"])

    def react(self, msg):
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
            elif "WORK_PLAN_JSON=" in text:
                challenges = json.loads(text.split("WORK_PLAN_JSON=", 1)[1])
                while True:
                    batch = self.tool("proof_work_next", {})
                    if batch["done"]:
                        break
                    # Simulate a little latency so concurrently launched fixtures
                    # overlap. This is not a claim about model execution time.
                    time.sleep(0.15)
                    latest = {}
                    for row in batch["rows"]:
                        if row["job"] not in latest or row["attempt"] > latest[row["job"]]["attempt"]:
                            latest[row["job"]] = row
                    failures = [r for r in latest.values() if r["status"] == "FAIL"]
                    self.tool("proof_work_submit", {"batch": batch["batch"],
                        "failed_jobs": sorted(r["job"] for r in failures),
                        "failed_tests": sum(r["failed_tests"] for r in failures)})
                    if batch["batch"] == 0:
                        for challenge in challenges:
                            self.tool("proof_send", challenge)
                    while not self.queue.empty():
                        incoming, ident = self.queue.get()
                        self.react(incoming)
                        if self.mode == "grok" and ident is not None:
                            self.wire.send({"id": ident, "result": {"stopReason": "end_turn"}})
        elif msg["kind"] == "challenge":
            self.tool("proof_send", {"to": msg["sender"], "kind": "reply", "case_id": msg["case_id"],
                                     "nonce": msg["nonce"], "memory": self.memory})
        elif msg["kind"] == "reply":
            self.tool("proof_report", {"phase": "received", "case_id": msg["case_id"],
                                       "nonce": msg["nonce"], "memory": msg["memory"], "peer": msg["sender"]})

    def work(self):
        while True:
            msg, ident = self.queue.get()
            turn = uuid.uuid4().hex
            if self.mode == "codex":
                self.wire.send({"method": "turn/started", "params": {"threadId": self.session,
                                "turn": {"id": turn, "status": "inProgress"}}})
            self.react(msg)
            # Drain native interjections before completing this prompt/turn.
            while not self.queue.empty():
                extra, extra_ident = self.queue.get()
                self.react(extra)
                if self.mode == "grok" and extra_ident is not None:
                    self.wire.send({"id": extra_ident, "result": {"stopReason": "end_turn"}})
            if self.mode == "codex":
                self.wire.send({"method": "turn/completed", "params": {"threadId": self.session,
                                "turn": {"id": turn, "status": "completed"}}})
            elif self.mode == "grok" and ident is not None:
                self.wire.send({"id": ident, "result": {"stopReason": "end_turn"}})

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
            self.queue.put((self.decode(params["text"]), None))
            result = {"status": "queued"}
        elif ident is None:
            return
        else:
            self.wire.send({"id": ident, "error": {"code": -32601, "message": "Unsupported fixture method"}})
            return
        self.wire.send({"id": ident, "result": result})

    def run(self):
        try:
            if self.mode == "claude":
                cfg = json.loads(Path(sys.argv[sys.argv.index("--mcp-config") + 1]).read_text())
                mcp = cfg["mcpServers"]["coord_proof"]
                self.connect_mcp(mcp["command"], mcp["args"])
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
    else:
        Fake(sys.argv[1]).run()
