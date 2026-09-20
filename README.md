# Multi-Harness Proof

**Confirmed proof of live coordination between Codex, Claude Code, and Grok Build.**

Coding harnesses expose different session, tool, and messaging interfaces. This
project tests whether agents in those harnesses can exchange messages, retain
earlier context, and continue useful work through a common local relay. Each
participant stays in one native session throughout the experiment.

The output is evidence: message traces, verified replies, scored work, and
diagnostics that explain what succeeded or failed on a particular installation.

> **Status: CONFIRMED.** The full live proof passed on 2026-09-20 UTC using
> version 0.4.1: **15/15 message checks, 9/9 correct work batches, and 69/69
> matched native tool calls** in approximately 4 minutes 35 seconds. Persistent
> sessions retained context and coordinated during unfinished work. Managed tmux
> cleanup and automatic diagnostic collection also completed successfully.
> [Read the live evidence, observed settings, and measured scope](LIVE_RESULTS.md).
>
> The PoC's mechanical objective is complete. The repository remains available
> as a reference implementation and compatibility regression project.
> [Validation status](VALIDATION.md) separates the live result from offline tests.

## What gets tested

The default three-harness run performs **15 message checks and three work scores**.
Each participant first receives a distinct context marker. Later replies must
include the remembered marker and a fresh challenge value.

| Check | Required evidence | Count |
| --- | --- | ---: |
| Round trips | Every ordered pair exchanges a challenge and a verified reply in its original sessions. | 6 |
| Delivery while busy | A message is submitted while the recipient has an outstanding proof-tool call, then receives a verified reply. | 3 |
| Messages during work | Each agent sends two challenges while every recipient has an unfinished fixture; each requires a verified reply. | 6 |
| Work accuracy | Each agent correctly identifies the latest failed job attempts across three build-log batches. | 3 |

The [work fixture](fixtures/README.md) contains 72 synthetic log records per agent,
including retries and out-of-order entries. All agents receive the same batches
and are scored independently. It exercises context, tool use, and
message handling without requiring changes to a real codebase. The relay
transports messages; the actual agents must invoke the tools and solve the task.

## Quick start

### Requirements

- Python **3.10+** on Linux, macOS, or WSL. No additional Python packages are required.
- Installed and authenticated **Codex**, **Claude Code**, and **xAI Grok Build** CLIs,
  available as `codex`, `claude`, and `grok` in the current shell.
- Claude Code support for custom development channels on your installation/account.
- `tmux` for the recommended managed launcher; separate terminals also work.

The launcher uses existing installations and authentication. It does not install
or upgrade harnesses. Runs with two participants are also supported.

### Get the project

```bash
git clone https://github.com/xormania/multi-harness-proof.git
cd multi-harness-proof
python3 proof.py doctor
bash scripts/test.sh
```

`doctor` reports executable paths and versions and generates a temporary schema
from the installed Codex binary to check `TurnStartParams.toolOutput`. The Codex
adapter repeats that check before starting a session. Missing or inconclusive
schema support stops that launch with a diagnostic. Neither the probe nor the
tests make model calls. Account policies, native hooks, and permission prompts
still need to be checked during a live run.

### Update an existing clone and the harnesses

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

### Explore failures offline

```bash
# Keep the reports and telemetry from all 24 predefined behavior scenarios.
bash scripts/behavior.sh --dir runs/mock1

# Configure message pairs, bursts, delays, and dropped messages; preserve each run.
bash scripts/experiment.sh proof.mock.example.json
```

The second command runs the supplied configuration in a new timestamped directory.
Edit it and rerun to compare behavior across settings. Neither command launches
real vendor CLIs or makes model calls. A crash or dropped message must produce an
unverified proof to meet its expected outcome.

See [behavior tests and telemetry](TESTING.md) and
[configurable experiments, run history, and comparison](EXPERIMENTS.md). Both
workflows retain the real verifier and label simulated reports `mock`.

### Run with automatic session management

```bash
# Full suite: messaging plus the work fixture.
bash scripts/proof.sh start

# For a first installation, test the messaging baseline alone.
bash scripts/proof.sh start --no-work
```

Choose one command per run. The launcher creates a fresh, automatically named
run under `runs/`, saves its settings and fixture, and opens a dedicated tmux
session with a dashboard and one window per harness. It requests **Luna, Sonnet,
and Grok 4.5, all with low reasoning**.

Accept Claude's development-channel prompt and respond to Grok's proof-tool
permission requests. Those are native interactive approvals; the launcher does
not bypass them. Press **Ctrl+B, then W** to choose a window. The dashboard shows
startup conditions, the current phase, message counts, and work results.

**When it finishes:** the launcher closes its harness windows, returns to the
dashboard, and writes `summary.txt`, `summary.json`, and a timestamped diagnostic
ZIP under that run's `archives/`. The summary separates missing replies, work
accuracy errors, and native-tool evidence failures. No manual ZIP naming or
routine tmux cleanup is needed. The dashboard stays open so you can read it.

```bash
bash scripts/proof.sh list
bash scripts/proof.sh status RUN_DIRECTORY
bash scripts/proof.sh stop RUN_DIRECTORY
bash scripts/proof.sh close RUN_DIRECTORY

# Repeat the saved plan and fixture in a NEW run.
bash scripts/proof.sh start --from RUN_DIRECTORY
```

Use the directory printed by `start` or `list` for `RUN_DIRECTORY`.
`stop` finalizes an active run; `close` removes a finished dashboard and retains
its files. Detach with **Ctrl+B, then D** to leave a run running.

See [managed-run operations](OPERATIONS.md) for model overrides, configured
scenarios, permission handling, transcript capture, recollection, and recovery.
The protocol instructions and pass predicate are unchanged by the new models.

### Separate terminals and the original launcher

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

## How coordination works

A Python relay listens on the loopback interface and maintains an inbox for each
participant. Adapters handle polling and native delivery. Models receive messages
through their harnesses and use proof tools to reply, report results, and work.

| Harness | Persistent session | Incoming messages | Outgoing proof tools |
| --- | --- | --- | --- |
| Codex | App-server; one thread | `turn/start.toolOutput` | Session-scoped dynamic tools |
| Claude Code | Native terminal UI; explicit session ID | MCP `notifications/claude/channel` | Stdio MCP |
| Grok Build | ACP over stdio; one session | `session/prompt` when idle; `_x.ai/interject` when active | Stdio MCP |

The launcher creates new sessions and keeps them alive across messages. Codex and
Grok display adapter output; Claude uses its native terminal UI. Attaching to
unrelated existing terminal sessions is outside the current implementation.

The proof records queueing, native submission, and model responses separately.
**A passing check requires the complete reply and context evidence.**

Native observations independently identify the tools that sent the challenge,
sent the reply, and reported receipt. Their session IDs must match registration:

| Harness | Native identity and tool evidence | Completion evidence |
| --- | --- | --- |
| Codex | `threadId` and `callId` on native `item/tool/call` requests | Native turn events |
| Claude Code | `session_id` and `tool_use_id` from a run-local `PreToolUse` hook | Run-local `Stop` hook |
| Grok Build | `sessionId`, `toolCallId`, tool name, and arguments on ACP tool notifications | Native completion notifications plus outstanding requests and interjections |

Relay events label registration as `registered_session_id`; this label is not
native identity evidence. Missing native observations leave the run unverified.
Claude's hooks are supplied through a generated run-local `--settings` file.
Startup requires a ready report with the channel-delivered memory marker and a
matching native tool observation; an MCP connection alone is insufficient.
Before sending any startup instructions, the controller also waits for Claude's
native `Channel notifications registered` entry for `coord_proof` in this run's
`claude-debug.log`. The entry observed in Claude Code 2.1.278 is recorded as
`channel_ready`, including its timestamp and source line. This is a
**version-sensitive diagnostic gate**, not a protocol acknowledgment or session
identity proof. Missing or changed log text leaves startup unverified at an
explicit channel-registration phase. No startup messages are retried, and the
ready-report/native-tool checks still establish actual receipt.
Grok prefers versioned `x.ai/tool` wire-name metadata, with exact ACP tool titles
as a fallback when that metadata is absent. Unknown versions, conflicting names,
and unrelated tools stop verification; display prose alone is insufficient.

Grok can expose MCP tools through two native helpers. `search_tool` searches the
tool catalog and is recorded as `native_discovery`, never proof evidence.
`use_tool` is accepted only when `tool_name` is exactly one of the five
`coord_proof__proof_*` tools and `tool_input` is a valid proof arguments object.
The adapter matches that inner call to the actual MCP execution while preserving
the outer wire name and arguments. Other MCP servers and built-in tools remain
outside the proof. Neither helper changes the permission policy.

## Results and diagnostics

Managed runs print their final summary and create a diagnostic ZIP automatically.
To inspect or recollect either a managed run or an older manual run:

```bash
bash scripts/proof.sh status RUN_DIRECTORY
bash scripts/proof.sh collect RUN_DIRECTORY
```

The full report lives in `RUN_DIRECTORY/run/report.json` for managed runs,
or directly in the directory for manual runs. Nothing is uploaded automatically.

| Result | Meaning |
| --- | --- |
| `pass` | All required evidence for that check matched. |
| `unverified` | Evidence was missing or incorrect, a timeout occurred, or the run was interrupted. |
| `unsupported` | A message case encountered a native method-not-found error. |
| `incorrect` | An agent submitted an incorrect work-fixture answer. |

The overall run passes only when all selected checks pass. On an incomplete
stage, the runner preserves earlier results and reports how many message cases
were not run. Within an already-launched work burst, every exchange is evaluated
independently; one missing reply does not hide other complete exchanges. Diagnose incomplete runs using the evidence: launch errors, model
behavior, account policy, and transport compatibility are different failure modes.
For example, verified round trips followed by an unsupported Grok busy-delivery
method remain useful evidence of idle messaging. They do not establish a busy
delivery pass.

Each message case lists `native_evidence_seqs`. Busy cases also record
`timing.reply_before_hold_finished` and, where both native turn IDs are available,
`timing.reply_in_hold_turn`. Work cases record `timing.reply_before_work_completed`.
These timing observations do not change the round-trip predicate. A later-turn
reply in the same session is recorded honestly; it does not prove mid-turn handling.
An unavailable turn comparison is `null`, not a successful comparison.
Late RPC acknowledgements are also recorded separately. Incomplete cases include
`diagnostics` with submission sequences, nonce/memory match indicators, and native
tool observations. The report's `evidence_last_seq` identifies its verdict snapshot.

The overall verdict also requires `tool_audit.status` to pass: each relay tool
call must have a matching native observation, with distinct native call IDs.
Reusing a call ID with different arguments or identity invalidates the audit;
identical repeated observations cannot cover additional calls. Discovery calls
are counted separately as `native_discovery_calls`; they cannot cover relay calls,
and reusing a discovery call ID for proof execution invalidates the audit.
Apart from Grok's scoped MCP discovery/dispatch helpers, observed use of
non-proof tools invalidates the run, including automatically
allowed Codex read commands. This detects violations of the cooperative test;
it is not an operating-system access boundary.

Local telemetry is enabled for every run:

| Evidence | Files |
| --- | --- |
| Verdicts, session IDs, settings, capability probes, stage, timings, and tool audit | `report.json` |
| Per-peer missing startup conditions and ready-report/native evidence sequences | `report.json` (`startup`), `events.jsonl` (`startup_progress`) |
| Claude channel registration gate, native timestamp, and debug source line | `events.jsonl` (`channel_ready`), `claude-debug.log` |
| Routing, native identity/tool observations, work gates, scores, and hold boundaries | `events.jsonl` |
| Grok catalog queries, normalized proof calls, and original `wire_tool`/`wire_arguments` | `events.jsonl` (`native_discovery`, `native_tool`) |
| Native requests, responses, notifications, launch details, timeouts, and exceptions | `codex-trace.jsonl`, `grok-trace.jsonl` |
| MCP calls, tool arguments/results, and channel notifications | `mcp-claude-trace.jsonl`, `mcp-grok-trace.jsonl` |
| Harness diagnostics | `codex-stderr.log`, `grok-stderr.log`, `claude-trace.jsonl`, `claude-debug.log` |
| Exact-run Claude conversation snapshot and explicit capture status | `claude-transcript.jsonl`, `claude-transcript-capture.json` |
| Managed lifecycle, progress, readable results, and pane snapshots | `session.json`, `controller.log`, `progress.json`, `summary.*`, `*-terminal.log` |
| Runtime information and hashes of the proof source and fixture | `manifest.json` |
| Configured launch requests and the copied work input | `settings.json`, `fixture.json` |

Protocol traces are flushed after each entry and include wall-clock and monotonic
timestamps. Native usage/model metadata is retained when the harness exposes it.
The report records requested settings; effective settings require native evidence.
Timeouts retain activity/audit snapshots. Grok permission requests include decision
and wait timing. Configured experiments additionally preserve their original
configuration, resolved defaults, scenario definitions, and per-run history.

The diagnostic bundle is a local ZIP; nothing is uploaded automatically. It
excludes run credentials, generated MCP configuration, and workspaces, and
redacts known credential values. **Review a bundle before sharing it publicly:**
test prompts, responses, and local paths are intentionally retained, and native
debug output may contain sensitive strings that redaction does not recognize.

For managed runs, use `bash scripts/proof.sh stop RUN_DIRECTORY` to finalize
and collect. For manual runs, press `Ctrl-C` in the relay and close Claude with
`/exit`. Start a fresh run to retry.

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

## Configuration and limits

Setup uses generated run files and session arguments. It does not edit global
harness configuration, register global MCP servers, copy authentication, or use
permission-bypass modes. Claude's development-channel flag enables a custom
channel subject to native confirmation. Claude's built-in tools are disabled,
Codex uses a read-only sandbox and declines approval requests, and Grok retains
native permission prompts. Claude's run-local hook rejects non-proof tools;
Codex and Grok tool observations invalidate the proof if unrelated tools appear.
The Grok adapter recognizes catalog discovery and exact proof MCP dispatch as
described above; that exception supplies no proof evidence for discovery itself.
Grok's built-in tools are not stripped by this launcher. Scoped Grok allow rules
have not been adopted without verifying their behavior under `agent stdio`.

Harnesses still use their normal identity and may read existing settings, hooks,
and ancestor instructions or write their own session history/cache. Use a
standalone checkout with an appropriate surrounding environment. This proof
does not provide OS isolation between agents running as the same user.

Interpret results within the experiment's measured boundaries:

- **Busy delivery** measures overlap with an outstanding proof-tool call, followed
  by a correct reply. It does not certify interruption during token generation.
- **Completion observation** uses Codex turn events, Claude hooks, and Grok
  completion notifications. Grok interjections can become later prompt turns;
  outstanding deliveries remain active until native handling and completion
  evidence arrive. The adapter does not invent native turn-start events from RPCs.
- **Context retention** is a cooperative task check, not an adversarial test of
  access to other processes or logs.
- **Work overlap** means submission between fixture start and completion. The
  burst is synchronized after every first batch, while later batches are gated.
  It does not demonstrate simultaneous model computation or interruption.
- **Compatibility** is version-sensitive: the experiment uses preview or
  experimental interfaces and a Grok extension. Unsupported behavior is recorded.

## Troubleshooting and contributing

| Symptom | First check |
| --- | --- |
| Executable missing | Check `PATH`, run `doctor`, or use `agent --binary`. |
| Codex schema unsupported or unverified | Inspect `doctor` output; this installed binary must expose `TurnStartParams.toolOutput`. |
| Claude never becomes ready | Inspect `/mcp`, channel confirmation, native hook execution, and account/organization channel availability. |
| Waiting for Claude channel handler registration | Inspect `claude-debug.log` for the exact `coord_proof` registration marker. Missing or changed diagnostic output is not assumed ready. |
| Startup appears idle | Read the relay's per-peer missing conditions or `report.json.startup` after it stops. Registration, notification submission, and verified receipt are separate conditions. |
| Grok never becomes ready | Check its terminal for proof-tool permission prompts, confirm xAI Grok Build supports `agent stdio`, and inspect authentication and logs. |
| v0.3.0 rejects Grok `search_tool` during startup | Update to v0.3.1 or later and use a fresh run directory. MCP catalog discovery is now recorded separately from proof execution. |
| Panes exit, then Claude hooks report connection refused | Read the relay's `report.json.detail` first. A stopped relay can cause later hook failures; pane exit status does not establish a pass. |
| Native method or tool missing | Compare the installed CLI with the interface references below. Preserve the failed run. |
| Message queued without a pass | Follow its ID through submission, reply, and receipt events. |
| Native evidence missing | Inspect `native_tool` events and IDs; MCP readiness and relay registration cannot substitute for native observations. |
| Grok never settles | Check completion notifications and pending interjections in `activity` events. An RPC response alone does not prove idle. |
| Tool audit fails | Inspect `tool_violation` events and unmatched calls; do not use shell/file tools to obtain markers or answers. |
| Work answer is incorrect | Compare `work_scored` events with the fixture rules; separate answer accuracy from transport. |
| Run directory already exists | Choose a fresh directory. |

For a reproducible issue, include CLI versions, requested model/reasoning settings,
the failing case, and a reviewed diagnostic excerpt. Use the
[issue tracker](https://github.com/xormania/multi-harness-proof/issues).

Contributions that improve native compatibility, diagnostics, or reproducibility
are welcome. Read [AGENTS.md](AGENTS.md), keep changes focused on the proof, and
run `bash scripts/test.sh` after protocol changes. Identify live results separately
from simulated tests.

The main modules are [adapters](mhproof/adapters.py), [relay](mhproof/relay.py),
[MCP server](mhproof/mcp.py), [verifier](mhproof/suite.py), and
[telemetry](mhproof/telemetry.py). GitHub is the maintained source; no checked-in
source ZIP is distributed. Diagnostic bundles remain available for local runs.

## Interface references

Interfaces reviewed for the initial implementation on **2026-09-19**:

- [Codex app-server](https://developers.openai.com/codex/app-server)
- [Claude Code channels](https://code.claude.com/docs/en/channels-reference)
- [Claude Code hook input and configuration](https://code.claude.com/docs/en/hooks)
- [Grok Build ACP/headless interface](https://docs.x.ai/build/cli/headless-scripting)
- [Grok Build CLI options](https://docs.x.ai/build/cli/reference)
- [Grok interjection extension at the reviewed commit](https://github.com/xai-org/grok-build/blob/482711333c7195dc16a272777f86086d615e2afb/crates/codegen/xai-grok-shell/src/extensions/interject.rs)
- [Grok native completion signals at the reviewed commit](https://github.com/xai-org/grok-build/blob/482711333c7195dc16a272777f86086d615e2afb/crates/codegen/xai-grok-shell/src/session/turn_completion.rs)
- [Grok initial tool notifications](https://github.com/xai-org/grok-build/blob/482711333c7195dc16a272777f86086d615e2afb/crates/codegen/xai-grok-shell/src/session/acp_session_impl/tool_calls.rs)
- [Grok canonical tool metadata contract](https://github.com/xai-org/grok-build/blob/482711333c7195dc16a272777f86086d615e2afb/crates/codegen/xai-grok-tools/schema/tool_meta.schema.json)
- [Grok MCP catalog discovery schema](https://github.com/xai-org/grok-build/blob/482711333c7195dc16a272777f86086d615e2afb/crates/codegen/xai-grok-tools/src/implementations/search_tool/types.rs)
- [Grok MCP dispatch schema and implementation](https://github.com/xai-org/grok-build/blob/482711333c7195dc16a272777f86086d615e2afb/crates/codegen/xai-grok-tools/src/implementations/use_tool/mod.rs)
- [ACP session and MCP setup](https://agentclientprotocol.com/protocol/v1/session-setup)

## License

[MIT](LICENSE). Maintained by [xormania](https://github.com/xormania).
