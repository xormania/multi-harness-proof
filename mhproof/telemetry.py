"""Local, flushed JSONL traces and an explicit diagnostic-bundle allowlist."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
import threading
import time
import zipfile

SENSITIVE_KEY = re.compile(r"api.?key|authorization|password|credential|private.?key|access.?token|refresh.?token|^tokens?$", re.I)


class Redactor:
    def __init__(self, extra_secrets=()):
        self.secrets = [str(s) for s in extra_secrets if s]
        self.secrets += [v for k, v in os.environ.items() if SENSITIVE_KEY.search(k) and len(v) >= 8]

    def text(self, text):
        for secret in sorted(set(self.secrets), key=len, reverse=True):
            text = text.replace(secret, "[REDACTED]")
        text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
        return text

    def value(self, value):
        if isinstance(value, dict):
            return {k: "[REDACTED]" if SENSITIVE_KEY.search(k) else self.value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.value(v) for v in value]
        if isinstance(value, str):
            return self.text(value)
        return value


class Trace:
    def __init__(self, directory, component):
        self.path = Path(directory) / (component + "-trace.jsonl")
        config = json.loads((Path(directory) / "run.json").read_text())
        self.redactor = Redactor(config["tokens"].values())
        self.lock = threading.Lock()
        self.handle = self.path.open("a", encoding="utf-8")
        os.chmod(self.path, 0o600)
        self.component = component

    def record(self, direction, payload):
        entry = {"time": time.time(), "monotonic_ns": time.monotonic_ns(),
                 "pid": os.getpid(), "component": self.component,
                 "direction": direction, "payload": self.redactor.value(payload)}
        with self.lock:
            if not self.handle.closed:
                self.handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self.handle.flush()

    def close(self):
        with self.lock:
            self.handle.close()


def manifest(root, peers, timeout):
    root = Path(root)
    paths = [root / "proof.py", *sorted((root / "mhproof").glob("*.py")),
             *sorted((root / "fixtures").glob("*.json"))]
    return {"python": sys.version, "python_executable": sys.executable,
            "platform": platform.platform(), "machine": platform.machine(),
            "peers": list(peers), "timeout_seconds": timeout,
            "source_sha256": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
            "environment_values_collected": False,
            "telemetry_destination": "local files only"}


def bundle(directory, output):
    directory, output = Path(directory).resolve(), Path(output).resolve()
    # Never archive credentials, workspaces, unrelated files, or native histories.
    config = json.loads((directory / "run.json").read_text())
    redactor = Redactor(config["tokens"].values())
    allowed = {"report.json", "manifest.json", "events.jsonl", "claude-debug.log"}
    allowed |= {p + "-stderr.log" for p in ("codex", "grok")}
    allowed |= {p + "-trace.jsonl" for p in ("codex", "grok", "claude", "mcp-claude", "mcp-grok")}
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
        included = []
        for name in sorted(allowed):
            path = directory / name
            if not path.is_file() or path.is_symlink():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if name.endswith(".json"):
                text = json.dumps(redactor.value(json.loads(text)), indent=2) + "\n"
            elif name.endswith(".jsonl"):
                lines = []
                for line in text.splitlines():
                    try:
                        lines.append(json.dumps(redactor.value(json.loads(line))))
                    except ValueError:
                        # Preserve a partial final line from an interrupted run.
                        lines.append(json.dumps({"partial_line": redactor.text(line)}))
                text = "\n".join(lines) + "\n"
            else:
                text = redactor.text(text)
            archive.writestr("diagnostics/" + name, text)
            included.append(name)
        archive.writestr("diagnostics/README.txt",
            "Local proof diagnostics. Run credentials/configs/workspaces excluded.\n"
            "Known secrets redacted; inspect native logs and local paths before sharing.\n"
            "Bundling sends no data anywhere.\nFiles: " + ", ".join(included) + "\n")
    os.chmod(output, 0o600)
    return output
