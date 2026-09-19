"""Native harness adapters. These processes keep one session for the whole run."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
import uuid

from . import VERSION
from .contract import INSTRUCTIONS, TOOLS, render
from .relay import Client, write_json
from .rpc import RPC, RPCError
from .telemetry import Trace

ENTRY = Path(__file__).resolve().parents[1] / "proof.py"


def version(peer, binary):
    try:
        argv = [binary] + (["--no-auto-update"] if peer == "grok" else []) + ["--version"]
        p = subprocess.run(argv, text=True, capture_output=True, timeout=15)
        return (p.stdout or p.stderr).strip()[:500]
    except (OSError, subprocess.TimeoutExpired) as e:
        return "unavailable: " + str(e)


def mcp_args(directory, peer):
    return [str(ENTRY), "mcp", "--dir", str(directory), "--peer", peer]


class Native:
    def __init__(self, client, binary, model=None, reasoning=None):
        self.client, self.binary, self.model = client, binary, model
        self.peer = client.peer
        self.reasoning = reasoning
        self.directory = client.directory
        self.cwd = self.directory / "workspaces" / self.peer
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.session = None
        self.busy = False
        self.active_turn = None
        self.background = set()
        self.rpc = None
        self.stderr = None
        self.trace = Trace(self.directory, self.peer)
        self.stderr_task = None

    async def event(self, event, **data):
        await asyncio.to_thread(self.client.event, event, **data)

    async def register(self, transport):
        await asyncio.to_thread(self.client.post, "/register", {
            "session_id": self.session, "pid": self.rpc.process.pid,
            "version": await asyncio.to_thread(version, self.peer, self.binary),
            "transport": transport, "model_requested": self.model or "harness default",
            "reasoning_requested": self.reasoning or "not overridden"})

    async def spawn(self, argv, jsonrpc):
        self.stderr = (self.directory / (self.peer + "-stderr.log")).open("w", encoding="utf-8")
        self.trace.record("launch", {"argv": argv, "cwd": str(self.cwd)})
        process = await asyncio.create_subprocess_exec(
            *argv, cwd=self.cwd, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=4 * 1024 * 1024)
        self.trace.record("process_started", {"pid": process.pid})
        self.rpc = RPC(process, self.request, self.notification, jsonrpc=jsonrpc, trace=self.trace)
        async def stderr_reader():
            while line := await process.stderr.readline():
                text = self.trace.redactor.text(line.decode("utf-8", errors="replace"))
                self.stderr.write(text)
                self.stderr.flush()
                self.trace.record("stderr", {"text": text})
        self.stderr_task = asyncio.create_task(stderr_reader())

    async def run(self):
        try:
            await self.start()
            print(self.peer + " ready; native session " + self.session, flush=True)
            while not self.rpc.dead.is_set():
                polled = await asyncio.to_thread(self.client.post, "/poll", {})
                if polled["stopped"]:
                    return
                if polled["message"]:
                    msg = polled["message"]
                    try:
                        await self.deliver(msg)
                    except Exception as e:
                        event = "unsupported" if isinstance(e, RPCError) and e.code == -32601 else "delivery_error"
                        await self.event(event, message_id=msg["id"], case_id=msg["case_id"], error=str(e))
                        print("Delivery stopped; no automatic retry: " + str(e), flush=True)
                        return
            raise ConnectionError(self.rpc.failure)
        except Exception as e:
            self.trace.record("adapter_exception", {"error": str(e), "traceback": traceback.format_exc()})
            try:
                await self.event("adapter_error", error=str(e))
            except Exception:
                pass
            raise
        finally:
            try:
                await self.event("adapter_stopped")
            except Exception:
                pass
            for task in list(self.background):
                task.cancel()
            await asyncio.gather(*self.background, return_exceptions=True)
            if self.rpc:
                await self.rpc.close()
                self.trace.record("process_exit", {"returncode": self.rpc.process.returncode})
            if self.stderr_task:
                await self.stderr_task
            if self.stderr:
                self.stderr.close()
            self.trace.close()


class Codex(Native):
    async def start(self):
        await self.spawn([self.binary, "app-server", "--listen", "stdio://"], jsonrpc=False)
        await self.rpc.request("initialize", {
            "clientInfo": {"name": "multi_harness_proof", "version": VERSION},
            "capabilities": {"experimentalApi": True}})
        await self.rpc.notify("initialized")
        params = {"cwd": str(self.cwd), "sandbox": "read-only", "approvalPolicy": "untrusted",
                  "developerInstructions": INSTRUCTIONS,
                  "dynamicTools": [{"type": "function", **t} for t in TOOLS]}
        if self.model:
            params["model"] = self.model
        if self.reasoning:
            params["config"] = {"model_reasoning_effort": self.reasoning}
        result = await self.rpc.request("thread/start", params)
        self.session = result["thread"]["id"]
        await self.register("codex/app-server")

    async def request(self, method, params):
        if method == "item/tool/call":
            if params.get("threadId") != self.session:
                raise ValueError("Tool call from an unexpected thread")
            try:
                result = await asyncio.to_thread(self.client.tool, params["tool"], params["arguments"])
                return {"contentItems": [{"type": "inputText", "text": json.dumps(result)}], "success": True}
            except Exception as e:
                return {"contentItems": [{"type": "inputText", "text": str(e)}], "success": False}
        await self.event("permission_denied", method=method)
        if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            return {"decision": "decline"}
        if method == "item/permissions/requestApproval":
            return {"permissions": {}, "scope": "turn"}
        if method == "mcpServer/elicitation/request":
            return {"action": "decline", "content": None}
        raise ValueError("Unsupported server request: " + method)

    async def notification(self, method, params):
        if method in ("turn/started", "turn/completed"):
            turn = params.get("turn", {})
            self.busy = method == "turn/started"
            self.active_turn = turn.get("id") if self.busy else None
            await self.event("turn_started" if self.busy else "turn_completed",
                             turn_id=turn.get("id"), status=turn.get("status"))
        elif method == "item/agentMessage/delta":
            print(params.get("delta", ""), end="", flush=True)

    async def deliver(self, msg):
        was_busy = self.busy
        if msg["sender"] == "controller":
            # Control instructions are sent only between cases or to start a hold.
            result = await self.rpc.request("turn/start", {
                "threadId": self.session,
                "input": [{"type": "text", "text": render(msg)}]})
        else:
            # This native API queues a tool output in an active regular turn,
            # or creates a new turn when idle. Never substitute a fresh thread.
            result = await self.rpc.request("turn/start", {
                "threadId": self.session, "input": [],
                "toolOutput": {"name": "peer_message_received", "namespace": None,
                               "output": render(msg)}})
        await self.event("submitted", message_id=msg["id"], case_id=msg["case_id"],
                         transport="codex/turn/start", busy=was_busy,
                         turn_id=result.get("turn", {}).get("id"), acceptance="rpc_response")


class Grok(Native):
    async def start(self):
        argv = [self.binary, "--no-auto-update"]
        if self.model:
            argv += ["--model", self.model]
        if self.reasoning:
            argv += ["--effort", self.reasoning]
        await self.spawn(argv + ["agent", "stdio"], jsonrpc=True)
        result = await self.rpc.request("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        methods = [m["id"] for m in result.get("authMethods", [])]
        if methods:
            method = "xai.api_key" if os.environ.get("XAI_API_KEY") and "xai.api_key" in methods else "cached_token"
            if method not in methods:
                raise ValueError("Grok needs an existing local login or XAI_API_KEY; advertised methods: " + str(methods))
            await self.rpc.request("authenticate", {"methodId": method, "_meta": {"headless": True}})
        result = await self.rpc.request("session/new", {"cwd": str(self.cwd), "mcpServers": [{
            "name": "coord_proof", "command": sys.executable,
            "args": mcp_args(self.directory, self.peer), "env": []}]})
        self.session = result["sessionId"]
        await self.register("grok/acp")

    async def request(self, method, params):
        if method != "session/request_permission":
            raise ValueError("Unsupported ACP client method: " + method)
        # No automatic approval of arbitrary Grok tools. The harness presents
        # tool metadata; a human can approve a one-off request in this terminal.
        title = params.get("toolCall", {}).get("title", "unknown tool")
        options = params.get("options", [])
        allow = next((o for o in options if o.get("kind") == "allow_once"), None)
        print("\nGrok requests permission: " + str(title), flush=True)
        try:
            answer = await asyncio.to_thread(input, "Allow this once? [y/N] ") if allow else "n"
        except EOFError:
            answer = "n"
        if answer.lower() == "y" and allow:
            return {"outcome": {"outcome": "selected", "optionId": allow["optionId"]}}
        await self.event("permission_denied", method=method, title=title)
        return {"outcome": {"outcome": "cancelled"}}

    async def notification(self, method, params):
        if method == "session/update":
            update = params.get("update", {})
            if update.get("sessionUpdate") == "agent_message_chunk":
                print(update.get("content", {}).get("text", ""), end="", flush=True)

    async def prompt(self, msg):
        try:
            await self.event("turn_started", turn_id=msg["id"])
            await self.rpc.request("session/prompt", {
                "sessionId": self.session, "prompt": [{"type": "text", "text": render(msg)}]},
                timeout=self.client.config["timeout"] * 2 + 30,
                on_sent=lambda: self.event("submitted", message_id=msg["id"], case_id=msg["case_id"],
                                          transport="grok/session/prompt", busy=False, acceptance="request_written"))
            await self.event("turn_completed", turn_id=msg["id"])
        except Exception as e:
            await self.event("delivery_error", message_id=msg["id"], case_id=msg["case_id"], error=str(e))
        finally:
            self.busy = False

    async def deliver(self, msg):
        if self.busy:
            result = await self.rpc.request("_x.ai/interject", {
                "sessionId": self.session, "text": render(msg), "interjectionId": msg["id"]})
            await self.event("submitted", message_id=msg["id"], case_id=msg["case_id"],
                             transport="grok/_x.ai/interject", busy=True, acceptance=result)
        else:
            self.busy = True
            task = asyncio.create_task(self.prompt(msg))
            self.background.add(task)
            task.add_done_callback(self.background.discard)


def claude(client, binary, model, reasoning=None):
    directory = client.directory
    cwd = directory / "workspaces" / "claude"
    cwd.mkdir(parents=True, exist_ok=True)
    session = str(uuid.uuid4())
    cfg = directory / "claude-mcp.json"
    write_json(cfg, {"mcpServers": {"coord_proof": {
        "command": sys.executable, "args": mcp_args(directory, "claude") + ["--channel"]}}})
    argv = [binary, "--session-id", session, "--strict-mcp-config", "--mcp-config", str(cfg),
            "--debug-file", str(directory / "claude-debug.log"),
            "--dangerously-load-development-channels", "server:coord_proof",
            "--tools", "", "--allowedTools", ",".join("mcp__coord_proof__" + t["name"] for t in TOOLS),
            "--append-system-prompt", INSTRUCTIONS]
    if model:
        argv += ["--model", model]
    if reasoning:
        argv += ["--effort", reasoning]
    argv += ["--", "Participate in the proof using the appended instructions. Await controller messages on coord_proof."]
    # Register before the MCP child starts polling; the supplied UUID is the
    # explicit native --session-id, not an invented broker session identity.
    client.post("/register", {"session_id": session, "pid": os.getpid(),
                             "identity_source": "claude --session-id (launcher PID)",
                             "version": version("claude", binary), "transport": "claude/channel",
                             "model_requested": model or "harness default",
                             "reasoning_requested": reasoning or "not overridden"})
    print("Claude will ask you to enable the development channel. Leave this session open.", flush=True)
    trace = Trace(directory, "claude")
    trace.record("launch", {"argv": argv, "cwd": str(cwd), "session_id": session})
    try:
        result = subprocess.call(argv, cwd=cwd)
        trace.record("process_exit", {"returncode": result})
        return result
    finally:
        try:
            client.event("adapter_stopped")
        except Exception:
            pass
        trace.close()


def run_agent(directory, peer, binary=None, model=None, reasoning=None):
    binary = shutil.which(binary or peer)
    if not binary:
        raise ValueError(peer + " executable not found; install/login separately or use --binary /path/to/cli")
    client = Client(directory, peer)
    if peer == "claude":
        return claude(client, binary, model, reasoning)
    adapter = Codex(client, binary, model, reasoning) if peer == "codex" else Grok(client, binary, model, reasoning)
    asyncio.run(adapter.run())
    return 0
