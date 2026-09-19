"""Independent native observations used to qualify relay evidence."""
import json
import sys

from .contract import TOOLS


def proof_name(name):
    for prefix in ("mcp__coord_proof__", "coord_proof__", ""):
        candidate = name.removeprefix(prefix) if isinstance(name, str) else ""
        if candidate in {tool["name"] for tool in TOOLS}:
            return candidate
    return None


def native_call(events, peer, name, arguments, sessions):
    """The ID comes from a native tool request/notification or Claude hook."""
    return next((event for event in events if event["event"] == "native_tool" and
                 event["peer"] == peer and event.get("tool") == name and
                 event.get("arguments") == arguments and
                 event.get("native_id") == sessions[peer]["session_id"] and
                 event.get("call_id") and event.get("origin") == {
                     "codex": "codex/item/tool/call", "grok": "grok/session/update",
                     "claude": "claude/PreToolUse"}[peer]), None)


def tool_audit(events, sessions):
    from collections import Counter
    def key(event):
        return (event["peer"], event["tool"], json.dumps(event["arguments"], sort_keys=True))
    actual = Counter(key(event) for event in events if event["event"] == "tool_called")
    observed = Counter()
    calls = set()
    for event in events:
        if event["event"] != "native_tool" or event["peer"] not in sessions:
            continue
        if not native_call([event], event["peer"], event.get("tool"), event.get("arguments"), sessions):
            continue
        call = (event["peer"], event["call_id"])
        if call not in calls:
            calls.add(call)
            observed[key(event)] += 1
    missing = sum((actual - observed).values())
    violations = [event["seq"] for event in events if event["event"] in {"tool_violation", "native_identity_error"} or
                  (event["event"] in {"native_session", "native_tool", "session_observed"} and
                   event["peer"] in sessions and event.get("native_id") != sessions[event["peer"]]["session_id"])]
    return {"status": "pass" if actual and not missing and not violations else "unverified",
            "relay_tool_calls": sum(actual.values()), "native_tool_calls": sum(observed.values()),
            "unmatched_relay_calls": missing, "violation_seqs": violations}


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
