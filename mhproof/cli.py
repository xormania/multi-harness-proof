import argparse
import json
import shutil
import sys
from pathlib import Path

from .adapters import run_agent, version
from .compat import codex_capability
from .contract import PEERS
from .mcp import run_mcp
from .suite import run_suite
from .telemetry import bundle


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Must be positive")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description="Test messages between persistent Codex, Claude Code, and Grok Build sessions.")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="Inspect local executable versions; make no model calls")
    doctor.add_argument("--peers", nargs="+", choices=PEERS, default=list(PEERS))
    run = sub.add_parser("run", help="Start the loopback relay and run the live evidence checks")
    run.add_argument("--dir", required=True, help="New run directory (must not exist)")
    run.add_argument("--peers", nargs="+", choices=PEERS, default=list(PEERS))
    run.add_argument("--timeout", type=positive, default=180, help="Seconds per step")
    run.add_argument("--startup-timeout", type=positive, default=600)
    run.add_argument("--no-work", action="store_true", help="Run only the baseline round-trip and busy checks")
    agent = sub.add_parser("agent", help="Launch a real harness in a persistent proof session")
    agent.add_argument("peer", choices=PEERS)
    agent.add_argument("--dir", required=True)
    agent.add_argument("--binary", help="Installed CLI path or name")
    agent.add_argument("--model", help="Override the selected profile's model for this invocation")
    agent.add_argument("--reasoning", help="Native effort level (for models that support it)")
    agent.add_argument("--profile", choices=("economy", "existing"),
                       help="economy: Codex Luna/low, Claude Haiku, Grok default/low; existing: use harness defaults")
    mcp = sub.add_parser("mcp", help="Internal stdio MCP server for the adapters")
    mcp.add_argument("--dir", required=True)
    mcp.add_argument("--peer", required=True, choices=PEERS)
    mcp.add_argument("--channel", action="store_true")
    diagnostics = sub.add_parser("bundle", help="Create a local diagnostic ZIP, excluding run credentials/configs")
    diagnostics.add_argument("--dir", required=True)
    diagnostics.add_argument("--out", required=True, help="New ZIP path (must not exist)")
    hook = sub.add_parser("claude-hook", help="Internal run-local native identity/tool observer")
    hook.add_argument("--dir", required=True)
    experiment = sub.add_parser("experiment", help="Run a configured experiment in a fresh, preserved directory")
    experiment.add_argument("--config", required=True)
    history = sub.add_parser("history", help="Read experiment outcomes across preserved runs")
    history.add_argument("--dir", required=True)
    compare = sub.add_parser("compare", help="Compare two experiment snapshots and their evidence")
    compare.add_argument("left")
    compare.add_argument("right")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            found = {}
            for peer in args.peers:
                binary = shutil.which(peer)
                found[peer] = {"binary": binary, "version": version(peer, binary) if binary else "not installed"}
                if peer == "codex" and binary:
                    found[peer]["tool_output"] = codex_capability(binary)
            print(json.dumps(found, indent=2))
            return 0 if all(v["binary"] and v.get("tool_output", {"status": "supported"})["status"] == "supported"
                            for v in found.values()) else 1
        if args.command == "claude-hook":
            from .evidence import run_claude_hook
            return run_claude_hook(args.dir)
        if args.command == "run":
            if len(set(args.peers)) != len(args.peers) or len(args.peers) < 2:
                parser.error("Select at least two distinct peers")
            return run_suite(args.dir, args.peers, args.timeout, args.startup_timeout, work=not args.no_work)
        if args.command == "agent":
            from .experiments import agent_settings
            config = json.loads((Path(args.dir) / "run.json").read_text())
            settings = agent_settings(args.peer, config.get("agent_settings", {}).get(args.peer))
            if args.profile:
                settings = agent_settings(args.peer, {"profile": args.profile, "binary": settings["binary"]})
            return run_agent(args.dir, args.peer, args.binary or settings["binary"],
                             args.model or settings["model"], args.reasoning or settings["reasoning"])
        if args.command in {"experiment", "history", "compare"}:
            from .experiments import run_experiment, history, compare
            if args.command == "experiment":
                return run_experiment(args.config)
            print(json.dumps(history(args.dir) if args.command == "history" else compare(args.left, args.right), indent=2))
            return 0
        if args.command == "bundle":
            print(bundle(args.dir, args.out))
            return 0
        if args.channel and args.peer != "claude":
            parser.error("Only the Claude adapter uses an MCP channel")
        run_mcp(args.dir, args.peer, args.channel)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print("proof: " + str(e), file=sys.stderr)
        return 1
