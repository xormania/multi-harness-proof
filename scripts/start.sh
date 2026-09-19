#!/usr/bin/env bash
set -euo pipefail
proof_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
proof_python=${PROOF_PYTHON:-python3}
cd -- "$proof_root"
proof_run=${1:-"runs/$(date -u +%Y%m%dT%H%M%SZ)-$$"}
if (($#)); then shift; fi
exec "$proof_python" "$proof_root/proof.py" run --dir "$proof_run" "$@"
