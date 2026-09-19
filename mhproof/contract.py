"""The entire model-visible protocol lives here."""
import json

PEERS = ("codex", "claude", "grok")
INSTRUCTIONS = """You are participating in a local multi-harness messaging experiment.
Use only the proof_* tools (possibly with an MCP prefix).
For Grok's MCP interface, search_tool may discover the coord_proof catalog
(query="coord_proof"). Then use_tool may invoke only coord_proof__proof_send,
coord_proof__proof_report, coord_proof__proof_hold, coord_proof__proof_work_next,
or coord_proof__proof_work_submit. Set tool_name to that exact qualified name
and tool_input to the proof tool's JSON arguments object. Discovery is not a
proof action. Never put PRIVATE_MEMORY or message contents in a search query.
Do not use shell, file, web, delegation, or configuration tools. Do not create
other agents. Do not inspect the run directory. Keep responses short.
The controller will give you a PRIVATE_MEMORY value once. Remember it within
this conversation. Never put it in a challenge; include it only in a reply or
in a ready report when asked. Do not retrieve it from files or other tools.

Process incoming JSON envelopes from the controller or another peer:
* Controller instruction: do exactly the requested proof tool operation.
* kind=challenge from a peer: call proof_send with to=that peer, kind=reply,
  the exact case_id and nonce from the challenge, memory=your PRIVATE_MEMORY.
* kind=reply from a peer: call proof_report with phase=received, the exact
  case_id and nonce, memory=the reply's memory, and peer=the reply's sender.
Do not reply to a reply with another message. Each message needs one tool call.
Channels/tool outputs can arrive while you are working; handle them at your
next opportunity, including after proof_hold returns. A tool returning queued
does not mean a peer has received anything. Never fabricate success.
Finish the controller's requested task (which may have several steps), then
end your turn and await the next message. During a work fixture, solve only the
single batch requested, then end your turn. The controller releases later batches.
Handle incoming peer messages at your next opportunity between these turns.
"""


def schema(properties, required):
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


STR = {"type": "string"}
TOOLS = [
    {"name": "proof_send", "description": "Send a proof challenge or reply to a different running harness.",
     "inputSchema": schema({"to": {"type": "string", "enum": list(PEERS)},
                            "kind": {"type": "string", "enum": ["challenge", "reply"]},
                            "case_id": STR, "nonce": STR, "memory": STR},
                           ["to", "kind", "case_id", "nonce", "memory"])},
    {"name": "proof_report", "description": "Report readiness or receipt of a peer reply. Copy values exactly.",
     "inputSchema": schema({"phase": {"type": "string", "enum": ["ready", "received"]},
                            "case_id": STR, "nonce": STR, "memory": STR, "peer": STR},
                           ["phase", "case_id", "nonce", "memory", "peer"])},
    {"name": "proof_hold", "description": "Remain inside this tool until the controller releases it. Used to test busy delivery.",
     "inputSchema": schema({"case_id": STR}, ["case_id"])},
    {"name": "proof_work_next", "description": "Get the next synthetic build-log batch. Submit an answer before fetching another.",
     "inputSchema": schema({}, [])},
    {"name": "proof_work_submit", "description": "Submit build-log triage: only each job's highest attempt counts; count only FAIL jobs.",
     "inputSchema": schema({"batch": {"type": "integer", "minimum": 0},
                            "failed_jobs": {"type": "array", "items": STR},
                            "failed_tests": {"type": "integer", "minimum": 0}},
                           ["batch", "failed_jobs", "failed_tests"])},
]


def validate_tool(name, args):
    spec = next((t for t in TOOLS if t["name"] == name), None)
    if not spec or not isinstance(args, dict):
        raise ValueError("Unknown proof tool or invalid arguments")
    s = spec["inputSchema"]
    if set(args) != set(s["required"]):
        raise ValueError("Supply exactly these fields: " + ", ".join(s["required"]))
    for key, value in args.items():
        prop = s["properties"][key]
        if prop["type"] == "integer":
            if type(value) is not int or not 0 <= value <= 100000:
                raise ValueError("Invalid integer field: " + key)
            continue
        if prop["type"] == "array":
            if not isinstance(value, list) or len(value) > 100 or any(not isinstance(x, str) or len(x) > 200 for x in value):
                raise ValueError("Invalid string array: " + key)
            continue
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError("Tool fields must be strings of at most 4096 characters")
        if "enum" in s["properties"][key] and value not in s["properties"][key]["enum"]:
            raise ValueError("Invalid " + key)


def render(envelope):
    return "PROOF MESSAGE\n" + json.dumps(envelope, ensure_ascii=False)
