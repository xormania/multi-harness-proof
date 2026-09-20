#!/usr/bin/env bash
# One operator entry point. No installation or global configuration writes.
set -euo pipefail
proof_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$proof_root"
exec "${PROOF_PYTHON:-python3}" "$proof_root/proof.py" session "$@"
