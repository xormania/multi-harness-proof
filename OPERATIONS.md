# Managed runs

The managed launcher owns the controller, harness windows, progress display,
and final diagnostic collection. It uses an installed tmux and existing harness
authentication. No global harness or tmux configuration is changed.

## Start

```bash
git pull --ff-only
bash scripts/proof.sh start
```

This selects the full fixture suite and requests:

| Peer | Model | Reasoning |
| --- | --- | --- |
| Codex | `gpt-5.6-luna` | `low` |
| Claude | `sonnet` | `low` |
| Grok | `grok-4.5` | `low` |

The launcher generates a fresh name under `runs/`, snapshots the plan and
fixture, checks executables and Codex's required schema capability, then creates
a dedicated tmux session. It opens Claude's window for the development-channel
prompt and selects Grok when a new pending permission is observed. Approvals
remain yours; there is no automatic permission bypass.

In tmux, press **Ctrl+B, release, then W** to choose a window, or **Ctrl+B, then D**
to detach without stopping the run. The `dashboard` window shows the phase,
verified message count, work results, and missing startup conditions.

At completion it closes only its own harness windows, selects the dashboard,
and prints **PASS**, **UNVERIFIED**, or an operational failure with the summary
and ZIP location. The dashboard remains for inspection. A dead dashboard pane
is expected after finalization; its process exit code alone is not a proof verdict.

Useful launch variants:

```bash
# Messaging baseline only; label is optional and names remain unique.
bash scripts/proof.sh start --label baseline --no-work

# Start without attaching this terminal; native prompts still need attention.
bash scripts/proof.sh start --detach

# Load the existing experiment schema, including selected peers and pairs.
bash scripts/proof.sh start --config proof.live.example.json

# Explicit model overrides are recorded with this run.
bash scripts/proof.sh start --codex-reasoning medium
```

`--timeout` and `--startup-timeout` override waits. A missing/unsupported model
is reported by the harness; the launcher does not silently substitute another.
Configured profiles are respected. The shipped live example uses the new
`coordination` profile and omits work for a first baseline.

## Inspect, stop, and repeat

Replace `RUN_DIRECTORY` with the directory printed by the launcher or `list`.
Commands operate from the checkout via `scripts/proof.sh`.

```bash
bash scripts/proof.sh list
bash scripts/proof.sh status RUN_DIRECTORY
bash scripts/proof.sh status RUN_DIRECTORY --watch
bash scripts/proof.sh attach RUN_DIRECTORY
bash scripts/proof.sh stop RUN_DIRECTORY
bash scripts/proof.sh close RUN_DIRECTORY
```

`stop` requests orderly controller finalization and automatic collection. `close`
removes only a finished run's owned tmux dashboard and keeps all files. It refuses
to close an active run. Leaving the status viewer with Ctrl+C does not stop the
proof. Detaching from tmux also leaves it running.

Repeat a saved plan in a **new** directory:

```bash
bash scripts/proof.sh start --from RUN_DIRECTORY
bash scripts/proof.sh start --from RUN_DIRECTORY --claude-model sonnet --claude-reasoning medium
```

The copied fixture and resolved settings carry forward. Sessions, nonces, and
run directories are always new. Source code and installed harnesses are those
available at the new launch, so compare the recorded hashes and versions too.
This does not resume a native session or retry a delivered message.

## Files and diagnostic ZIPs

Every managed directory contains:

| Path relative to that directory | Contents |
| --- | --- |
| `plan.json`, `settings.json`, `fixture.json` | Saved plan, resolved settings, copied work input |
| `session.json`, `preflight.json`, `controller.log` | Managed lifecycle, probes, controller output |
| `summary.txt`, `summary.json` | Readable verdict, incomplete exchanges, work differences, capture status |
| `run/` | Report, events, native/MCP traces, configs, and run-local workspaces |
| `archives/diagnostics-<timestamp>.zip` | Automatically collected diagnostic bundle |

Runs have timestamped names plus a random suffix. Explicit `--dir` paths must
not exist. Recollection creates another timestamped ZIP without overwriting one:

```bash
bash scripts/proof.sh collect RUN_DIRECTORY
```

`status` and `collect` also work on older manual run directories. Collection is
local; it never uploads anything. Partial initialization and controller failures
are collected where filesystem access still permits it. If there is no finalized
report, the collection summary is **incomplete**, never an invented proof verdict.

For Claude, run-local hooks supply the exact native session's transcript path.
The collector reads that one file and writes a redacted snapshot as
`run/claude-transcript.jsonl`; it never searches other sessions or modifies the
native history. `claude-transcript-capture.json` records captured, partial,
unavailable, or error status. Changed identities/paths, leaf symlinks, wrong
ownership, and files over 128 MiB are rejected explicitly. A partial final JSONL
write is reported and can be recollected. Missing transcript data does not turn
a failed proof into a pass or replace native tool evidence.

The bundle also contains the controller log, progress/final summaries, permission
telemetry, and a final snapshot of each owned harness pane's available tmux
scrollback. Scrollback is bounded by tmux; native protocol/debug traces remain
the primary record. Credentials, generated MCP configs, workspaces, and the
native transcript source pointer are excluded; known secret values are redacted.
**Diagnostic ZIPs retain local paths and conversation content. Review them before
sharing; they are not automatically suitable for public publication.**

## Failure handling and limits

Only tmux sessions bearing this run's ownership token can be selected or closed.
Only owned peer panes are cleaned up; unrelated tmux sessions remain untouched.
No PID read from a saved file is used to kill an unrelated process. Launch
failures after one peer starts still finalize the controller, close owned peers,
and collect evidence.

Graceful stops preserve reports and release holds. SIGKILL, a system crash, disk
failure, or forcibly destroying the tmux server can prevent finalization. A saved
`running` state is not proof of liveness. Preserve files with `collect`, inspect
the controller log, and launch a fresh run; do not delete the failed directory.
Use the managed `stop` command for routine cancellation.

The new supervisor is tested with real project processes and fake vendor
executables plus a simulated tmux lifecycle. Real tmux interaction and the new
model combination still need a local live run. Existing manual launchers remain
available; their model default stays `economy` for reproducibility.

## Version 0.4.0 startup target error

If startup reports `tmux: no such session: =mhproof-...`, update to 0.4.1 and
start a fresh run:

```bash
git pull --ff-only
bash scripts/proof.sh start
```

Version 0.4.0 used a bare exact-session name for `set-option` and `show-options`,
whose target parser expects a pane expression. Version 0.4.1 supplies the explicit
session component (`=session:`). Session commands still use `=session`; ownership
checks and permission handling are unchanged. The failure precedes controller
and agent-session launch. Keep the failed directory and its diagnostic ZIP; no reclone
or deletion of run data is needed.
