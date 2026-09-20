"""Read only the native Claude transcript selected by this run's hooks."""
import fcntl
import json
import os
from pathlib import Path
import stat
import time

from .telemetry import Redactor

MAX_TRANSCRIPT_BYTES = 128 * 1024 * 1024


def capture_claude(directory, packet=None):
    from .relay import write_json
    directory = Path(directory)
    identity_path = directory / "claude-session.json"
    if not identity_path.exists():
        return {"status": "unavailable", "detail": "No run-local Claude session identity"}
    identity = json.loads(identity_path.read_text())["session_id"]
    lock_path = directory / "claude-transcript.lock"
    with lock_path.open("a") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        source_file = directory / "claude-transcript-source.json"
        status = {"status": "unavailable", "session_id": identity, "captured_at": time.time()}
        try:
            if packet is not None:
                if packet.get("session_id") != identity:
                    raise ValueError("Hook identity does not match this run")
                source = packet.get("transcript_path")
                if source:
                    path = Path(source).expanduser()
                    if not path.is_absolute() or path.name != identity + ".jsonl":
                        raise ValueError("Transcript path must name this exact native session")
                    if source_file.exists() and json.loads(source_file.read_text())["path"] != str(path):
                        raise ValueError("Native transcript path changed within this run")
                    write_json(source_file, {"path": str(path), "session_id": identity})
            if not source_file.exists():
                status["detail"] = "Native hook has not supplied transcript_path"
            else:
                source = json.loads(source_file.read_text())
                if source["session_id"] != identity or Path(source["path"]).name != identity + ".jsonl":
                    raise ValueError("Saved transcript identity mismatch")
                descriptor = os.open(source["path"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, "rb") as handle:
                    info = os.fstat(handle.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                        raise ValueError("Transcript must be a regular file owned by this user")
                    data = handle.read(MAX_TRANSCRIPT_BYTES + 1)
                if len(data) > MAX_TRANSCRIPT_BYTES:
                    raise ValueError("Transcript exceeds capture limit; source remains untouched")
                config = json.loads((directory / "run.json").read_text())
                redactor = Redactor(config["tokens"].values())
                lines, partial = [], False
                raw_lines = data.splitlines(keepends=True)
                for index, raw in enumerate(raw_lines):
                    if not raw.strip():
                        continue
                    try:
                        row = json.loads(raw)
                    except (ValueError, UnicodeDecodeError):
                        if index == len(raw_lines) - 1 and not raw.endswith(b"\n"):
                            partial = True
                            break
                        raise ValueError("Malformed native transcript record")
                    if not isinstance(row, dict):
                        raise ValueError("Native transcript record must be an object")
                    for key in ("sessionId", "session_id"):
                        if row.get(key) and row[key] != identity:
                            raise ValueError("Transcript contains another native session")
                    lines.append(json.dumps(redactor.value(row), ensure_ascii=False))
                target = directory / "claude-transcript.jsonl"
                temp = directory / "claude-transcript.jsonl.tmp"
                with temp.open("w", encoding="utf-8") as handle:
                    os.chmod(temp, 0o600)
                    handle.write("\n".join(lines) + ("\n" if lines else ""))
                temp.replace(target)
                status.update(status="partial" if partial else "captured", records=len(lines),
                              source_bytes=len(data), partial_final_line=partial)
        except Exception as error:
            status.update(status="unavailable" if isinstance(error, FileNotFoundError) else "error",
                          detail=type(error).__name__ + ": " + str(error))
        write_json(directory / "claude-transcript-capture.json", status)
        return status
