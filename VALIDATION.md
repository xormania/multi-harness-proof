# Validation status

[← Project overview](README.md) · [Documentation map](README.md#documentation)

Updated: 2026-09-20. Offline runtime: Python 3.12, Linux.

**PoC status: CONFIRMED.** The full live 0.4.1 proof passed on 2026-09-20 UTC.

<details>
<summary>On this page</summary>

- [Confirmed live result](#confirmed-live-result)
- [Version 0.4.1 tmux startup correction](#version-041-tmux-startup-correction)
- [Verified locally](#verified-locally)
- [Behavior and experiment coverage](#behavior-and-experiment-coverage)
- [Reported live startup failure and scoped fix](#reported-live-startup-failure-and-scoped-fix)
- [Validation boundaries and continued use](#validation-boundaries-and-continued-use)

</details>

## Confirmed live result

The reviewed operator-supplied diagnostic bundle records:

- **15/15 message checks passed:** six ordered round trips, three busy checks,
  and six exchanges during unfinished work. Every work reply arrived before
  its recipient completed the fixture.
- **9/9 work batches correct**, with all three agents passing their work scores.
- **69/69 native tool calls matched**, zero unmatched calls, violations,
  conflicting call IDs, or observed native session drift.
- **275.2 seconds** elapsed; report status `pass`, controller exit code 0,
  all owned harness windows closed, and diagnostics collected automatically.
- Claude's exact-session transcript captured successfully with 232 records.

Review re-evaluated every case chain, work answer, and the native tool audit from
the saved evidence. All 18 source hashes and the copied fixture hash matched
0.4.1. See [LIVE_RESULTS.md](LIVE_RESULTS.md) for tested CLI versions, requested
and observed model settings, earlier failure evidence, and the measured scope.
Grok's observed model/effort differed from the request; that is a recorded run
condition, not a failure of the coordination objective.

This live run confirms the mechanical objective. Offline tests continue to
exercise failures and verification behavior independently.

## Version 0.4.1 tmux startup correction

An operator's 0.4.0 launch stopped with `no such session: =mhproof-...` before the
controller or agent sessions launched. The diagnostic archive was retained. Source
inspection confirms that option commands use `CMD_FIND_PANE`; without a colon,
`=session` is treated as a pane expression. Both owner-option calls now use
`=session:`. Session-only commands retain their existing exact-name targets.
See tmux's [target parser](https://github.com/tmux/tmux/blob/3.4/cmd-find.c) and
[set-option command definition](https://github.com/tmux/tmux/blob/3.4/cmd-set-option.c).

**Verification:** 98 tests collected in 193.984 seconds: **96 passed, two
explicitly skipped**. The command-contract regression fails against the 0.4.0
implementation with the reported error and passes after the correction.

Two real-tmux integration checks cover ownership and unrelated-session preservation on a private socket.
For this verification, tmux 3.3a was available in an isolated temporary directory,
but the host denied Unix-domain sockets with `EPERM`; both native checks explicitly
skip. Those skips remain part of the offline record. The subsequent full live
0.4.1 run above confirms that the corrected managed launch completed on the
operator's installation, including cleanup and automatic collection.

## Verified locally

- Version 0.4.0: **95 offline tests passed** in 194.538 seconds, including 24
  process-level behavior scenarios, configurable experiments, updater checks,
  and 14 new managed-lifecycle/transcript tests. No real model calls were made.
- Managed completion, stop, partial launch failure, controller crash, missing
  tmux, and preflight failures retain summaries and diagnostic archives.
  Process tests use the real controller/adapters against fake harnesses and a
  simulated tmux lifecycle; they do not certify real terminal behavior.
- Exact-session transcript snapshots redact known credentials and leave native
  sources untouched. Tests reject unrelated identity and leaf symlinks, record
  partial writes, and check archive exclusions. Saved-plan reruns preserve old
  settings and fixture files. Cleanup refuses a foreign tmux ownership token.
- A mocked two-message Claude work drop preserves 13 verified message cases and
  all work scores. Every already-launched burst exchange is evaluated with the
  original case predicate, including during failure finalization.
- The integration test runs the actual relay, adapters, MCP server, and verifier
  against deterministic **fake** Codex, Claude, and Grok processes. It checks
  all 15 message cases, three work scores, and independently supplied native
  session/tool evidence. Simulated Claude uses later turns and actual generated
  hook commands; simulated Grok emits completion for fallback turns with no
  matching prompt RPC. Harness speeds deliberately differ.
- A Claude MCP channel notification gets through while a proof tool is blocked.
- Missing messages, stale nonces, wrong context values, changed sessions, and
  missing busy overlap cannot produce a verified pass.
- Correct relay registration labels without native tool observations cannot pass.
  Repeated observations of one native call do not cover multiple relay calls.
- Grok retains pending interjections across completion of the previous turn.
  RPC responses alone cannot prove idle, duplicate completions cannot clear a
  later active turn, and timeouts record unknown activity. The prompt deadline
  exceeds the work-batch wait.
- Concurrent Grok permission requests are serialized.
- Native Codex command execution invalidates cooperative evidence. Claude's
  run-local hook records the actual session and rejects non-proof tools.
- Installed Codex schema probing is exercised through the simulated CLI; a
  missing toolOutput capability prevents session startup.
- Work grading ignores superseded job attempts and warnings, and rejects
  duplicate submissions without returning answers to the agent.
- Later work batches cannot be fetched before controller release; all burst
  submissions precede release of the remaining batches in the integration test.
- Credential redaction and diagnostic-bundle exclusions are checked.
- RPC errors/EOF and non-JSON protocol stdout are explicit failures.
- CLI smoke checks cover startup timeout producing an unverified report,
  diagnostic bundling without run credentials, and refusal to overwrite a run.
- Bash launch scripts pass syntax checks.

## Behavior and experiment coverage

- The 24 mocked process scenarios cover successful delayed/duplicate delivery,
  display-title changes, dropped handling, wrong context, stale nonces, missing
  native observations/hooks/completion, changed sessions, unsupported methods,
  denied permissions, process crashes, malformed stdout, incorrect work, reused
  call IDs, forbidden tools, and operator interruption.
- Claude startup scenarios reproduce a connected MCP server whose channel
  handler registers later. The fake drops notifications sent before registration,
  so the new gate must precede delivery. Unknown registration text prevents
  submission; a registered handler that drops the notification still cannot
  satisfy readiness. Native registration logs are not delivery evidence.
- The explicit harness updater passes fake-only tests for version capture,
  dry runs, partial download failure, independent updater failures, and unknown
  install methods. No real harnesses or package managers were updated here.
- Grok discovery coverage uses the pinned source's `search_tool` query/limit
  schema and `use_tool` tool_name/tool_input wrapper. A mocked catalog search
  invokes the actual MCP `tools/list`; every subsequent proof execution is
  wrapped. Discovery alone cannot cover a missing native ready call, and an
  unrelated MCP target invalidates the run. Unit checks reject malformed
  wrappers, unknown metadata versions, contradictory identities, and call-ID
  reuse across discovery and proof execution. Original wire inputs are retained.
- The runner asserts specific failure evidence, preserves earlier passes, and
  marks expected failures as matched tests with **unverified proof reports**.
  Reports, process output, exit codes, fault records, and native/MCP traces remain
  available when using the artifact-preserving behavior command.
- Grok canonical `x.ai/tool` version-1 identity is checked against pinned source.
  Initial tool titles at that source are wire function names; versioned metadata
  avoids dependence on later display text. Unknown or conflicting identity fails
  closed. This source review does not establish installed-build compatibility.
- Configuration checks cover strict fields, duplicate JSON keys, invalid fault
  rules, model/profile resolution, selected pairs, burst sizes, copied fixture
  validation, and two-peer live plan resolution without launching native CLIs.
- Configured process tests run changed coordination plans through real adapters,
  preserve both runs, and compare their settings and outcomes. The first snapshot
  remains unchanged after editing and rerunning the configuration.
- The supplied mock example was executed: baseline and delayed Claude handling
  passed with four-message work bursts; dropping Grok's second work message left
  the proof unverified as expected. This is simulated behavior evidence only.

Reproduce with `bash scripts/test.sh`, `bash scripts/behavior.sh --dir runs/mock1`,
or `bash scripts/experiment.sh proof.mock.example.json`. Details and boundaries
are in [TESTING.md](TESTING.md) and [EXPERIMENTS.md](EXPERIMENTS.md).

## Reported live startup failure and scoped fix

A user-supplied v0.3.0 report from 2026-09-19 records Grok 1.0.30 calling native
`search_tool` during readiness. The adapter rejected its identity as outside
the proof MCP namespace, and the controller stopped before any of nine selected
message cases ran. Subsequent Claude hook connection errors followed relay
shutdown. Native session registration and the Codex toolOutput probe had succeeded;
this run establishes neither working nor broken cross-harness messaging.

Version 0.3.1 recognizes Grok catalog discovery separately and normalizes only
exact proof MCP targets from `use_tool`. The discovery and dispatch schemas were
reviewed at public source commit `482711333c7195dc16a272777f86086d615e2afb`.
The installed binary's `04b7ffed98c6` revision could not be resolved in the public
repository, so that source review is not a verification of the exact installed
build. A subsequent user-supplied v0.3.1 diagnostic bundle does contain the live
discovery and wrapped `use_tool` call: Grok produced a correct ready report with
matching native evidence. Codex also produced a correct verified ready report.
These are startup results, not cross-harness round trips.

The v0.3.1 bundle identifies a separate Claude Code 2.1.278 startup race: our
notification was written about 5 ms before its native debug log reported channel
handler registration. Claude ran only its initial launch turn and produced no
ready report. This supports early notification loss as the cause; none of the
nine selected message cases ran. Run paths, session identifiers, prompts, and
private markers from the supplied bundle are not included in this repository.

Version 0.3.2 waits for that handler-registration marker in the fresh run-local
debug log before queuing startup instructions. The native log format is an
explicit compatibility dependency, not a documented readiness acknowledgment.
Missing/changed output leaves the run unverified with per-peer startup diagnostics;
actual receipt still requires the same memory marker and native tool evidence.
The subsequent 0.3.2 live baseline passed all nine messaging checks. Two full
fixture runs also passed readiness and the same nine baseline checks. This
supersedes the earlier startup-only validation; see [LIVE_RESULTS.md](LIVE_RESULTS.md).

## Validation boundaries and continued use

The maintainer's offline environment made no real model calls. The confirmed
live result comes from the reviewed operator-supplied 0.4.1 bundle. Earlier
0.3.2 failures remain documented as observed failure modes; they do not change
the later completed proof.

Authentication, account policies, preview-channel access, and exact installed
CLI compatibility remain installation-dependent. Claude's registration log is
still a version-sensitive gate. Grok permission-rule flags remain unapplied;
the adapter retains serialized human approval. The MCP server implements only
protocol version 2025-06-18; clients must accept that negotiated version.

Managed runs preserve their report, events, native traces, transcript capture
status, summary, and ZIP automatically. Future runs can test compatibility and
reliability as harnesses evolve. Their verdicts remain specific to their own
recorded evidence; the confirmed PoC does not make future runs pass automatically.
