"""Installed-binary probes and native startup observations; no model calls."""
import json
from pathlib import Path
import re
import subprocess
import tempfile


class ClaudeChannelLog:
    """Observe handler registration in this run's fresh native debug log.

    This is a version-sensitive launch gate, not an ingestion acknowledgement or
    independent session identity. The channel-delivered ready report still has
    to match native tool evidence. Missing/changed log formats never imply ready.
    """
    marker = re.compile(
        rb'(?P<time>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z) '
        rb'\[DEBUG\] MCP server "coord_proof": Channel notifications registered\r?\n')

    def __init__(self, path):
        self.path = Path(path)
        self.offset = 0
        self.pending = b""
        self.discarding = False
        self.line = 0
        self.observation = None

    def poll(self):
        if self.observation:
            return self.observation
        try:
            with self.path.open("rb") as stream:
                stream.seek(0, 2)
                if stream.tell() < self.offset:
                    raise ValueError("Claude debug log was truncated before channel readiness")
                stream.seek(self.offset)
                chunk = stream.read(65536)
        except FileNotFoundError:
            return None
        self.offset += len(chunk)
        parts = (self.pending + chunk).split(b"\n")
        self.pending = parts.pop()
        for part in parts:
            self.line += 1
            match = None if self.discarding else self.marker.fullmatch(part + b"\n")
            self.discarding = False
            if match:
                self.observation = {"source": self.path.name, "source_line": self.line,
                    "native_timestamp": match["time"].decode(), "server": "coord_proof",
                    "origin": "claude/debug-log",
                    "basis": "handler registration log; not a delivery acknowledgement"}
                return self.observation
        # Bound memory without accepting a suffix of an oversized unrelated line.
        if len(self.pending) > 65536 or self.discarding:
            self.pending = b""
            self.discarding = True
        return None


def supports_tool_output(documents):
    def walk(node, key=""):
        if isinstance(node, dict):
            if key == "TurnStartParams" or node.get("title") == "TurnStartParams":
                yield node
            for name, value in node.items():
                yield from walk(value, name)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)
    definitions = [node for document in documents for node in walk(document)]
    if not definitions:
        return None
    return any({"input", "threadId", "toolOutput"} <= set(node.get("properties", {}))
               for node in definitions)


def codex_capability(binary):
    try:
        help_result = subprocess.run([binary, "app-server", "generate-json-schema", "--help"],
                                     capture_output=True, text=True, timeout=15)
        if help_result.returncode:
            return {"status": "unverified", "detail": "Cannot inspect schema generator help"}
        with tempfile.TemporaryDirectory(prefix="mhproof-schema-") as temporary:
            argv = [binary, "app-server", "generate-json-schema", "--out", temporary]
            if "--experimental" in help_result.stdout:
                argv.append("--experimental")
            result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
            if result.returncode:
                return {"status": "unverified", "detail": "Installed schema generator failed",
                        "returncode": result.returncode}
            documents = [json.loads(path.read_text()) for path in Path(temporary).rglob("*.json")]
            supported = supports_tool_output(documents)
            return {"status": "supported" if supported else "unsupported" if supported is False else "unverified",
                    "capability": "TurnStartParams.toolOutput",
                    "detail": "Checked installed-binary JSON schema",
                    "schema_files": len(documents)}
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return {"status": "unverified", "detail": type(error).__name__ + " during schema probe"}
