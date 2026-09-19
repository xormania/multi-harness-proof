"""Loopback relay and evidence log. No model answers are generated here."""
import json
import os
import queue
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from .contract import validate_tool
from .telemetry import Redactor
from .workload import FIXTURE, Workload


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(value, f, indent=2)
        f.write("\n")
    os.chmod(temp, 0o600)
    temp.replace(path)


class State:
    def __init__(self, directory, peers, hold_timeout=180, fixture=FIXTURE):
        self.directory = Path(directory)
        self.peers = tuple(peers)
        self.hold_timeout = hold_timeout
        self.lock = threading.RLock()
        self.events = []
        self.sessions = {}
        self.queues = {p: queue.Queue() for p in peers}
        self.holds = {}
        self.stopped = threading.Event()
        self.tokens = {p: secrets.token_urlsafe(32) for p in peers}
        self.redactor = Redactor(self.tokens.values())
        self.log = (self.directory / "events.jsonl").open("x", encoding="utf-8")
        self.workload = Workload(self.emit, fixture)

    def emit(self, event, peer=None, **fields):
        with self.lock:
            record = {"seq": len(self.events) + 1, "time": time.time(), "event": event,
                      "peer": peer, **fields}
            if peer in self.sessions:
                record["registered_session_id"] = self.sessions[peer]["session_id"]
            self.events.append(record)
            self.log.write(json.dumps(self.redactor.value(record), ensure_ascii=False) + "\n")
            self.log.flush()
            return record

    def snapshot(self):
        with self.lock:
            return list(self.events)

    def register(self, peer, data):
        with self.lock:
            if peer in self.sessions:
                raise ValueError("A session is already registered for " + peer + "; start a new run")
            if not isinstance(data.get("session_id"), str) or not data["session_id"]:
                raise ValueError("Missing native session identity")
            self.sessions[peer] = data
            self.emit("registered", peer, details=data)

    def enqueue(self, sender, recipient, **body):
        if recipient not in self.queues or recipient == sender:
            raise ValueError("Recipient must be another selected peer")
        with self.lock:
            msg = {**body, "id": uuid.uuid4().hex, "sender": sender, "to": recipient}
            self.emit("queued", sender, message=msg)
            self.queues[recipient].put(msg)
            return {"queued": True, "message_id": msg["id"]}

    def control(self, peer, text, case_id):
        return self.enqueue("controller", peer, kind="instruction", text=text, case_id=case_id)

    def tool(self, peer, name, args):
        validate_tool(name, args)
        if peer not in self.sessions:
            raise ValueError("Harness has not registered a session")
        self.emit("tool_called", peer, tool=name, arguments=args)
        if name == "proof_send":
            return self.enqueue(peer, args["to"], **{k: v for k, v in args.items() if k != "to"})
        if name == "proof_report":
            self.emit("report", peer, report=args)
            return {"recorded": True}
        if name in ("proof_work_next", "proof_work_submit"):
            with self.lock:
                return self.workload.next(peer) if name == "proof_work_next" else self.workload.submit(peer, args)
        case = args["case_id"]
        key = (peer, case)
        with self.lock:
            if key in self.holds:
                raise ValueError("This hold already exists")
            release = self.holds[key] = threading.Event()
            self.emit("hold_started", peer, case_id=case)
        released = release.wait(self.hold_timeout)
        self.emit("hold_finished", peer, case_id=case, released=released)
        return {"released": released, "instruction": "Handle any pending proof messages, then end your turn."}

    def release(self, peer, case):
        with self.lock:
            hold = self.holds.get((peer, case))
            if hold:
                self.emit("hold_release_requested", peer, case_id=case)
                hold.set()

    def close(self):
        self.stopped.set()
        with self.lock:
            for hold in self.holds.values():
                hold.set()


class Server(ThreadingHTTPServer):
    daemon_threads = True


def serve(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.connection.settimeout(15)
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            peer = next((p for p, t in state.tokens.items() if secrets.compare_digest(t, token)), None)
            status = 200
            try:
                if peer is None:
                    raise PermissionError("Invalid run credential")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Request must be an object")
                if self.path == "/register":
                    state.register(peer, body)
                    result = {"ok": True}
                elif self.path == "/poll":
                    try:
                        msg = state.queues[peer].get(timeout=1)
                    except queue.Empty:
                        msg = None
                    result = {"message": msg, "stopped": state.stopped.is_set()}
                elif self.path == "/tool":
                    result = state.tool(peer, body["name"], body["arguments"])
                elif self.path == "/event":
                    allowed = {"submitted", "delivery_error", "unsupported", "adapter_error",
                               "mcp_ready", "turn_started", "turn_completed", "permission_denied",
                               "permission_requested", "permission_resolved",
                               "session_observed", "adapter_stopped", "native_session", "native_tool",
                               "native_identity_error", "activity", "tool_violation", "protocol_capability"}
                    event = body.pop("event")
                    if event not in allowed or set(body) & {"peer", "session_id", "registered_session_id", "seq", "time"}:
                        raise ValueError("Invalid adapter event")
                    state.emit(event, peer, **body)
                    if event == "adapter_stopped":
                        with state.lock:
                            for (owner, case) in state.holds:
                                if owner == peer:
                                    state.release(owner, case)
                    result = {"ok": True}
                else:
                    status, result = 404, {"error": "Unknown endpoint"}
            except PermissionError as e:
                status, result = 403, {"error": str(e)}
            except (ValueError, KeyError, TypeError) as e:
                status, result = 400, {"error": str(e)}
            if status >= 400:
                state.emit("http_error", peer, path=self.path, status=status, error=result["error"])
            payload = json.dumps(result).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class Client:
    def __init__(self, directory, peer):
        self.directory = Path(directory).resolve()
        self.config = json.loads((self.directory / "run.json").read_text())
        self.peer = peer
        self.token = self.config["tokens"][peer]
        self.url = self.config["url"]
        if not self.url.startswith("http://127.0.0.1:"):
            raise ValueError("Run URL must use loopback")

    def post(self, path, body, timeout=15):
        req = Request(self.url + path, data=json.dumps(body).encode(),
                      headers={"Authorization": "Bearer " + self.token,
                               "Content-Type": "application/json"}, method="POST")
        # Never send local tokens through an environment-configured HTTP proxy.
        with build_opener(ProxyHandler({})).open(req, timeout=timeout) as response:
            return json.load(response)

    def event(self, event, **data):
        return self.post("/event", {"event": event, **data})

    def tool(self, name, args):
        return self.post("/tool", {"name": name, "arguments": args},
                         timeout=self.config["timeout"] + 30)
