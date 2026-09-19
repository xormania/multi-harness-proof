#!/usr/bin/env bash
# Optional convenience: four windows in a NEW tmux session. Installs nothing.
set -euo pipefail
proof_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
proof_python=${PROOF_PYTHON:-python3}
command -v tmux >/dev/null || { printf 'tmux is not installed; use scripts/start.sh and three terminals.\n' >&2; exit 1; }
cd -- "$proof_root"
"$proof_python" proof.py doctor
proof_run=${1:-"runs/$(date -u +%Y%m%dT%H%M%SZ)-$$"}
if (($#)); then shift; fi
proof_run=$("$proof_python" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve())' "$proof_run")
if [[ -e "$proof_run" ]]; then
    printf 'Run directory already exists: %s\n' "$proof_run" >&2
    exit 1
fi
proof_session="mhproof-$(date -u +%H%M%S)-$$"
printf -v proof_command '%q ' "$proof_python" "$proof_root/proof.py" run --dir "$proof_run" "$@"
tmux new-session -d -s "$proof_session" -n relay -c "$proof_root" "bash -c $(printf '%q' "$proof_command")"
# This option applies only to the newly created session, never global tmux config.
tmux set-option -w -t "$proof_session:relay" remain-on-exit on
for ((proof_wait=0; proof_wait<100; proof_wait++)); do
    [[ -f "$proof_run/run.json" ]] && break
    sleep 0.1
done
if [[ ! -f "$proof_run/run.json" ]]; then
    printf 'Relay did not become ready. Inspect: tmux attach -t %s\n' "$proof_session" >&2
    exit 1
fi
for proof_peer in codex claude grok; do
    printf -v proof_command '%q ' "$proof_python" "$proof_root/proof.py" agent "$proof_peer" --dir "$proof_run"
    tmux new-window -t "$proof_session" -n "$proof_peer" -c "$proof_root" "bash -c $(printf '%q' "$proof_command")"
    tmux set-option -w -t "$proof_session:$proof_peer" remain-on-exit on
done
tmux select-window -t "$proof_session:claude"
printf 'Run: %s\nSession: %s\n' "$proof_run" "$proof_session"
if [[ -n "${TMUX:-}" ]]; then
    tmux switch-client -t "$proof_session"
else
    tmux attach-session -t "$proof_session"
fi
