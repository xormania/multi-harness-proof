#!/usr/bin/env bash
set -euo pipefail
proof_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$proof_root"
exec "${PROOF_PYTHON:-python3}" -m unittest discover -s tests -v
