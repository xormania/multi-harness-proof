"""Operator-owned tmux lifecycle. A run is never resumed or overwritten."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import time

from .adapters import version
from .capture import capture_claude
from .compat import codex_capability
from .experiments import load_config
from .relay import write_json
from .reporting import read_json, render, summarize
from .telemetry import bundle

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "proof.py"


class Tmux:
    def call(self, *args, check=True):
        try:
            result = subprocess.run(["tmux", *map(str, args)], text=True, capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            if check:
                raise
            return ""
        if check and result.returncode:
            raise RuntimeError("tmux: " + result.stderr.strip())
        return result.stdout.strip()

    def owned(self, record):
        # Option commands parse target-pane. The colon makes the exact session
        # component explicit; a bare '=name' is otherwise parsed as a pane.
        actual = self.call("show-options", "-qv", "-t", "=" + record["session"] + ":", "@mhproof_owner", check=False)
        return actual == record["owner"]

    def create(self, record):
        pane = self.call("new-session", "-d", "-P", "-F", "#{pane_id}", "-s", record["session"],
                         "-n", "dashboard", "-c", ROOT, sys.executable, "-c", "import time; time.sleep(60)")
        self.call("set-option", "-t", "=" + record["session"] + ":", "@mhproof_owner", record["owner"])
        self.call("set-option", "-w", "-t", pane, "remain-on-exit", "on")
        self.call("set-option", "-w", "-t", pane, "automatic-rename", "off")
        self.call("set-option", "-w", "-t", pane, "allow-rename", "off")
        return pane

    def launch(self, record, peer, argv):
        if not self.owned(record):
            raise RuntimeError("Managed tmux session is missing or ownership differs")
        pane = self.call("new-window", "-d", "-P", "-F", "#{pane_id}", "-t", record["session"] + ":",
                         "-n", peer, "-c", ROOT, shlex.join(argv))
        # Retain ownership even if configuring the new window fails afterwards.
        record["panes"][peer] = pane
        write_json(Path(record["directory"]) / "session.json", record)
        self.call("set-option", "-w", "-t", pane, "remain-on-exit", "on")
        self.call("set-option", "-w", "-t", pane, "automatic-rename", "off")
        self.call("set-option", "-w", "-t", pane, "allow-rename", "off")
        return pane

    def select(self, record, name):
        if self.owned(record):
            self.call("select-window", "-t", record["session"] + ":" + name, check=False)

    def pane_owned(self, record, pane):
        return self.owned(record) and self.call("display-message", "-p", "-t", pane,
                                              "#{session_name}", check=False) == record["session"]

    def finish_peer(self, record, peer, pane, directory):
        if not self.pane_owned(record, pane):
            return {"peer": peer, "status": "already-closed-or-unowned"}
        from .telemetry import Redactor
        tokens = read_json(Path(directory) / "run.json", {}).get("tokens", {})
        text = self.call("capture-pane", "-p", "-S", "-", "-t", pane, check=False)
        path = Path(directory) / (peer + "-terminal.log")
        path.write_text(Redactor(tokens.values()).text(text))
        os.chmod(path, 0o600)
        dead = self.call("display-message", "-p", "-t", pane, "#{pane_dead}:#{pane_dead_status}", check=False)
        self.call("kill-pane", "-t", pane)
        return {"peer": peer, "status": "closed", "pane_state_before_close": dead}

    def close(self, record):
        if self.owned(record):
            self.call("kill-session", "-t", "=" + record["session"])

    def attach(self, record):
        if not self.owned(record):
            raise ValueError("This run has no open managed tmux session; use status or collect")
        command = "switch-client" if os.environ.get("TMUX") else "attach-session"
        return subprocess.call(["tmux", command, "-t", "=" + record["session"]])


def paths(directory):
    job = Path(directory).resolve()
    return job, job / "run" if (job / "session.json").exists() else job


def collect(directory):
    job, run = paths(directory)
    record = read_json(job / "session.json", {})
    if not (run / "run.json").exists() and not record:
        raise ValueError("No initialized proof run to collect; inspect controller.log and session.json")
    # A final Stop hook can predate the final native transcript write.
    capture_claude(run)
    summary = summarize(run)
    if record and not summary["finished"] and record.get("state") in {"finished", "error", "stopped"}:
        summary.update(status="incomplete", detail=record.get("error", "Controller exited without a final report"))
    write_json(job / "summary.json", summary)
    (job / "summary.txt").write_text(render(summary))
    os.chmod(job / "summary.txt", 0o600)
    for name in ("session.json", "controller.log", "preflight.json", "summary.json", "summary.txt"):
        source = job / name
        if job != run and run.exists() and source.exists():
            shutil.copyfile(source, run / name)
            os.chmod(run / name, 0o600)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archives = job / "archives"
    archives.mkdir(exist_ok=True, mode=0o700)
    output = archives / ("diagnostics-" + stamp + ".zip")
    bundle(run if run.exists() else job, output)
    return summary, output


def prepare(args):
    if args.config:
        _, settings = load_config(args.config)
    elif args.previous:
        _, settings = load_config(Path(args.previous) / "plan.json")
    else:
        # Use the existing strict experiment schema for every launch.
        settings = {"schema_version": 1, "name": "live", "mode": "live", "output_root": str(ROOT / "runs"),
                    "peers": ["codex", "claude", "grok"], "timeout": 180, "startup_timeout": 600,
                    "agents": {peer: {"profile": "coordination"} for peer in ("codex", "claude", "grok")},
                    "checks": {"work": True}}
    if settings["mode"] != "live":
        raise ValueError("Managed tmux start uses live harnesses; run mock plans with proof.py experiment")
    if args.timeout is not None:
        settings["timeout"] = args.timeout
    if args.startup_timeout is not None:
        settings["startup_timeout"] = args.startup_timeout
    if args.no_work:
        settings["checks"]["work"] = False
    if args.label:
        from .experiments import name
        settings["name"] = name(args.label, "run label")
    for peer in settings["peers"]:
        for key in ("model", "reasoning"):
            override = getattr(args, peer + "_" + key, None)
            if override is not None:
                settings["agents"].setdefault(peer, {})[key] = override
    # Resolved experiment settings include the mock-only scenarios key.
    plan = {k: v for k, v in settings.items() if k != "scenarios"}
    directory = Path(args.dir).resolve() if args.dir else Path(settings["output_root"]) / (
        settings["name"] + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3))
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    from .workload import FIXTURE
    fixture = Path(plan.get("fixture", FIXTURE))
    shutil.copyfile(fixture, directory / "fixture.json")
    os.chmod(directory / "fixture.json", 0o600)
    plan["fixture"] = str(directory / "fixture.json")
    write_json(directory / "plan.json", plan)
    _, settings = load_config(directory / "plan.json")
    write_json(directory / "settings.json", settings)
    owner = secrets.token_hex(16)
    record = {"schema_version": 1, "directory": str(directory), "session": "mhproof-" + owner[:12],
              "owner": owner, "state": "preflight", "created": time.time(), "panes": {}}
    write_json(directory / "session.json", record)
    return directory, settings, record


def preflight(settings):
    found = {}
    if not shutil.which("tmux"):
        raise ValueError("tmux is not installed")
    for peer in settings["peers"]:
        binary = shutil.which(settings["agents"][peer]["binary"])
        found[peer] = {"binary": binary, "version": version(peer, binary) if binary else "not installed"}
        if peer == "codex" and binary:
            found[peer]["tool_output"] = codex_capability(binary)
    ok = all(v["binary"] and v.get("tool_output", {"status": "supported"})["status"] == "supported"
             for v in found.values())
    return ok, found


def start(args, tmux=None):
    tmux = tmux or Tmux()
    directory, settings, record = prepare(args)
    try:
        ok, found = preflight(settings)
        write_json(directory / "preflight.json", found)
        if not ok:
            raise ValueError("Preflight failed; inspect " + str(directory / "preflight.json"))
        pane = tmux.create(record)
        record.update(state="starting", dashboard=pane)
        write_json(directory / "session.json", record)
        command = shlex.join([sys.executable, str(ENTRY), "session", "supervise", str(directory)])
        tmux.call("respawn-pane", "-k", "-t", pane, command)
    except Exception as error:
        record.update(state="error", error=str(error), finished=time.time())
        write_json(directory / "session.json", record)
        try:
            tmux.close(record)
        except Exception as cleanup_error:
            record["cleanup_error"] = str(cleanup_error)
            write_json(directory / "session.json", record)
        try:
            _, archive = collect(directory)
            print("Diagnostics: " + str(archive), flush=True)
        except Exception as collection_error:
            print("Diagnostic collection failed: " + str(collection_error), flush=True)
        raise
    print("Run: " + str(directory), flush=True)
    print("Status: bash scripts/proof.sh status " + shlex.quote(str(directory)), flush=True)
    print("Claude channel and Grok tool permissions may need input. Ctrl+B, then W selects a window.", flush=True)
    return 0 if args.detach else tmux.attach(record)


def controller(directory):
    from .suite import run_suite
    job = Path(directory).resolve()
    settings = read_json(job / "settings.json")
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    return run_suite(job / "run", settings["peers"], settings["timeout"], settings["startup_timeout"],
                     work=settings["checks"]["work"], settings=settings, mode=settings["mode"])


def supervise(directory, tmux=None):
    tmux = tmux or Tmux()
    job = Path(directory).resolve()
    record = read_json(job / "session.json")
    settings = read_json(job / "settings.json")
    run = job / "run"
    child = None
    log = None
    interrupted = False
    stop_sent = None
    previous = None
    launched = False
    last_permission = None
    try:
        if not tmux.owned(record):
            raise ValueError("Supervisor is not in its registered tmux session")
        log = (job / "controller.log").open("x")
        os.chmod(job / "controller.log", 0o600)
        child = subprocess.Popen([sys.executable, str(ENTRY), "session", "controller", str(job)],
                                 stdout=log, stderr=subprocess.STDOUT, cwd=ROOT, start_new_session=True)
        record.update(state="running", controller_pid=child.pid, supervisor_pid=os.getpid())
        write_json(job / "session.json", record)
        launch_deadline = time.monotonic() + 30
        while child.poll() is None:
            if ((job / "stop.request").exists() and stop_sent is None and
                    not read_json(run / "report.json", {}).get("finished")):
                child.send_signal(signal.SIGINT)
                stop_sent = time.monotonic()
                record["state"] = "stopping"
                write_json(job / "session.json", record)
            if stop_sent is not None and time.monotonic() - stop_sent > 15:
                raise TimeoutError("Controller did not finish after stop request")
            if not launched and stop_sent is None:
                if (run / "run.json").exists():
                    for peer in settings["peers"]:
                        argv = [sys.executable, str(ENTRY), "agent", peer, "--dir", str(run)]
                        record["panes"][peer] = tmux.launch(record, peer, argv)
                        write_json(job / "session.json", record)
                    launched = True
                    if "claude" in settings["peers"]:
                        tmux.select(record, "claude")
                elif time.monotonic() > launch_deadline:
                    raise TimeoutError("Controller did not initialize within 30 seconds; see controller.log")
            if launched:
                snapshot = summarize(run)
                text = render(snapshot)
                if text != previous:
                    print(text, flush=True)
                    previous = text
                pending = tuple(snapshot["pending_permissions"])
                if pending and pending != last_permission:
                    tmux.select(record, pending[0])
                last_permission = pending
            time.sleep(0.5)
        record["controller_exit_code"] = child.returncode
    except KeyboardInterrupt:
        interrupted = True
        record["error"] = "Managed supervisor interrupted"
    except Exception as error:
        record["error"] = type(error).__name__ + ": " + str(error)
    finally:
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGINT)
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            record["controller_exit_code"] = child.returncode
        if log:
            log.close()
        record["cleanup"] = []
        for peer, pane in record.get("panes", {}).items():
            try:
                record["cleanup"].append(tmux.finish_peer(record, peer, pane, run))
            except Exception as error:
                record["cleanup"].append({"peer": peer, "status": "error", "detail": str(error)})
        report = read_json(run / "report.json", {})
        if any(item["status"] == "error" for item in record["cleanup"]):
            record["error"] = "One or more harness windows could not be closed; inspect cleanup records"
        record.update(state="error" if "error" in record or "finished" not in report else "finished",
                      finished=time.time())
        write_json(job / "session.json", record)
        try:
            summary, archive = collect(job)
            record["diagnostics"] = str(archive)
            print("\n" + render(summary), flush=True)
            print("Diagnostics: " + str(archive), flush=True)
        except Exception as error:
            record["collection_error"] = str(error)
            print("Diagnostic collection failed: " + str(error), flush=True)
        write_json(job / "session.json", record)
        try:
            tmux.select(record, "dashboard")
        except Exception as error:
            print("Could not select dashboard: " + str(error), flush=True)
        print("Run ended. Cleanup status: " + (record.get("error") or "owned harness windows closed") + ".\n"
              "Detach: Ctrl+B, then D. Close this dashboard: bash scripts/proof.sh close " +
              shlex.quote(str(job)), flush=True)
    return 130 if interrupted else (0 if report.get("status") == "pass" and
                                   "error" not in record and "collection_error" not in record else 1)


def operate(action, directory, watch=False, tmux=None):
    tmux = tmux or Tmux()
    job, run = paths(directory)
    if not job.is_dir():
        raise ValueError("Run directory does not exist")
    record = read_json(job / "session.json", {})
    if action == "collect":
        summary, output = collect(job)
        print(render(summary) + "Diagnostics: " + str(output))
        return 0
    if action == "status":
        previous = None
        try:
            while True:
                record = read_json(job / "session.json", {})
                text = ("Managed session: " + record.get("state", "manual") + "\n" + render(summarize(run)))
                if record.get("error"):
                    text += "Supervisor: " + record["error"] + "\n"
                if record.get("diagnostics"):
                    text += "Diagnostics: " + record["diagnostics"] + "\n"
                if text != previous:
                    print(text, flush=True)
                    previous = text
                if not watch or read_json(run / "report.json", {}).get("finished") or record.get("finished"):
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            pass  # Leaving the viewer must not cancel the run.
        return 0
    if not record:
        raise ValueError("Session controls require a managed run; use status or collect for older manual runs")
    if action == "attach":
        return tmux.attach(record)
    if action == "stop":
        if record.get("finished"):
            print("Run already finished; use close to remove its dashboard.")
            return 0
        if not tmux.owned(record):
            raise ValueError("Managed session is absent; use collect to preserve available evidence")
        (job / "stop.request").touch(mode=0o600, exist_ok=True)
        print("Stop requested. The supervisor will finalize evidence, close its harnesses, and collect diagnostics.")
        return 0
    if action == "close":
        if not record.get("finished"):
            raise ValueError("Run is still active; use stop first so diagnostics can be finalized")
        tmux.close(record)
        print("Managed dashboard closed. Run files preserved.")
        return 0
    raise ValueError("Unknown session action")


def list_runs(root):
    if not Path(root).exists():
        print("No saved runs in " + str(root))
        return
    for job in sorted(Path(root).resolve().iterdir()):
        if not job.is_dir():
            continue
        _, run = paths(job)
        record = read_json(job / "session.json", {})
        report = read_json(run / "report.json", {})
        if record or report:
            status = report.get("status") if report.get("finished") else record.get("state", "incomplete")
            print(f"{job.name}: {status}  {job}")
