"""Installed-binary capability probes; no sessions, logins, or model calls."""
import json
from pathlib import Path
import subprocess
import tempfile


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
