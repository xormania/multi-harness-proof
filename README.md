# Multi-Harness Proof

**A local experiment in live coordination between Codex, Claude Code, and Grok Build.**

Coding harnesses expose different session, tool, and messaging interfaces. This
project tests whether agents in those harnesses can exchange messages, retain
earlier context, and continue useful work through a common local relay. Each
participant stays in one native session throughout the experiment.

The output is evidence: message traces, verified replies, scored work, and
diagnostics that explain what succeeded or failed on a particular installation.

> **Experimental:** version 0.3.1 includes 21 mocked end-to-end behavior scenarios
> and configurable coordination experiments. It fixes a reported live startup
> rejection of Grok's MCP discovery tool; a complete live pass remains unverified.
> See [validation status](VALIDATION.md).

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
- Optional: `tmux` to open the relay and three harness windows together.

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

### Explore failures offline

```bash
# Keep the reports and telemetry from all 21 predefined behavior scenarios.
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

### First run: messaging

Start with `--no-work`: six round trips and three busy-delivery checks. This
checks messaging and retained context before adding the work fixture.
Choose either tmux or separate terminals below.

#### Launch with tmux

```bash
bash scripts/tmux.sh runs/live1 --no-work
```

This creates a fresh run directory and a new tmux session with **relay**, **codex**,
**claude**, and **grok** windows. It opens the Claude window first for its native
development-channel and workspace prompts. Use `Ctrl-b w` to switch windows, and
check Grok's window for any permission prompts for the proof tools.

The experiment starts automatically when all participants and tools are ready.
Watch the relay window for results and the run directory. The helper changes
settings only for the tmux session/windows it creates.

`session launched` means a native session exists; proof readiness still requires
the agent's verified ready report. `Pane is dead (status 0)` is a wrapper exit,
not a proof result. Read `report.json` or the relay window for the verdict.
After a failed run, close Claude with `/exit`, detach with `Ctrl-b d`, update
with `git pull --ff-only`, and retry using a fresh directory. Keep the failed
run's evidence.

#### Launch in separate terminals

From the project directory, run each command in its own terminal:

| Terminal | Command |
| --- | --- |
| Relay | `bash scripts/start.sh runs/live1 --no-work` |
| Codex | `bash scripts/agent.sh codex runs/live1` |
| Claude | `bash scripts/agent.sh claude runs/live1` |
| Grok | `bash scripts/agent.sh grok runs/live1` |

Start the relay first. It also prints absolute-path commands for terminals opened
elsewhere. Accept Claude's native development-channel prompt when shown, and
respond to Grok's proof-tool permission prompts in its terminal. Those prompts
are serialized and may recur for every tool call; there is no blanket approval.
Agent messages
then travel between sessions automatically.

**Use a fresh run directory for every attempt.** Existing runs are never
overwritten or silently resumed. With no directory argument, `scripts/start.sh`
chooses a new name under `runs/`.

### Review, then add the work fixture

Watch the relay and `runs/live1/events.jsonl` for progress. After the run ends,
inspect `runs/live1/report.json` and preserve any diagnostics before retrying.
Resolve incomplete messaging checks before testing work overlap.

Close the first run's harness sessions, then launch the full suite in a fresh
directory:

```bash
bash scripts/tmux.sh runs/live2
```

For separate terminals, use `bash scripts/start.sh runs/live2` and launch each
agent with `runs/live2` in the commands above. The full suite repeats the messaging
checks, then adds six messages during work and three work scores. A successful
first run establishes the messaging baseline; the work stage separately tests
answer accuracy and message delivery before the fixture is finished. Later
batches are released by the controller, so a fast agent cannot finish before
the burst arrives. This measures interleaving with unfinished work, not
simultaneous computation; busy-tool delivery is a separate check.

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

For the manual example above:

```bash
cat runs/live1/report.json
python3 proof.py bundle --dir runs/live1 --out live1-diagnostics.zip
```

Use `runs/live2` and a different bundle filename for the full run. For other run
names, substitute the directory printed by the launcher.

| Result | Meaning |
| --- | --- |
| `pass` | All required evidence for that check matched. |
| `unverified` | Evidence was missing or incorrect, a timeout occurred, or the run was interrupted. |
| `unsupported` | A message case encountered a native method-not-found error. |
| `incorrect` | An agent submitted an incorrect work-fixture answer. |

The overall run passes only when all selected checks pass. On an incomplete
stage, the runner preserves earlier results and reports how many message cases
were not run. Diagnose incomplete runs using the evidence: launch errors, model
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
| Routing, native identity/tool observations, work gates, scores, and hold boundaries | `events.jsonl` |
| Grok catalog queries, normalized proof calls, and original `wire_tool`/`wire_arguments` | `events.jsonl` (`native_discovery`, `native_tool`) |
| Native requests, responses, notifications, launch details, timeouts, and exceptions | `codex-trace.jsonl`, `grok-trace.jsonl` |
| MCP calls, tool arguments/results, and channel notifications | `mcp-claude-trace.jsonl`, `mcp-grok-trace.jsonl` |
| Harness diagnostics | `codex-stderr.log`, `grok-stderr.log`, `claude-trace.jsonl`, `claude-debug.log` |
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

To stop, press `Ctrl-C` in the relay. It saves an incomplete report and releases
pending holds. Codex/Grok wrappers exit; close Claude with `/exit`. If a harness
hangs, interrupt it in its own terminal. Start a fresh run to retry.

## Models and run options

The default profile is named `economy` in the CLI and requests these starting
settings:

| Participant | Requested model | Requested reasoning |
| --- | --- | --- |
| Codex | `gpt-5.6-luna` | `low` |
| Claude Code | `haiku` | No override |
| Grok Build | Existing Grok model selection | `low` |

Model choice is independent of harness identity. Different models and reasoning
levels can help distinguish task-following failures from transport failures.
Availability and supported effort levels depend on the installation/account;
the initial defaults have not yet been verified in live runs.

Use separate terminals for custom settings. Each command below is an alternative
launch for its participant, not an additional participant in the same run:

```bash
bash scripts/agent.sh claude runs/live1 --model sonnet --reasoning low
bash scripts/agent.sh grok runs/live1 --profile existing
bash scripts/agent.sh codex runs/live1 --binary /path/to/codex
```

`--model` and `--reasoning` are available for all adapters. `--profile existing`
leaves model/reasoning defaults to the harness unless explicitly overridden.
Settings are passed through invocation arguments or native session parameters.

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
Python interpreter. The tmux helper uses all three participants and the default
profile; it forwards run options such as `--no-work`, `--timeout`, and
`--startup-timeout` after the directory argument. Use separate terminals for
`--peers` or custom agent settings. See `python3 proof.py --help` for the CLI entry
points.

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
