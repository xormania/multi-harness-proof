#!/usr/bin/env bash
# Explicit maintenance only. Never called by a proof run or its launch scripts.
set -uo pipefail
proof_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
proof_python=${PROOF_PYTHON:-python3}
proof_dry=false
proof_codex_method=auto
proof_log_dir="$proof_root/runs/updates-$(date -u +%Y%m%dT%H%M%SZ)-$$"
usage() {
    cat <<'HELP'
Usage: bash scripts/update-harnesses.sh [--dry-run]
       [--codex-method auto|standalone|npm|brew] [--log-dir NEW_DIRECTORY]

Update the installed Codex, Claude Code, and Grok Build harnesses.
Records before/after versions, commands, update output, and a summary.tsv.
Continues after an individual failure; exits nonzero if any harness failed,
is missing, or has an unrecognized installation. Does not install missing CLIs.
Run between proof sessions. --dry-run prints commands without executing updates.
Codex auto detects common standalone, npm-global, and Homebrew installations.
HELP
}
while (($#)); do
    case "$1" in
        --dry-run) proof_dry=true; shift ;;
        --codex-method|--log-dir)
            (($# >= 2)) || { usage >&2; exit 2; }
            if [[ "$1" == --codex-method ]]; then proof_codex_method=$2; else proof_log_dir=$2; fi
            shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
case "$proof_codex_method" in auto|standalone|npm|brew) ;; *) usage >&2; exit 2 ;; esac
command -v "$proof_python" >/dev/null || { printf 'Python 3 is required.\n' >&2; exit 2; }
umask 077
mkdir -p -- "$(dirname -- "$proof_log_dir")" || exit 2
mkdir -- "$proof_log_dir" || { printf 'Use a fresh log directory.\n' >&2; exit 2; }
proof_log_dir=$(cd -- "$proof_log_dir" && pwd)
printf 'harness\tstatus\texit_code\tbefore\tafter\n' > "$proof_log_dir/summary.tsv"
printf 'Update logs: %s\n' "$proof_log_dir"

codex_standalone_update() {
    # Download completely before executing; retain the exact installer for audit.
    curl --fail --silent --show-error --location --connect-timeout 20 --max-time 180 \
        --proto '=https' --proto-redir '=https' \
        --output "$proof_log_dir/codex-install.sh" https://chatgpt.com/codex/install.sh || return $?
    sh "$proof_log_dir/codex-install.sh"
}
proof_failed=0
for proof_peer in codex claude grok; do
    proof_before=unavailable
    proof_after=unavailable
    proof_status=failed
    proof_rc=0
    proof_path=$(type -P "$proof_peer" || true)
    proof_command=()
    if [[ -z "$proof_path" ]]; then
        proof_status=missing
        proof_rc=127
    else
        proof_path=$("$proof_python" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).absolute())' "$proof_path")
        proof_resolved=$("$proof_python" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve())' "$proof_path")
        proof_version_args=(--version)
        [[ "$proof_peer" == grok ]] && proof_version_args=(--no-auto-update --version)
        if proof_before=$("$proof_path" "${proof_version_args[@]}" 2>&1); then :; else proof_before="version probe failed: $proof_before"; fi
        printf '%s\n' "$proof_before" > "$proof_log_dir/$proof_peer-before.txt"
        printf 'executable: %s\nresolved: %s\n' "$proof_path" "$proof_resolved" > "$proof_log_dir/$proof_peer-command.txt"
        case "$proof_peer" in
            codex)
                proof_method=$proof_codex_method
                if [[ "$proof_method" == auto ]]; then
                    case "$proof_resolved" in
                        */Caskroom/codex/*|*/Cellar/codex/*) proof_method=brew ;;
                        *)
                            proof_npm_root=$(npm root -g 2>/dev/null || true)
                            if [[ -n "$proof_npm_root" && "$proof_resolved" == "$proof_npm_root/@openai/codex/"* ]]; then
                                proof_method=npm
                            elif [[ "$proof_resolved" == */node_modules/* ]]; then
                                proof_method=auto  # Do not replace a different package manager's install.
                            elif [[ "$proof_path" == "$HOME/.local/bin/codex" ]]; then
                                proof_method=standalone
                            fi ;;
                    esac
                fi
                case "$proof_method" in
                    standalone) proof_command=(codex_standalone_update) ;;
                    npm) proof_command=(npm install -g @openai/codex@latest) ;;
                    brew) proof_command=(brew upgrade codex) ;;
                    *) printf 'Codex install method unknown at %s; select --codex-method.\n' "$proof_path"; proof_status=unsupported; proof_rc=2 ;;
                esac ;;
            claude)
                case "$proof_resolved" in
                    */Caskroom/claude-code@latest/*) proof_command=(brew upgrade claude-code@latest) ;;
                    */Caskroom/claude-code/*) proof_command=(brew upgrade claude-code) ;;
                    *) proof_command=("$proof_path" update) ;;
                esac ;;
            grok) proof_command=("$proof_path" --no-auto-update update) ;;
        esac
        if ((${#proof_command[@]})); then
            printf '\n%s update command: ' "$proof_peer"
            printf '%q ' "${proof_command[@]}"
            printf '\n'
            printf '%q ' "${proof_command[@]}" >> "$proof_log_dir/$proof_peer-command.txt"
            printf '\n' >> "$proof_log_dir/$proof_peer-command.txt"
            if [[ "$proof_peer" == codex && "$proof_method" == standalone ]]; then
                printf 'Downloads https://chatgpt.com/codex/install.sh, then runs sh on the saved installer.\n' | tee -a "$proof_log_dir/$proof_peer-command.txt"
            fi
            if "$proof_dry"; then
                proof_status=dry-run
            else
                "${proof_command[@]}" 2>&1 | tee "$proof_log_dir/$proof_peer-update.log"
                proof_codes=("${PIPESTATUS[@]}")
                proof_rc=${proof_codes[0]}
                ((proof_rc == 0)) && proof_rc=${proof_codes[1]}
                ((proof_rc == 0)) && proof_status=updated-or-current
            fi
        fi
        if proof_after=$("$proof_path" "${proof_version_args[@]}" 2>&1); then :; else
            proof_after="version probe failed: $proof_after"
            proof_status=failed
            ((proof_rc == 0)) && proof_rc=1
        fi
        printf '%s\n' "$proof_after" > "$proof_log_dir/$proof_peer-after.txt"
    fi
    proof_before=${proof_before//$'\n'/ }
    proof_before=${proof_before//$'\t'/ }
    proof_after=${proof_after//$'\n'/ }
    proof_after=${proof_after//$'\t'/ }
    printf '%s\t%s\t%s\t%s\t%s\n' "$proof_peer" "$proof_status" "$proof_rc" "$proof_before" "$proof_after" >> "$proof_log_dir/summary.tsv"
    printf '%s: %s (exit %s)\n  before: %s\n  after:  %s\n' "$proof_peer" "$proof_status" "$proof_rc" "$proof_before" "$proof_after"
    ((proof_rc == 0)) || proof_failed=1
done
printf '\nSummary: %s/summary.tsv\n' "$proof_log_dir"
exit "$proof_failed"
