#!/usr/bin/env bash
set -euo pipefail
proof_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
proof_python=${PROOF_PYTHON:-python3}
if (($# < 2)); then
    printf 'Usage: %s codex|claude|grok RUN_DIRECTORY [--model NAME] [--binary PATH]\n' "$0" >&2
    exit 2
fi
proof_peer=$1
proof_run=$2
shift 2
cd -- "$proof_root"
exec "$proof_python" "$proof_root/proof.py" agent "$proof_peer" --dir "$proof_run" "$@"
