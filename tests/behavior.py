"""Run real proof processes against deterministic, explicitly fake harnesses."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mhproof.relay import write_json
from mhproof.suite import run_suite


# Expectations describe verifier behavior, not model or vendor compatibility.
SCENARIOS = {
    "happy": ("pass", True, "Full coordination and work with later-turn delivery"),
    "delayed-duplicate": ("pass", True, "Late native evidence and duplicate observations/completions"),
    "prose-title": ("pass", False, "Human-facing Grok titles with canonical tool identity"),
    "claude-channel-delayed": ("pass", True, "MCP connects before Claude registers its channel handler"),
    "claude-channel-unobserved": ("unverified", False, "Channel registration log format is unrecognized"),
    "claude-channel-drop": ("unverified", False, "A registered channel drops the startup notification"),
    "grok-discovery": ("pass", True, "Grok discovers the MCP catalog then dispatches every proof tool via use_tool"),
    "grok-discovery-only": ("unverified", False, "Grok catalog discovery cannot cover an unobserved ready tool call"),
    "grok-wrong-dispatch": ("unverified", False, "Grok use_tool targets an unrelated MCP server"),
    "queued-only": ("unverified", False, "Accepted messages never handled"),
    "wrong-memory": ("unverified", False, "Reply carries the wrong private memory"),
    "stale-nonce": ("unverified", False, "Reply reuses an old challenge nonce"),
    "missing-native": ("unverified", False, "MCP reply without native tool evidence"),
    "missing-hook": ("unverified", False, "Claude connects and reports but emits no tool hook"),
    "session-drift": ("unverified", False, "Grok tool observation changes native session"),
    "unsupported-interject": ("unverified", False, "Grok extension returns method-not-found"),
    "permission-denied": ("unverified", False, "Human denies one proof-tool permission"),
    "crash": ("unverified", False, "Native process exits during a challenge"),
    "malformed-stdout": ("unverified", False, "Native stdout violates JSON-lines framing"),
    "missing-completion": ("unverified", False, "Prompt RPC returns without native completion"),
    "incorrect-work": ("unverified", True, "Messaging succeeds but one work answer is wrong"),
    "reused-call-id": ("unverified", False, "Different native calls reuse one call ID"),
    "extra-tool": ("unverified", False, "A non-proof tool appears in native evidence"),
    "interrupted": ("unverified", False, "Operator interrupts a stalled case; evidence survives"),
}


def assess(scenario, report, events, settings=None):
    """A timeout by itself is insufficient: check the injected failure's evidence."""
    expected, work, _ = SCENARIOS[scenario]
    if settings:
        work = settings["checks"]["work"]
    errors = []
    def require(condition, message):
        if not condition:
            errors.append(message)
    cases, audit = report.get("cases", []), report.get("tool_audit", {})
    kinds = {e["event"] for e in events}
    require(report.get("mode") == "mock", "Report must be explicitly marked mock")
    require(report.get("status") == expected, "Unexpected proof verdict")
    require(len([e for e in events if e["event"] == "registered"]) == 3, "Expected three persistent sessions")
    require(bool(report.get("sessions")), "Missing session records")
    if expected == "pass":
        planned = (len(settings["checks"]["round_trip_pairs"]) + len(settings["checks"]["busy_pairs"]) +
                   (3 * settings["checks"]["burst_per_peer"] if work else 0)) if settings else (15 if work else 9)
        require(len(cases) == planned and all(c["status"] == "pass" for c in cases), "Incomplete message coverage")
        require(audit.get("status") == "pass", "Tool audit did not pass")
        require(len(report.get("work", [])) == (3 if work else 0) and
                all(w["status"] == "pass" for w in report.get("work", [])), "Incomplete work scores")
    elif scenario not in {"missing-hook", "missing-completion", "grok-discovery-only",
                           "claude-channel-unobserved", "claude-channel-drop"} and not settings:
        require(any(c["status"] == "pass" for c in cases), "Earlier successful evidence was not preserved")
    if expected != "pass" and scenario != "incorrect-work":
        require(not report.get("work"), "Failure should stop before the work stage")
        require(not any(c["status"] == "pass" for c in cases[next((i for i, c in enumerate(cases)
                    if c["status"] != "pass"), len(cases)):]), "No case may pass after the first incomplete case")
    if scenario in {"queued-only", "wrong-memory", "stale-nonce", "missing-native", "reused-call-id"}:
        require(bool(cases) and cases[-1]["status"] == "unverified", "Affected message case must remain unverified")
        require("verification_timeout" in kinds, "Missing timeout decision telemetry")
    if scenario in {"missing-native", "missing-hook", "grok-discovery-only"}:
        require(audit.get("unmatched_relay_calls", 0) > 0, "Native evidence gap was not audited")
    if scenario in {"missing-hook", "missing-completion", "grok-discovery-only"}:
        require(not cases, "Unready harnesses must not start message cases")
    if scenario.startswith("claude-channel-"):
        ready = [e for e in events if e["event"] == "channel_ready" and e["peer"] == "claude"]
        submitted = [e for e in events if e["event"] == "submitted" and e["peer"] == "claude"]
        startup = report.get("startup", {}).get("claude", {})
        if scenario == "claude-channel-unobserved":
            require(not ready and not submitted, "Unobserved handler must prevent channel submission")
            require("Claude channel handler registration" in report.get("phase", ""), "Wrong startup failure phase")
            require("channel_handler_registered" in startup.get("missing", []), "Missing channel gate diagnostic")
        else:
            require(len(ready) == 1 and submitted and ready[0]["seq"] < min(e["seq"] for e in submitted),
                    "Channel handler must be observed before first submission")
        if scenario == "claude-channel-delayed":
            require(not startup.get("missing"), "Delayed channel must reach verified readiness")
        else:
            require(not cases and not startup.get("ready_report"), "Missing readiness cannot start message cases")
            require("verification_timeout" in kinds, "Missing readiness timeout evidence")
        if scenario == "claude-channel-drop":
            require(startup.get("instruction_submitted") and "ready_report" in startup.get("missing", []),
                    "Registration and submission must not substitute for model receipt")
            require(all(report.get("startup", {}).get(p, {}).get("ready_report") for p in ("codex", "grok")),
                    "Report must identify Claude's missing readiness while preserving other peers' reports")
    if scenario == "session-drift":
        require("native_identity_error" in kinds, "Missing native identity failure")
    if scenario == "unsupported-interject":
        require("unsupported" in kinds and any(c["status"] == "unsupported" for c in cases), "Missing unsupported verdict")
    if scenario == "permission-denied":
        require("permission_denied" in kinds, "Missing permission denial")
        require({"permission_requested", "permission_resolved"} <= kinds, "Missing permission timing telemetry")
    if scenario in {"crash", "malformed-stdout"}:
        require(bool({"adapter_error", "delivery_error"} & kinds), "Missing native process/protocol failure")
        diagnostic = "Harness closed stdout" if scenario == "crash" else "Non-JSON data on native protocol stdout"
        require(diagnostic in report.get("detail", ""), "Missing precise process/protocol diagnostic")
    if scenario == "incorrect-work":
        require(len(cases) == 15 and all(c["status"] == "pass" for c in cases), "Transport should still pass")
        require(any(w["status"] == "incorrect" for w in report.get("work", [])), "Wrong answer was not scored incorrect")
    if scenario == "reused-call-id":
        require(bool(audit.get("conflicting_call_seqs")), "Conflicting native call IDs were not audited")
    if scenario in {"extra-tool", "grok-wrong-dispatch"}:
        require("tool_violation" in kinds and audit.get("status") == "unverified", "Extra tool must invalidate audit")
    if scenario == "interrupted":
        require(report.get("detail") == "Interrupted by operator", "Operator interruption must be explicit")
        require("verification_failed" in kinds, "Missing interruption decision telemetry")
    if scenario == "prose-title":
        require(any(e["event"] == "native_tool" and e["peer"] == "grok" and
                    e.get("identity_basis") == "x.ai/tool v1" for e in events), "Canonical identity not exercised")
    if scenario in {"grok-discovery", "grok-discovery-only", "grok-wrong-dispatch"}:
        require(audit.get("native_discovery_calls") == 1, "Catalog discovery must be counted separately")
        require(any(e["event"] == "native_discovery" and e.get("tool") == "search_tool" and
                    e.get("arguments", {}).get("query") == "coord_proof" for e in events),
                "Missing discovery arguments in telemetry")
        native = [e for e in events if e["event"] == "native_tool" and e["peer"] == "grok"]
        if scenario == "grok-discovery-only":
            require(not native, "Discovery must not create native proof evidence")
        else:
            require(bool(native) and all(e.get("wire_tool") == "use_tool" and
                    e.get("wire_arguments") == {"tool_name": "coord_proof__" + e["tool"],
                                                "tool_input": e["arguments"]} for e in native),
                    "Wrapped dispatch must preserve exact outer and inner arguments")
    if scenario == "delayed-duplicate":
        require(any(c.get("timing", {}).get("challenge_ack_after_reply") or
                    c.get("timing", {}).get("reply_ack_after_receipt") for c in cases), "Late RPC acknowledgement not exercised")
    return errors


def run_scenario(directory, scenario, timeout=3, settings=None, scenario_config=None):
    """Always use repository fake executables; never discover vendor CLIs."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    run = directory / "run"
    processes, handles, launches, cleanup = {}, [], [], []
    started = time.time()
    failures = []
    scenario_config = scenario_config or {"name": scenario, "preset": scenario, "expect": SCENARIOS[scenario][0], "faults": []}
    result = {"scenario": scenario_config["name"], "preset": scenario, "mode": "mock",
              "expected_proof_status": scenario_config["expect"]}
    def launch(label, argv, log, **kwargs):
        output = log.open("w", encoding="utf-8")
        handles.append(output)
        child = subprocess.Popen(argv, stdout=output, stderr=subprocess.STDOUT,
                                 start_new_session=True, **kwargs)
        processes[label] = child
        launches.append({"component": label, "argv": argv, "pid": child.pid, "started": time.time()})
        return child
    try:
        controller_argv = [sys.executable, str(Path(__file__).resolve()), "--controller",
            "--dir", str(run), "--scenario", scenario, "--timeout", str(timeout)]
        if settings:
            write_json(directory / "launch-settings.json", settings)
            controller_argv += ["--settings-file", str(directory / "launch-settings.json")]
        controller = launch("controller", controller_argv, directory / "controller.log")
        deadline = time.monotonic() + 10
        while not (run / "run.json").exists():
            if controller.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("Mock controller failed before writing run.json; inspect controller.log")
            time.sleep(0.03)
        import hashlib
        write_json(run / "scenario.json", {"mode": "mock", "scenario": scenario,
            "expected_proof_status": scenario_config["expect"], "description": SCENARIOS[scenario][2],
            "definition": scenario_config,
            "faults_apply_only_to_fake_processes": True,
            "fixture_sha256": {name: hashlib.sha256((ROOT / "tests" / name).read_bytes()).hexdigest()
                               for name in ("behavior.py", "fake_harness.py")}})
        for peer in ("codex", "claude", "grok"):
            shim = directory / ("fake-" + peer)
            shim.write_text(f"#!{sys.executable}\nimport runpy,sys\nsys.argv.insert(1, {peer!r})\n"
                            f"runpy.run_path({str(ROOT / 'tests/fake_harness.py')!r}, run_name='__main__')\n")
            shim.chmod(0o700)
            child = launch(peer, [sys.executable, str(ROOT / "proof.py"), "agent", peer,
                "--dir", str(run), "--binary", str(shim)] + ([] if settings else ["--profile", "existing"]), run / (peer + "-launcher.log"),
                stdin=subprocess.PIPE, env={**os.environ, "MHPROOF_FAKE_SCENARIO": scenario,
                    "MHPROOF_FAKE_RULES": json.dumps(scenario_config["faults"]),
                    "MHPROOF_FAKE_FAULT_LOG": str(run / ("fake-" + peer + "-faults.jsonl"))})
            # Only the fake permission-denied scenario asks. No real permissions or CLI are involved.
            if peer == "grok" and scenario == "permission-denied":
                child.stdin.write(b"n\n")
            child.stdin.close()
        if scenario == "interrupted":
            deadline = time.monotonic() + 20
            fault_path = run / "fake-grok-faults.jsonl"
            while not fault_path.exists() or "queued-without-handling" not in fault_path.read_text():
                if controller.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Mock never reached the interruption barrier")
                time.sleep(0.02)
            os.killpg(controller.pid, signal.SIGINT)
        controller.wait(timeout=max(60, timeout * 6))
        report = json.loads((run / "report.json").read_text())
        events = [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]
        fault_events = [json.loads(line) for p in run.glob("fake-*-faults.jsonl") for line in p.read_text().splitlines()]
        triggered = {e["rule_index"] for e in fault_events if "rule_index" in e}
        result["untriggered_rules"] = sorted(set(range(len(scenario_config["faults"]))) - triggered)
        if scenario_config["faults"] or scenario_config["expect"] != SCENARIOS[scenario][0]:
            if report.get("mode") != "mock":
                failures.append("Report must be explicitly marked mock")
            if scenario_config["expect"] != "observe" and report["status"] != scenario_config["expect"]:
                failures.append("Unexpected proof verdict")
            if result["untriggered_rules"] and scenario_config["expect"] != "observe":
                failures.append("One or more expected fault rules never triggered")
        else:
            failures.extend(assess(scenario, report, events, settings))
            if scenario_config["expect"] not in {"observe", report["status"]}:
                failures.append("Unexpected configured proof verdict")
        result.update(proof_status=report["status"], phase=report.get("phase"),
                      tool_audit=report.get("tool_audit"), cases=len(report["cases"]),
                      first_incomplete=next((c for c in report["cases"] if c["status"] != "pass"), None))
        if controller.returncode != (0 if report["status"] == "pass" else 1):
            failures.append("Controller exit code contradicts report")
    except KeyboardInterrupt:
        failures.append("Behavior runner interrupted by operator")
        result["interrupted"] = True
    except Exception as error:
        failures.append(type(error).__name__ + ": " + str(error))
    finally:
        for label, child in processes.items():
            # Include MCP grandchildren and a mock Claude that stays open after the run.
            try:
                os.killpg(child.pid, signal.SIGTERM)
                cleanup.append({"component": label, "signal": "SIGTERM", "time": time.time()})
            except ProcessLookupError:
                pass
        for label, child in processes.items():
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                cleanup.append({"component": label, "signal": "SIGKILL", "time": time.time()})
                child.wait(timeout=5)
            launches[next(i for i, item in enumerate(launches) if item["component"] == label)]["exit_code"] = child.returncode
        for handle in handles:
            handle.close()
        if run.is_dir():
            shutil.copyfile(directory / "controller.log", run / "controller.log")
        result.update(status=("observed" if scenario_config["expect"] == "observe" else "matched") if not failures else "mismatch", failures=failures,
                      started=started, finished=time.time(), processes=launches, cleanup=cleanup)
        write_json((run if run.is_dir() else directory) / "behavior.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", help="Fresh output directory; retains all mock evidence")
    parser.add_argument("--scenario", choices=["all", *SCENARIOS], default="all")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--timeout", type=int, default=3, help="Per-step mock deadline; increase on slow machines")
    parser.add_argument("--controller", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--settings-file", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.list:
        for name, (verdict, _, description) in SCENARIOS.items():
            print(f"{name}: expected proof={verdict}; {description}")
        return 0
    if not args.dir or args.timeout <= 0:
        parser.error("--dir and a positive --timeout are required")
    if args.controller:
        if args.scenario == "all":
            parser.error("Controller requires one scenario")
        settings = json.loads(Path(args.settings_file).read_text()) if args.settings_file else None
        return run_suite(args.dir, ["codex", "claude", "grok"], timeout=args.timeout,
                         startup_timeout=settings["startup_timeout"] if settings else 10,
                         work=settings["checks"]["work"] if settings else SCENARIOS[args.scenario][1],
                         mode="mock", settings=settings)
    root = Path(args.dir).resolve()
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    summary = {"mode": "mock", "status": "running", "scenarios": []}
    try:
        for name in (SCENARIOS if args.scenario == "all" else [args.scenario]):
            result = run_scenario(root / name, name, args.timeout)
            summary["scenarios"].append(result)
            write_json(root / "behavior-summary.json", summary)
            print(f"{result['status'].upper()} {name}: proof={result.get('proof_status', 'missing')}; "
                  f"expected={result['expected_proof_status']}", flush=True)
            if result.get("interrupted"):
                summary["status"] = "interrupted"
                break
        if summary["status"] != "interrupted":
            summary["status"] = "matched" if all(r["status"] == "matched" for r in summary["scenarios"]) else "mismatch"
    finally:
        write_json(root / "behavior-summary.json", summary)
    print("Mock behavior evidence: " + str(root / "behavior-summary.json"))
    return 0 if summary["status"] == "matched" else 130 if summary["status"] == "interrupted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
