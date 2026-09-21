# Operations guide

[← Project overview](README.md) · [Documentation map](README.md#documentation)

The managed launcher owns the controller, harness windows, progress display,
and final diagnostic collection. It uses an installed tmux and existing harness
authentication. No global harness or tmux configuration is changed.

<details>
<summary>On this page</summary>

- [Start](#start)
- [Inspect, stop, and repeat](#inspect-stop-and-repeat)
- [Files and diagnostic ZIPs](#files-and-diagnostic-zips)
- [Models and run options](#models-and-run-options)
- [Separate terminals and the original launcher](#separate-terminals-and-the-original-launcher)
- [Update the checkout and harnesses](#update-the-checkout-and-harnesses)
- [Failure handling and limits](#failure-handling-and-limits)
- [Version 0.4.0 startup target error](#version-040-startup-target-error)

</details>

## Start

```bash
git pull --ff-only
bash scripts/proof.sh start
```

This selects the full fixture suite and the `coordination` profile: Luna, Sonnet,
and Grok 4.5, with low reasoning requested for each. See
[models and run options](#models-and-run-options) for overrides and observed
settings.

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

## Models and run options

The managed launcher defaults to the `coordination` profile:

| Participant | Requested model | Requested reasoning |
| --- | --- | --- |
| Codex | `gpt-5.6-luna` | `low` |
| Claude Code | `sonnet` | `low` |
| Grok Build | `grok-4.5` | `low` |

These are requested settings, not a claim of effective vendor selection.
Availability and effort support depend on the installed harness and account.
The confirming live run used this requested profile. Native telemetry identified
Claude as `claude-sonnet-5` and Grok as `grok-4.6` / `xhigh`, despite the Grok
request above. These are recorded run conditions; model selection was not the
mechanical acceptance criterion. See [LIVE_RESULTS.md](LIVE_RESULTS.md).

```bash
bash scripts/proof.sh start --codex-reasoning medium
bash scripts/proof.sh start --from RUN_DIRECTORY --claude-model sonnet
bash scripts/proof.sh start --config proof.live.example.json
```

The existing `economy` profile remains Luna/low, Haiku/no effort override, and
Grok's existing model/low. Manual launches and configurations without an explicit
profile retain that default. `existing` leaves both choices to the harness.
The shipped live plan now explicitly selects `coordination` for all peers.
All profiles allow explicit per-agent model and reasoning overrides.

Other run options:

```bash
# Two participants: launch only Codex and Grok against this run directory.
bash scripts/start.sh runs/two --peers codex grok

# Isolate round-trip and busy-delivery checks from the work fixture.
bash scripts/start.sh runs/messages --no-work

# Allow longer per-step and startup waits.
bash scripts/start.sh runs/slow --timeout 300 --startup-timeout 900
```

The default `--startup-timeout 600` covers participant registration and required
MCP connections. After that, `--timeout 180` applies to individual readiness and
verification waits; each work-batch wait allows three times that value. Grok's
prompt RPC deadline is three times the value plus 30 seconds, so it does not cut
that work wait short. An RPC timeout records unknown activity, never idle.
Approve native channel and proof-tool prompts promptly: a prompt that blocks the
initial ready report is subject to the readiness wait, not a new 600-second wait.

Set `PROOF_PYTHON=/path/to/python3` when using the Bash scripts to select another
Python interpreter. The managed launcher accepts a live JSON plan for selected
peers, message pairs, and burst sizes. The original `scripts/tmux.sh` remains a
manual three-peer helper. See `python3 proof.py session start --help` and
[EXPERIMENTS.md](EXPERIMENTS.md) for configuration.

## Separate terminals and the original launcher

Manual launchers remain available with their original `economy` defaults:

| Terminal | Command |
| --- | --- |
| Relay | `bash scripts/start.sh runs/manual1 --no-work` |
| Codex | `bash scripts/agent.sh codex runs/manual1 --profile coordination` |
| Claude | `bash scripts/agent.sh claude runs/manual1 --profile coordination` |
| Grok | `bash scripts/agent.sh grok runs/manual1 --profile coordination` |

Start the relay first; each command runs in its own terminal. Explicit
`--profile coordination` requests the newer models. Existing directories are
refused. The original `scripts/tmux.sh` also remains available but does not provide
the managed launcher's automatic finalization and collection.

After a messaging baseline passes, omit `--no-work` in a new run to add the
fixture. Its gated later batches preserve unfinished work while the burst is
submitted. This measures interleaving and answer accuracy, not simultaneous
model computation.

## Update the checkout and harnesses

Keep the checkout and its previous run directories. Pull new proof code with:

```bash
git pull --ff-only
```

To update all three installed harnesses, run this separately between sessions:

```bash
bash scripts/update-harnesses.sh
# Optional: preview the selected commands without downloading or updating.
bash scripts/update-harnesses.sh --dry-run
```

The script uses the [documented Codex updater](https://developers.openai.com/codex/cli)
for standalone installations, the existing npm global installation, or Homebrew.
Auto detection recognizes the usual `~/.local/bin/codex`, npm-global, and Homebrew
paths. For a custom installation, explicitly select `--codex-method standalone`,
`npm`, or `brew` after checking how it was installed. An unknown method is reported
without replacing that installation.

For native/npm Claude installations it runs
[`claude update`](https://code.claude.com/docs/en/setup#update-manually);
recognized Homebrew Claude installations use their existing cask. Grok uses
[`grok update`](https://docs.x.ai/build/cli/reference) with automatic background
updating disabled for that invocation. Vendor release-channel settings still apply.

Each invocation creates `runs/updates-<timestamp>-<pid>/` containing command/path
records, before/after versions, updater output, and `summary.tsv`. Codex's downloaded
standalone installer is saved before execution; a failed download is never run.
Each harness is attempted even if another fails, with a nonzero final exit for
failures, missing executables, or an unknown install method. Missing harnesses
are not installed. `updated-or-current` means the updater and version probe
succeeded; inspect its output and versions for the actual change.

The script does not invoke `sudo`, edit permission/MCP/authentication settings,
or run model sessions. Vendor updaters manage their own installed files and
maintenance state. Proof launch scripts never invoke this maintenance script.
For managed OS-package installations, use that package manager's update command.

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

The managed launcher completed the full live 0.4.1 proof on 2026-09-20 UTC,
including harness cleanup, transcript capture, summaries, and ZIP collection.
[Live results](LIVE_RESULTS.md) record the native settings actually observed.
Mocked lifecycle tests separately cover failure handling; optional real-tmux
checks use an isolated socket. Existing manual launchers remain available; their
model default stays `economy` for reproducibility.

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
