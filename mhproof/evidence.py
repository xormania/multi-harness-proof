"""Independent native observations used to qualify relay evidence."""
import json
import sys

from .contract import TOOLS, validate_tool


GROK_MCP_MACHINERY = {"search_tool", "use_tool"}


def proof_name(name):
    for prefix in ("mcp__coord_proof__", "coord_proof__", ""):
        candidate = name.removeprefix(prefix) if isinstance(name, str) else ""
        if candidate in {tool["name"] for tool in TOOLS}:
            return candidate
    return None


def grok_tool_identity(update):
    """Prefer the vendor's versioned wire name over a human-facing ACP title."""
    meta = update.get("_meta")
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise ValueError("Grok tool metadata is not an object")
    if "x.ai/tool" not in meta:
        return update.get("title"), "exact ACP title (no canonical metadata)"
    identity = meta["x.ai/tool"]
    if not isinstance(identity, dict) or type(identity.get("version")) is not int or identity["version"] != 1:
        raise ValueError("Unsupported Grok x.ai/tool metadata version")
    name = identity.get("name")
    direct = (isinstance(name, str) and name.startswith("coord_proof__") and
              proof_name(name) and identity.get("namespace") == "mcp")
    machinery = (isinstance(name, str) and name in GROK_MCP_MACHINERY and
                 identity.get("namespace") == "grok_build")
    if not (direct or machinery):
        raise ValueError("Grok canonical tool identity is outside proof MCP/discovery/dispatch")
    title = update.get("title")
    title_name = title if isinstance(title, str) and title in GROK_MCP_MACHINERY else proof_name(title)
    canonical_name = name if machinery else proof_name(name)
    if title_name and title_name != canonical_name:
        raise ValueError("Grok canonical tool identity conflicts with its exact tool title")
    return name, "x.ai/tool v1"


def grok_tool_observation(update):
    """Separate catalog discovery from a precisely identified MCP execution.

    rawInput is the actual call input; canonical metadata's input is only a UI
    projection. Never infer a dispatch target or its arguments from display text.
    """
    wire_name, basis = grok_tool_identity(update)
    arguments = update.get("rawInput")
    result = {"identity_basis": basis, "wire_tool": wire_name, "wire_arguments": arguments}
    if wire_name == "search_tool":
        if (not isinstance(arguments, dict) or "query" not in arguments or
                set(arguments) - {"query", "limit"} or
                not isinstance(arguments["query"], str) or len(arguments["query"]) > 4096 or
                (arguments.get("limit") is not None and
                 (type(arguments["limit"]) is not int or not 0 <= arguments["limit"] <= 255))):
            raise ValueError("Grok search_tool must have a query and optional uint8/null limit")
        return {**result, "event": "native_discovery", "tool": wire_name, "arguments": arguments}
    if wire_name == "use_tool":
        if not isinstance(arguments, dict) or set(arguments) != {"tool_name", "tool_input"}:
            raise ValueError("Grok use_tool requires exactly tool_name and tool_input")
        target, tool_input = arguments["tool_name"], arguments["tool_input"]
        name = proof_name(target)
        if not name or target != "coord_proof__" + name:
            raise ValueError("Grok use_tool target is not an exact coord_proof MCP tool")
        validate_tool(name, tool_input)
        return {**result, "event": "native_tool", "tool": name, "arguments": tool_input}
    name = proof_name(wire_name)
    if not name:
        raise ValueError("Grok native tool is not an exact proof tool name")
    return {**result, "event": "native_tool", "tool": name, "arguments": arguments}


def call_signature(event):
    return json.dumps([event.get(k) for k in (
        "event", "tool", "arguments", "native_id", "origin", "wire_tool", "wire_arguments")], sort_keys=True)


def native_call(events, peer, name, arguments, sessions):
    """The ID comes from a native tool request/notification or Claude hook."""
    return next((event for event in events if event["event"] == "native_tool" and
                 event["peer"] == peer and event.get("tool") == name and
                 event.get("arguments") == arguments and
                 event.get("native_id") == sessions[peer]["session_id"] and
                 event.get("call_id") and event.get("origin") == {
                     "codex": "codex/item/tool/call", "grok": "grok/session/update",
                     "claude": "claude/PreToolUse"}[peer] and
                 not any(other["event"] in {"native_tool", "native_discovery"} and other["peer"] == peer and
                         other.get("call_id") == event["call_id"] and
                         call_signature(other) != call_signature(event) for other in events)), None)


def tool_audit(events, sessions):
    from collections import Counter
    def key(event):
        return (event["peer"], event["tool"], json.dumps(event["arguments"], sort_keys=True))
    actual = Counter(key(event) for event in events if event["event"] == "tool_called")
    observed = Counter()
    calls = set()
    signatures = {}
    conflicts = []
    discoveries = set()
    for event in events:
        if event["event"] not in {"native_tool", "native_discovery"} or event["peer"] not in sessions:
            continue
        call = (event["peer"], event.get("call_id"))
        signature = call_signature(event)
        if call in signatures and signatures[call] != signature:
            conflicts.append(event["seq"])
        signatures[call] = signature
        if event["event"] == "native_discovery":
            if (event["peer"] == "grok" and event.get("call_id") and
                    event.get("origin") == "grok/session/update" and
                    event.get("native_id") == sessions["grok"]["session_id"]):
                discoveries.add(call)
            continue
        if not native_call([event], event["peer"], event.get("tool"), event.get("arguments"), sessions):
            continue
        if call not in calls:
            calls.add(call)
            observed[key(event)] += 1
    missing = sum((actual - observed).values())
    violations = [event["seq"] for event in events if event["event"] in {"tool_violation", "native_identity_error"} or
                  (event["event"] in {"native_session", "native_tool", "native_discovery", "session_observed"} and
                   event["peer"] in sessions and event.get("native_id") != sessions[event["peer"]]["session_id"])]
    return {"status": "pass" if actual and not missing and not violations and not conflicts else "unverified",
            "relay_tool_calls": sum(actual.values()), "native_tool_calls": sum(observed.values()),
            "native_discovery_calls": len(discoveries),
            "unmatched_relay_calls": missing, "violation_seqs": violations,
            "conflicting_call_seqs": conflicts}


def claude_hook(client, packet):
    """Run-local native hook. Never prints context, approves tools, or edits settings."""
    native_id = packet.get("session_id")
    if not isinstance(native_id, str) or not native_id:
        client.event("native_identity_error", detail="Claude hook omitted session_id")
        return 2
    kind = packet.get("hook_event_name")
    client.event("native_session", native_id=native_id, origin="claude/" + str(kind))
    if kind == "PreToolUse":
        name = proof_name(packet.get("tool_name"))
        client.event("activity", state="active", basis="native hook", native_id=native_id,
                     turn_id=packet.get("prompt_id"))
        if not name:
            client.event("tool_violation", tool=packet.get("tool_name"), native_id=native_id)
            return 2
        client.event("native_tool", tool=name, arguments=packet.get("tool_input"),
                     call_id=packet.get("tool_use_id"), native_id=native_id,
                     turn_id=packet.get("prompt_id"), origin="claude/PreToolUse")
    elif kind in ("Stop", "SessionStart"):
        client.event("activity", state="idle", basis="native hook", native_id=native_id,
                     turn_id=packet.get("prompt_id"))
    elif kind == "StopFailure":
        client.event("adapter_error", error="Claude reported StopFailure")
    return 0


def run_claude_hook(directory):
    from .relay import Client
    packet = json.load(sys.stdin)
    return claude_hook(Client(directory, "claude"), packet)
