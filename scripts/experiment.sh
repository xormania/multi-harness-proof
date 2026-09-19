#!/usr/bin/env bash
set -euo pipefail
proof_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$proof_root"
exec "${PROOF_PYTHON:-python3}" proof.py experiment --config "${1:-proof.mock.example.json}"
