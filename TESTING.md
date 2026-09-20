# Behavior testing

The mock tests check how the plumbing and verifier respond to realistic failures.
A separate full live run on 2026-09-20 confirmed the coordination mechanics;
see [LIVE_RESULTS.md](LIVE_RESULTS.md). Mock outcomes remain evidence about the
plumbing and verifier, separate from native harness compatibility.

## Run the checks

```bash
bash scripts/test.sh
```

This includes unit/integration tests and all 24 process-level behavior scenarios
in temporary directories. It requires Python 3.10+ on Linux, macOS, or WSL, with
no installed vendor CLIs, login, model calls, or third-party packages.

To retain every scenario's evidence, use a fresh directory:

```bash
bash scripts/behavior.sh --dir runs/mock1
bash scripts/behavior.sh --list
bash scripts/behavior.sh --dir runs/mock-crash --scenario crash
```

The mock per-step deadline defaults to three seconds. Add `--timeout 8` on a
slower machine. A process watchdog bounds hangs. Scenarios run sequentially in
fresh sessions; existing directories are refused. This is a fixed set of plausible
faults, not an arbitrary fuzzing framework.

For custom message pairs, burst sizes, and precisely targeted delays/drops, use
the [experiment configuration](EXPERIMENTS.md).

## What runs end to end

The controller, relay, adapters, MCP servers, generated Claude hooks, work grader,
verifier, and report writer are real project code. The three harness executables
and model behavior are scripted. Explicit fake executable paths are used; the
runner never discovers or launches installed vendor CLIs.

Fake Grok exercises later fallback turns with no matching prompt RPC. Fake Claude
uses later turns and executes the generated hook commands. Unequal work speeds
exercise the batch gate. These deterministic answers establish neither model
competence nor vendor compatibility.

| Scenario | Expected proof result | Evidence checked |
| --- | --- | --- |
| `happy` | `pass` | All 15 default messages, three work scores, original native IDs, counted tool audit |
| `delayed-duplicate` | `pass` | Late RPC acknowledgements/native observations; duplicate observations do not add calls |
| `prose-title` | `pass` | Grok display text changes while canonical tool identity remains exact |
| `claude-channel-delayed` | `pass` | MCP and the initial turn finish before channel registration; all 15 messages and three work scores still pass |
| `claude-channel-unobserved` | `unverified` | A changed registration log format prevents startup notification submission and names the missing gate |
| `claude-channel-drop` | `unverified` | Registration is observed but the notification is dropped; Claude's missing report remains distinct from Codex/Grok readiness |
| `grok-discovery` | `pass` | Native catalog search plus actual MCP `tools/list`, followed by wrapped `use_tool` calls through all 15 messages and three work scores |
| `grok-discovery-only` | `unverified` | Discovery cannot cover a ready report whose native execution observation is missing; no message cases start |
| `grok-wrong-dispatch` | `unverified` | Wrapped dispatch to another MCP server invalidates the audit; the fake never invokes that server |
| `queued-only` | `unverified` | Submission without handling cannot pass |
| `wrong-memory` | `unverified` | Correct routing cannot compensate for forgotten context |
| `stale-nonce` | `unverified` | A reply to an earlier challenge cannot satisfy the current case |
| `missing-native` | `unverified` | A real MCP reply cannot replace its missing native tool observation |
| `missing-hook` | `unverified` | Claude readiness/reporting without native tool hooks fails startup |
| `session-drift` | `unverified` | A changed native session produces an identity error |
| `unsupported-interject` | `unverified`; case `unsupported` | Method-not-found remains distinct from successful queueing |
| `permission-denied` | `unverified` | Denial stops verification; request, decision, and waiting time are retained |
| `crash` | `unverified` | Native EOF records a delivery/process failure and preserves earlier passes |
| `malformed-stdout` | `unverified` | Non-JSON protocol output is retained and diagnosed |
| `missing-completion` | `unverified` | A prompt RPC result cannot invent native completion |
| `incorrect-work` | `unverified`; work `incorrect` | Passing transport stays visible alongside an incorrect work answer |
| `reused-call-id` | `unverified` | Different calls sharing a native ID cannot qualify as independent evidence |
| `extra-tool` | `unverified` | A native non-proof tool invalidates the audit; the fake does not read files |
| `interrupted` | `unverified` | Interrupting a stalled case saves the report and decision event |

`MATCHED` means the verifier produced the expected outcome. For a crash, that
means an **unverified proof** with the specific failure evidence. A timeout at an
unrelated stage is insufficient. Mismatches return a nonzero exit code. Earlier
successful cases remain visible. Sequential baseline checks stop at the first
incomplete case. Already-launched work exchanges are evaluated independently,
so one dropped burst message does not hide complete neighboring exchanges.

## Telemetry

`behavior-summary.json` lists scenario expectations, actual verdicts, assertion
failures, launch arguments, wrapper exit codes, and cleanup signals. Each
scenario has its own `run/` containing:

- The normal `report.json`, `events.jsonl`, manifest, native protocol traces,
  MCP traces, hook observations, and diagnostics. Reports say `mode: "mock"`.
- `scenario.json`, with the exact definition and hashes of the mock runner and
  harness fixture. The normal manifest hashes production code and the work input.
- `fake-*-faults.jsonl`, with fault timestamps, target peers and, for configured
  rules, message IDs, case IDs, rule indices, and occurrence counts. These records
  do not substitute for evidence from the real verifier.
- `controller.log`, `*-launcher.log`, and `behavior.json`, covering process output,
  expected-versus-observed assessment, exit codes, and explicit cleanup signals.

Timeout events retain per-peer activity and the tool audit. Incomplete cases have
`diagnostics` showing challenge/submission sequences, reply and receipt nonce/
memory comparisons, and native-tool sequences. Grok permission telemetry records
queue delay, total wait, and the decision. Native RPC traces retain protocol
requests, responses, notifications, stderr, timeouts, and malformed lines.
Grok catalog searches have separate `native_discovery` events and audit counts.
Wrapped proof observations retain `wire_tool` and `wire_arguments` alongside the
normalized inner tool and arguments. Discovery never fills a native-evidence gap.
Claude's `channel_ready` event records the native registration log's timestamp
and source line. `startup_progress` and the final report's `startup` map identify
missing conditions per peer. Unit tests cover partial log writes, oversized lines,
unrelated server/text, and truncation; no fixed sleep establishes readiness.

The harness updater is tested with fake executables and a fake downloaded
installer. Tests cover all three update paths in a native-install scenario,
dry runs with no downloads, continued progress after one updater fails, failed
downloads never being executed, and unknown installation methods. These tests
never run real package managers or vendor updates.

`report.json.evidence_last_seq` marks the verdict snapshot; shutdown can append
later events. Acknowledgement can arrive after the model has already replied.
`challenge_ack_after_reply` and `reply_ack_after_receipt` record that timing without
claiming the acknowledgement timestamp was the actual ingestion time.

```bash
python3 proof.py bundle --dir runs/mock1/crash/run --out mock-crash-diagnostics.zip
```

Mock bundles include scenario, process, and fault records. Credentials, generated
MCP configs, and workspaces are excluded; known secrets are redacted. These are
protocol-visible observations, not access to private vendor internals. Review
bundles before sharing: local paths and test transcripts remain. Do not commit
run directories or diagnostics.

## Live checks

Run `python3 proof.py doctor`, then start with the README's messaging-only run:

```bash
bash scripts/proof.sh start --no-work
```

Preserve its diagnostics before attempting the work fixture in another fresh
directory. Configured live runs are also described in [EXPERIMENTS.md](EXPERIMENTS.md).
Mocks cannot authenticate accounts, accept native channel prompts, verify model
discipline, or establish compatibility with installed CLI versions.

## Managed lifecycle and transcript tests

Additional process tests launch the real controller, adapters, hooks, and relay
against fake harness executables through a simulated tmux lifecycle. They cover
full completion and collection, partial peer launch failure, orderly stop,
controller failure before a report, preflight failure, missing tmux, ownership
checks, unique run names, and saved-plan/fixture preservation. They make no model
calls and do not establish real tmux compatibility.

Transcript checks cover exact native identity, redaction, read-only source
handling, permissions, a partially written final JSONL row, and rejection of
unrelated sessions and leaf symlinks. A dropped two-message Claude work burst
must retain the other 13 verified message cases and all three work scores.
Managed summaries and ZIPs are collected on success and failure; repeated
collection preserves earlier archives. See [OPERATIONS.md](OPERATIONS.md).

The confirmed full live 0.4.1 pass and earlier baseline/fixture failures are
documented separately in [LIVE_RESULTS.md](LIVE_RESULTS.md).

## Native tmux regression checks

`tests/test_tmux.py` runs the production tmux methods against real tmux on a
private socket with `/dev/null` as its configuration. It launches only sleeping
Python processes, exercises owned session/window setup, selection, capture, and
cleanup, and checks that a similarly named unrelated session survives. It never
uses the default tmux server or launches vendor harnesses.

These two tests are automatically included in `scripts/test.sh`. They explicitly
skip when tmux is absent or the host forbids private Unix sockets. An additional
always-on command-contract test covers the 0.4.0 target-parser failure: option
commands need an explicit session component, while session commands accept the
bare exact-session target. The older process lifecycle fake did not cover this
native parsing boundary.

To run only the real-tmux checks on an installation that permits local sockets:

```bash
python3 -m unittest discover -s tests -p test_tmux.py -v
```
