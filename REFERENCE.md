# Protocol and evidence reference

[← Project overview](README.md) · [Documentation map](README.md#documentation)

Native session interfaces, verification rules, telemetry, and compatibility
boundaries for the confirmed proof. For launch commands, use the
[operations guide](OPERATIONS.md); for measured outcomes, read the
[live results](LIVE_RESULTS.md).

<details>
<summary>On this page</summary>

- [How coordination works](#how-coordination-works)
- [Results and diagnostics](#results-and-diagnostics)
- [Configuration and limits](#configuration-and-limits)
- [Troubleshooting](#troubleshooting)
- [Interface references](#interface-references)

</details>

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

### Native identity and completion

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

### Startup gates

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

### Grok tool identity and dispatch

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

### Timing and incomplete cases

Each message case lists `native_evidence_seqs`. Busy cases also record
`timing.reply_before_hold_finished` and, where both native turn IDs are available,
`timing.reply_in_hold_turn`. Work cases record `timing.reply_before_work_completed`.
These timing observations do not change the round-trip predicate. A later-turn
reply in the same session is recorded honestly; it does not prove mid-turn handling.
An unavailable turn comparison is `null`, not a successful comparison.
Late RPC acknowledgements are also recorded separately. Incomplete cases include
`diagnostics` with submission sequences, nonce/memory match indicators, and native
tool observations. The report's `evidence_last_seq` identifies its verdict snapshot.

### Native tool audit

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

### Telemetry inventory

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

### Bundles and shutdown

The diagnostic bundle is a local ZIP; nothing is uploaded automatically. It
excludes run credentials, generated MCP configuration, and workspaces, and
redacts known credential values. **Review a bundle before sharing it publicly:**
test prompts, responses, and local paths are intentionally retained, and native
debug output may contain sensitive strings that redaction does not recognize.

For managed runs, use `bash scripts/proof.sh stop RUN_DIRECTORY` to finalize
and collect. For manual runs, press `Ctrl-C` in the relay and close Claude with
`/exit`. Start a fresh run to retry.

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

## Troubleshooting

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
