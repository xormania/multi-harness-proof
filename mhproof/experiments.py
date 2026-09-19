"""Validated experiment inputs, immutable run snapshots, and read-only comparison."""
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import re
import secrets

from .contract import PEERS
from .relay import write_json
from .workload import FIXTURE, read_fixture

ROOT = Path(__file__).resolve().parents[1]


def mock_module():
    spec = importlib.util.spec_from_file_location("proof_mock_behavior", ROOT / "tests" / "behavior.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def object_keys(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(label + ": expected an object with only " + ", ".join(allowed))


def positive(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError(label + " must be a positive integer")
    return value


def name(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError(label + " must be 1–64 letters, digits, underscores or hyphens")
    return value


def agent_settings(peer, values=None):
    values = values or {}
    object_keys(values, ("binary", "profile", "model", "reasoning"), "agent " + peer)
    profile = values.get("profile", "economy")
    if profile not in {"economy", "existing"}:
        raise ValueError("Unknown agent profile")
    defaults = {"binary": peer, "profile": profile, "model": None, "reasoning": None}
    if profile == "economy":
        defaults.update(model={"codex": "gpt-5.6-luna", "claude": "haiku", "grok": None}[peer],
                        reasoning=None if peer == "claude" else "low")
    defaults.update(values)
    for key in ("binary", "model", "reasoning"):
        value = defaults[key]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError("Agent " + key + " must be a nonempty string or null")
    if defaults["binary"] is None:
        raise ValueError("Agent binary cannot be null")
    return defaults


def load_config(path):
    path = Path(path).resolve()
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate configuration key: " + key)
            result[key] = value
        return result
    raw = json.loads(path.read_text(), object_pairs_hook=unique)
    object_keys(raw, ("schema_version", "name", "mode", "output_root", "peers", "timeout", "startup_timeout",
                      "agents", "fixture", "checks", "scenarios"), "experiment")
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise ValueError("schema_version must be 1")
    mode = raw.get("mode", "mock")
    if mode not in {"mock", "live"}:
        raise ValueError("mode must be mock or live")
    peers = raw.get("peers", list(PEERS))
    if not isinstance(peers, list) or not all(isinstance(p, str) and p in PEERS for p in peers) or len(set(peers)) != len(peers) or len(peers) < 2:
        raise ValueError("Select at least two distinct known peers")
    if mode == "mock" and peers != list(PEERS):
        raise ValueError("The current mock fixtures require peers codex, claude, grok in that order")
    checks = {"round_trip_pairs": "all", "busy_pairs": "ring", "work": True, "burst_per_peer": 2}
    object_keys(raw.get("checks", {}), tuple(checks), "checks")
    checks.update(raw.get("checks", {}))
    for key, keyword in (("round_trip_pairs", "all"), ("busy_pairs", "ring")):
        value = checks[key]
        if value == keyword:
            checks[key] = [list(p) for p in itertools.permutations(peers, 2)] if keyword == "all" else [
                [peers[(i + 1) % len(peers)], target] for i, target in enumerate(peers)]
        elif not isinstance(value, list) or any(not isinstance(p, list) or len(p) != 2 or
                any(x not in peers for x in p) or p[0] == p[1] for p in value):
            raise ValueError(key + " must be " + keyword + " or a list of distinct-peer pairs")
        if len({tuple(p) for p in checks[key]}) != len(checks[key]):
            raise ValueError("Duplicate pairs are ambiguous; repeat the experiment in a new run")
    if type(checks["work"]) is not bool:
        raise ValueError("checks.work must be boolean")
    positive(checks["burst_per_peer"], "burst_per_peer")
    if checks["burst_per_peer"] > 64:
        raise ValueError("burst_per_peer is limited to 64 for this bounded proof")
    if not checks["round_trip_pairs"] and not checks["busy_pairs"] and not checks["work"]:
        raise ValueError("Select at least one coordination check")
    def resolve(value):
        if not isinstance(value, str) or not value:
            raise ValueError("Paths must be nonempty strings")
        return str((path.parent / value).resolve())
    fixture = resolve(raw["fixture"]) if "fixture" in raw else str(FIXTURE)
    read_fixture(fixture)
    agents = raw.get("agents", {})
    object_keys(agents, peers, "agents")
    resolved_agents = {p: agent_settings(p, agents.get(p)) for p in peers}
    for settings in resolved_agents.values():
        if "/" in settings["binary"]:
            settings["binary"] = resolve(settings["binary"])
    scenarios = raw.get("scenarios", [{"name": "baseline", "preset": "happy"}])
    if mode == "live" and "scenarios" in raw:
        raise ValueError("Fault scenarios apply only to mocks; use checks to configure live coordination")
    resolved_scenarios = []
    if mode == "mock":
        presets = mock_module().SCENARIOS
        if not isinstance(scenarios, list) or not scenarios:
            raise ValueError("scenarios must be a nonempty list")
        for scenario in scenarios:
            object_keys(scenario, ("name", "preset", "expect", "faults"), "scenario")
            item = copy.deepcopy(scenario)
            item["name"] = name(item.get("name"), "scenario name")
            item.setdefault("preset", "happy")
            if item["preset"] not in presets:
                raise ValueError("Unknown mock preset: " + str(item["preset"]))
            item.setdefault("faults", [])
            if not isinstance(item["faults"], list) or len(item["faults"]) > 16:
                raise ValueError("Use at most 16 explicit fault rules per scenario")
            item.setdefault("expect", "observe" if item["faults"] else presets[item["preset"]][0])
            if item["expect"] not in {"pass", "unverified", "observe"}:
                raise ValueError("Scenario expect must be pass, unverified, or observe")
            if item["preset"] == "incorrect-work" and not checks["work"]:
                raise ValueError("incorrect-work needs checks.work")
            for rule in item["faults"]:
                object_keys(rule, ("peer", "on", "case_prefix", "action", "seconds", "occurrence", "times"), "fault rule")
                if rule.get("peer") not in peers or rule.get("on") not in {"instruction", "challenge", "reply"}:
                    raise ValueError("Fault rule needs a selected peer and on=instruction/challenge/reply")
                if rule.get("action") not in {"delay", "drop", "wrong-memory", "stale-nonce", "crash"}:
                    raise ValueError("Unsupported fault action")
                if rule["action"] == "wrong-memory" and rule["on"] != "challenge":
                    raise ValueError("wrong-memory applies to challenge replies")
                if rule["action"] == "stale-nonce" and rule["on"] == "instruction":
                    raise ValueError("Instructions do not carry a challenge nonce")
                if not isinstance(rule.setdefault("case_prefix", ""), str):
                    raise ValueError("case_prefix must be a string")
                positive(rule.setdefault("occurrence", 1), "occurrence")
                positive(rule.setdefault("times", 1), "times")
                if rule["action"] == "delay":
                    if type(rule.get("seconds")) not in {int, float} or not 0 < rule["seconds"] <= 300:
                        raise ValueError("Delay seconds must be greater than 0 and at most 300")
                elif "seconds" in rule:
                    raise ValueError("seconds applies only to delay")
            resolved_scenarios.append(item)
        if len({s["name"] for s in resolved_scenarios}) != len(resolved_scenarios):
            raise ValueError("Scenario names must be unique")
    settings = {"schema_version": 1, "name": name(raw.get("name", "proof"), "experiment name"),
        "mode": mode, "output_root": resolve(raw.get("output_root", "runs/experiments")), "peers": peers,
        "timeout": positive(raw.get("timeout", 3 if mode == "mock" else 180), "timeout"),
        "startup_timeout": positive(raw.get("startup_timeout", 10 if mode == "mock" else 600), "startup_timeout"),
        "agents": resolved_agents, "fixture": fixture, "checks": checks, "scenarios": resolved_scenarios}
    return raw, settings


def run_experiment(path):
    from .suite import run_suite
    from .telemetry import manifest
    raw, settings = load_config(path)  # Validate everything before creating a run or launching a process.
    run_id = settings["name"] + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + secrets.token_hex(3)
    directory = Path(settings["output_root"]) / run_id
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    fixture = json.loads(Path(settings["fixture"]).read_text())
    write_json(directory / "fixture.json", fixture)
    digest = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
    snapshot = {"source_config": raw, "resolved": settings, "config_sha256": digest,
                "fixture_sha256": hashlib.sha256((directory / "fixture.json").read_bytes()).hexdigest(),
                "manifest": manifest(ROOT, settings["peers"], settings["timeout"])}
    write_json(directory / "experiment.json", snapshot)
    launch_settings = {**settings, "fixture": str(directory / "fixture.json"), "experiment": snapshot}
    write_json(directory / "launch-settings.json", launch_settings)
    result = {"mode": settings["mode"], "status": "running", "run_id": run_id, "config_sha256": digest,
              "reports": [], "scenarios": []}
    write_json(directory / "experiment-result.json", result)
    print("Experiment: " + str(directory), flush=True)
    try:
        if settings["mode"] == "mock":
            behavior = mock_module()
            for scenario in settings["scenarios"]:
                outcome = behavior.run_scenario(directory / scenario["name"], scenario["preset"],
                    settings["timeout"], settings=launch_settings, scenario_config=scenario)
                result["scenarios"].append(outcome)
                report_path = directory / scenario["name"] / "run" / "report.json"
                if report_path.exists():
                    result["reports"].append(str(report_path.relative_to(directory)))
                write_json(directory / "experiment-result.json", result)
                print(f"{scenario['name']}: proof={outcome.get('proof_status', 'missing')}, expectation={outcome['status']}", flush=True)
                if outcome.get("interrupted"):
                    result["status"] = "interrupted"
                    break
            if result["status"] != "interrupted":
                result["status"] = "mismatch" if any(s["status"] == "mismatch" for s in result["scenarios"]) else "complete"
        else:
            run_suite(directory / "run", settings["peers"], settings["timeout"], settings["startup_timeout"],
                      work=settings["checks"]["work"], settings=launch_settings)
            result["reports"] = ["run/report.json"]
            result["status"] = json.loads((directory / "run/report.json").read_text())["status"]
    except KeyboardInterrupt:
        result["status"] = "interrupted"
    except Exception as error:
        result.update(status="error", detail=type(error).__name__ + ": " + str(error))
    finally:
        write_json(directory / "experiment-result.json", result)
    print("Saved: " + str(directory / "experiment-result.json"), flush=True)
    return 0 if result["status"] in {"pass", "complete"} else 1


def history(directory):
    rows = []
    for path in sorted(Path(directory).resolve().glob("*/experiment-result.json")):
        item = json.loads(path.read_text())
        reports = []
        for relative in item["reports"]:
            report = json.loads((path.parent / relative).read_text())
            reports.append({"path": str(path.parent / relative), "status": report["status"],
                "phase": report.get("phase"), "detail": report.get("detail"), "tool_audit": report.get("tool_audit"),
                "first_incomplete": next((c for c in report["cases"] if c["status"] != "pass"), None)})
        rows.append({"directory": str(path.parent), "mode": item["mode"], "status": item["status"],
                     "config_sha256": item["config_sha256"], "reports": reports})
    return rows


def compare(left, right):
    def flatten(value, prefix=""):
        if isinstance(value, dict):
            return {p: v for k, item in value.items() for p, v in flatten(item, prefix + "." + k if prefix else k).items()}
        return {prefix: value}
    directories = [Path(left).resolve(), Path(right).resolve()]
    snapshots = [json.loads((p / "experiment.json").read_text()) for p in directories]
    values = [flatten({"settings": s["resolved"], "fixture_sha256": s["fixture_sha256"],
                      "source_sha256": s["manifest"]["source_sha256"]}) for s in snapshots]
    changes = [{"setting": k, "before": values[0].get(k), "after": values[1].get(k)}
               for k in sorted(values[0].keys() | values[1].keys()) if values[0].get(k) != values[1].get(k)]
    outcomes = []
    for directory in directories:
        item = json.loads((directory / "experiment-result.json").read_text())
        reports = []
        for relative in item["reports"]:
            report = json.loads((directory / relative).read_text())
            reports.append({"scenario": relative.split("/")[0], "mode": report["mode"], "status": report["status"],
                "phase": report.get("phase"), "detail": report.get("detail"), "sessions": report.get("sessions"),
                "cases": report["cases"], "work": report["work"], "tool_audit": report.get("tool_audit")})
        outcomes.append({"directory": str(directory), "status": item["status"], "reports": reports})
    case_maps = [{(report["scenario"], case["case_id"]): case["status"]
                  for report in outcome["reports"] for case in report["cases"]} for outcome in outcomes]
    case_changes = [{"scenario": scenario, "case_id": case, "before": case_maps[0].get((scenario, case), "not_recorded"),
                     "after": case_maps[1].get((scenario, case), "not_recorded")}
                    for scenario, case in sorted(case_maps[0].keys() | case_maps[1].keys())
                    if case_maps[0].get((scenario, case)) != case_maps[1].get((scenario, case))]
    return {"changes": changes, "case_changes": case_changes, "outcomes": outcomes,
            "interpretation": "Observed differences only; mock results do not establish live compatibility or causation."}
