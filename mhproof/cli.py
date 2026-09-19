import argparse
import json
import shutil
import sys

from .adapters import run_agent, version
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
    agent.add_argument("--profile", choices=("economy", "existing"), default="economy",
                       help="economy: Codex Luna/low, Claude Haiku, Grok default/low; existing: use harness defaults")
    mcp = sub.add_parser("mcp", help="Internal stdio MCP server for the adapters")
    mcp.add_argument("--dir", required=True)
    mcp.add_argument("--peer", required=True, choices=PEERS)
    mcp.add_argument("--channel", action="store_true")
    diagnostics = sub.add_parser("bundle", help="Create a local diagnostic ZIP, excluding run credentials/configs")
    diagnostics.add_argument("--dir", required=True)
    diagnostics.add_argument("--out", required=True, help="New ZIP path (must not exist)")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            found = {}
            for peer in args.peers:
                binary = shutil.which(peer)
                found[peer] = {"binary": binary, "version": version(peer, binary) if binary else "not installed"}
            print(json.dumps(found, indent=2))
            return 0 if all(v["binary"] for v in found.values()) else 1
        if args.command == "run":
            if len(set(args.peers)) != len(args.peers) or len(args.peers) < 2:
                parser.error("Select at least two distinct peers")
            return run_suite(args.dir, args.peers, args.timeout, args.startup_timeout, work=not args.no_work)
        if args.command == "agent":
            model, reasoning = args.model, args.reasoning
            if args.profile == "economy":
                model = model or {"codex": "gpt-5.6-luna", "claude": "haiku", "grok": None}[args.peer]
                if args.peer != "claude":
                    reasoning = reasoning or "low"
            return run_agent(args.dir, args.peer, args.binary, model, reasoning)
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
