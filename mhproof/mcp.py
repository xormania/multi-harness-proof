"""Small stdio MCP server; Claude also receives channel notifications here."""
import concurrent.futures
import json
import os
import sys
import threading

from . import VERSION
from .contract import INSTRUCTIONS, TOOLS, render
from .relay import Client
from .telemetry import Trace


def run_mcp(directory, peer, channel=False):
    client = Client(directory, peer)
    trace = Trace(directory, "mcp-" + peer)
    output_lock = threading.Lock()
    initialized = threading.Event()
    stopped = threading.Event()
    protocol_version = "2025-06-18"  # The one implemented version; clients may negotiate down to it.

    def write(packet):
        with output_lock:
            trace.record("mcp_out", packet)
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", **packet}) + "\n")
            sys.stdout.flush()

    def channels():
        initialized.wait()
        while not stopped.is_set():
            try:
                polled = client.post("/poll", {})
                if polled["stopped"]:
                    return
                msg = polled["message"]
                if msg:
                    write({"method": "notifications/claude/channel", "params": {
                        "content": render(msg),
                        "meta": {"message_id": msg["id"], "sender_id": msg["sender"]}}})
                    client.event("submitted", message_id=msg["id"], case_id=msg["case_id"],
                                 transport="claude/channel", acceptance="notification_written")
            except Exception as e:
                print("Channel delivery stopped: " + str(e), file=sys.stderr)
                return

    def handle(packet):
        ident = packet.get("id")
        method = packet.get("method")
        try:
            if method == "notifications/initialized":
                client.event("mcp_ready", channel=channel, protocol_version=protocol_version)
                if os.environ.get("CLAUDE_CODE_SESSION_ID"):
                    client.event("session_observed", native_id=os.environ["CLAUDE_CODE_SESSION_ID"])
                initialized.set()
                return
            if ident is None:
                return
            if method == "initialize":
                caps = {"tools": {}}
                if channel:
                    caps["experimental"] = {"claude/channel": {}}
                result = {"protocolVersion": protocol_version, "capabilities": caps,
                          "serverInfo": {"name": "coord_proof", "version": VERSION},
                          "instructions": INSTRUCTIONS}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                params = packet.get("params", {})
                try:
                    value = client.tool(params["name"], params.get("arguments", {}))
                    result = {"content": [{"type": "text", "text": json.dumps(value)}]}
                except Exception as e:
                    result = {"isError": True, "content": [{"type": "text", "text": str(e)}]}
            else:
                write({"id": ident, "error": {"code": -32601, "message": "Method not implemented: " + str(method)}})
                return
            write({"id": ident, "result": result})
        except Exception as e:
            if ident is not None:
                write({"id": ident, "error": {"code": -32603, "message": str(e)}})

    if channel:
        threading.Thread(target=channels, daemon=True).start()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        try:
            for line in sys.stdin:
                try:
                    packet = json.loads(line)
                    trace.record("mcp_in", packet)
                    if not isinstance(packet, dict):
                        raise ValueError("Expected an object")
                except ValueError:
                    write({"id": None, "error": {"code": -32700, "message": "Invalid JSON-RPC"}})
                    continue
                if packet.get("method") == "tools/call":
                    pool.submit(handle, packet)
                else:
                    handle(packet)
        finally:
            stopped.set()
    trace.close()
