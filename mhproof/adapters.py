"""Native harness adapters. These processes keep one session for the whole run."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import traceback
import uuid

from . import VERSION
from .contract import INSTRUCTIONS, TOOLS, render
from .compat import codex_capability
from .evidence import proof_name
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

    async def observe_session(self, native_id, origin):
        if not native_id or native_id != self.session:
            await self.event("native_identity_error", native_id=native_id, origin=origin,
                             expected_id=self.session)
            raise ValueError("Native session identity missing or changed: " + origin)
        await self.event("native_session", native_id=native_id, origin=origin)

    async def observe_tool(self, name, arguments, call_id, native_id, origin, turn_id=None):
        await self.observe_session(native_id, origin)
        name = proof_name(name)
        if not name:
            await self.event("tool_violation", native_id=native_id, origin=origin, call_id=call_id)
            return False
        await self.event("native_tool", native_id=native_id, origin=origin, call_id=call_id,
                         tool=name, arguments=arguments, turn_id=turn_id)
        return True

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
        capability = await asyncio.to_thread(codex_capability, self.binary)
        await self.event("protocol_capability", **capability)
        if capability["status"] != "supported":
            await self.event("unsupported", detail="Installed Codex toolOutput capability not established",
                             probe=capability)
            raise ValueError("Codex must expose TurnStartParams.toolOutput; run doctor")
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
            allowed = await self.observe_tool(params.get("tool"), params.get("arguments"),
                params.get("callId"), params.get("threadId"), "codex/item/tool/call", params.get("turnId"))
            if not allowed:
                raise ValueError("Only proof tools belong in this experiment")
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
        if "threadId" in params:
            await self.observe_session(params["threadId"], "codex/" + method)
        if method in ("turn/started", "turn/completed"):
            if "threadId" not in params:
                await self.observe_session(None, "codex/" + method)
            turn = params.get("turn", {})
            self.busy = method == "turn/started"
            self.active_turn = turn.get("id") if self.busy else None
            await self.event("turn_started" if self.busy else "turn_completed",
                             turn_id=turn.get("id"), status=turn.get("status"), native_id=params["threadId"])
            await self.event("activity", state="active" if self.busy else "idle", basis="native turn event",
                             native_id=params["threadId"], turn_id=turn.get("id"))
        elif method in ("item/started", "item/completed"):
            item = params.get("item", {})
            kind = item.get("type", "")
            if kind in {"commandExecution", "fileChange", "webSearch", "imageView", "imageGeneration",
                        "mcpToolCall", "collabAgentToolCall"} or (kind.endswith("ToolCall") and kind != "dynamicToolCall"):
                await self.event("tool_violation", tool=kind, item_id=item.get("id"), native_id=params.get("threadId"))
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
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.permission_lock = asyncio.Lock()
        self.prompts_in_flight = 0
        self.native_busy = False  # A newly created session has no prompt yet.
        self.pending_interjections = {}
        self.completed_turns = set()

    async def activity(self, basis):
        active = bool(self.prompts_in_flight or self.pending_interjections or self.native_busy)
        self.busy = active or self.native_busy is None
        await self.event("activity", state="unknown" if self.native_busy is None else "active" if active else "idle",
                         basis=basis, native_id=self.session,
                         prompts_in_flight=self.prompts_in_flight,
                         pending_interjections=list(self.pending_interjections))

    async def permission_answer(self):
        # A cancellable terminal read: shutdown cannot strand an input() worker.
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        descriptor = sys.stdin.fileno()
        print("Allow this once? [y/N] ", end="", flush=True)
        answer = bytearray()
        def ready():
            if not future.done():
                try:
                    char = os.read(descriptor, 1)
                    if not char or char == b"\n":
                        future.set_result(answer.decode("utf-8", errors="replace").strip())
                    else:
                        answer.extend(char)
                except OSError as error:
                    future.set_exception(error)
        loop.add_reader(descriptor, ready)
        try:
            return await future
        finally:
            loop.remove_reader(descriptor)

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
        await self.observe_session(params.get("sessionId"), "grok/session/request_permission")
        # No automatic approval of arbitrary Grok tools. The harness presents
        # tool metadata; a human can approve a one-off request in this terminal.
        title = params.get("toolCall", {}).get("title", "unknown tool")
        options = params.get("options", [])
        allow = next((o for o in options if o.get("kind") == "allow_once"), None)
        async with self.permission_lock:
            print("\nGrok requests permission: " + str(title), flush=True)
            try:
                answer = await self.permission_answer() if allow else "n"
            except (EOFError, OSError, ValueError):
                answer = "n"
        if answer.lower() == "y" and allow:
            return {"outcome": {"outcome": "selected", "optionId": allow["optionId"]}}
        await self.event("permission_denied", method=method, title=title)
        return {"outcome": {"outcome": "cancelled"}}

    async def notification(self, method, params):
        if method not in {"session/update", "_x.ai/session/update", "_x.ai/session/prompt_complete"}:
            return
        # Session creation can emit configuration updates before session/new returns.
        if self.session is None:
            return
        await self.observe_session(params.get("sessionId"), "grok/" + method)
        update = params.get("update", {})
        kind = update.get("sessionUpdate")
        if method == "_x.ai/session/prompt_complete" or kind == "turn_completed":
            turn = params.get("promptId") if method.endswith("prompt_complete") else update.get("prompt_id")
            if not turn:
                await self.event("adapter_error", error="Grok completion omitted native prompt ID")
                return
            if turn not in self.completed_turns:
                self.completed_turns.add(turn)
                self.native_busy = False
                await self.event("turn_completed", turn_id=turn, native_id=self.session,
                                 status=params.get("stopReason", update.get("stop_reason")), origin=method)
                await self.activity("native completion plus outstanding deliveries")
        elif method == "session/update":
            if kind in {"agent_message_chunk", "agent_thought_chunk", "tool_call", "tool_call_update"}:
                self.native_busy = True
            if kind == "tool_call":
                name, arguments = proof_name(update.get("title")), update.get("rawInput")
                await self.observe_tool(update.get("title"), arguments, update.get("toolCallId"),
                                        params["sessionId"], "grok/session/update")
                for ident, msg in list(self.pending_interjections.items()):
                    args = arguments if isinstance(arguments, dict) else {}
                    matches = (msg["kind"] == "challenge" and name == "proof_send" and args.get("kind") == "reply" or
                               msg["kind"] == "reply" and name == "proof_report" and args.get("phase") == "received")
                    if matches and args.get("case_id") == msg["case_id"] and args.get("nonce") == msg.get("nonce"):
                        self.pending_interjections.pop(ident)
                    elif msg["sender"] == "controller" and name and (
                            args.get("case_id") == msg["case_id"] or
                            msg["case_id"].startswith("work-batch-") and name == "proof_work_next" or
                            msg["case_id"] == "work-burst" and name == "proof_send" and args.get("kind") == "challenge"):
                        self.pending_interjections.pop(ident)
            if kind in {"agent_message_chunk", "agent_thought_chunk", "tool_call", "tool_call_update"}:
                await self.activity("native ACP activity")
            if kind == "agent_message_chunk":
                print(update.get("content", {}).get("text", ""), end="", flush=True)

    async def prompt(self, msg):
        try:
            await self.rpc.request("session/prompt", {
                "sessionId": self.session, "prompt": [{"type": "text", "text": render(msg)}]},
                timeout=self.client.config["timeout"] * 3 + 30,
                on_sent=lambda: self.event("submitted", message_id=msg["id"], case_id=msg["case_id"],
                                          transport="grok/session/prompt", busy=False, acceptance="request_written"))
        except Exception as e:
            self.native_busy = None
            await self.event("delivery_error", message_id=msg["id"], case_id=msg["case_id"],
                             error=str(e) or type(e).__name__, native_activity="unknown")
        finally:
            self.prompts_in_flight -= 1
            await self.activity("RPC ended; native completion still required")

    async def deliver(self, msg):
        if self.busy:
            self.pending_interjections[msg["id"]] = msg
            await self.activity("interjection awaiting native handling evidence")
            result = await self.rpc.request("_x.ai/interject", {
                "sessionId": self.session, "text": render(msg), "interjectionId": msg["id"]})
            await self.event("submitted", message_id=msg["id"], case_id=msg["case_id"],
                             transport="grok/_x.ai/interject", busy=True, acceptance=result)
        else:
            self.prompts_in_flight += 1
            self.native_busy = True
            await self.activity("prompt request in flight; not a native turn-start event")
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
    settings = directory / "claude-settings.json"
    command = shlex.join([sys.executable, str(ENTRY), "claude-hook", "--dir", str(directory)])
    write_json(settings, {"hooks": {event: [{"hooks": [{"type": "command", "command": command}]}]
                                   for event in ("SessionStart", "PreToolUse", "Stop", "StopFailure")}})
    argv = [binary, "--session-id", session, "--strict-mcp-config", "--mcp-config", str(cfg),
            "--settings", str(settings),
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
