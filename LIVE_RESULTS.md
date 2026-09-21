# Confirmed live proof

[← Project overview](README.md) · [Documentation map](README.md#documentation)

**Status: CONFIRMED — cross-harness coordination within persistent native
sessions is proved.** On 2026-09-20 UTC, version 0.4.1 completed the full live
suite: all 15 message checks, all nine work batches, and the native tool audit
passed. The PoC has achieved its mechanical objective.

<details>
<summary>On this page</summary>

- [Confirming run — version 0.4.1](#confirming-run--version-041)
- [Earlier live evidence — version 0.3.2](#earlier-live-evidence--version-032)
- [Evidence basis and publication boundary](#evidence-basis-and-publication-boundary)
- [Lessons available for integration](#lessons-available-for-integration)

</details>

## Confirming run — version 0.4.1

Tested source: [a944f31](https://github.com/xormania/multi-harness-proof/commit/a944f31b7527807d000da985c546e8b5bc9ff476).
The operator ran the installed native harnesses and supplied the complete
managed diagnostic bundle for review. Elapsed proof time was **275.2 seconds
(approximately 4 minutes 35 seconds)**.

| Check | Observed result |
| --- | --- |
| Every ordered pair exchanges a challenge and verified reply | **6/6 passed** |
| Submission while a native proof-tool hold is outstanding, followed by a verified reply | **3/3 passed** |
| Exchanges during unfinished fixture work | **6/6 passed**; every reply preceded the recipient's work completion |
| Work accuracy | **9/9 batches correct**, three per agent |
| Native tool audit | **69/69 matched calls**; no unmatched calls, violations, or conflicting call IDs |
| Native session identity | No observed drift |
| Overall report | **`mode: live`, `status: pass`**, zero planned cases left unrun |

The review recomputed all 15 case evidence chains from the event stream using
the tested verifier, checked all nine answers against the copied fixture, and
recomputed the native tool audit. All 18 recorded source hashes and the copied
fixture hash matched the reviewed 0.4.1 source. The bundle contained 3,472 ordered
events; the report identifies its verdict snapshot before shutdown events.

Each verified exchange includes the fresh challenge value, the recipient's
retained private marker, the reply, the sender's receipt report, and matching
native tool/session observations. Receipt or queueing alone did not earn a pass.
Codex also correctly included synthetic job `b2-job-04`, which it had omitted
in both earlier fixture runs.

### Tested harnesses and observed settings

| Harness | CLI version | Requested settings | Native observations |
| --- | --- | --- | --- |
| Codex | 0.155.1 | `gpt-5.6-luna`, `low` | `gpt-5.6-luna`, `low` |
| Claude Code | 2.1.278 | `sonnet`, `low` | Transcript model: `claude-sonnet-5`; effective effort not independently established |
| Grok Build | 1.0.34 (`3736acbc8658`, stable) | `grok-4.5`, `low` | Session settings and prompt-response metadata: `grok-4.6`, `xhigh` |

The requested Grok model/effort differed from the native observations. These
settings are recorded as experimental conditions. The acceptance criterion was
cross-harness coordination; the difference does not alter the confirmed
mechanical result. This run does not establish a model-quality comparison or
attribute the improved answers to any particular configuration change.

### Managed operation confirmed

The managed tmux launch completed with controller exit code 0. Cleanup records
show all three owned harness windows closed. The summary and diagnostic ZIP were
collected automatically, and Claude's exact-session conversation snapshot was
captured with 232 records and no partial final record. This supplies live
operational evidence for the 0.4.1 launcher and capture path in addition to the
mocked lifecycle tests.

### Scope of confirmation

The confirmed capability is message exchange and context retention across newly
launched persistent Codex, Claude Code, and Grok Build sessions, including
submission during a proof-tool hold and coordination during unfinished work.
Busy replies followed hold release; the proof does not claim interruption during
token generation or arbitrary tools. It measures cooperative agents on the
recorded installation, not an OS security boundary or a guarantee across all
future harness versions.

## Earlier live evidence — version 0.3.2

The following operator-supplied results are from 2026-09-19 UTC, using
[source commit 4af8216](https://github.com/xormania/multi-harness-proof/commit/4af8216a8230e07a30cdc7d40b3bc60812c4eb59).
They preserve the earlier successes and failure modes that informed the proof.

### Earlier installation

| Harness | CLI version | Requested model | Requested reasoning |
| --- | --- | --- | --- |
| Codex | 0.155.1 | `gpt-5.6-luna` | `low` |
| Claude Code | 2.1.278 | `haiku` | Not overridden |
| Grok Build | 1.0.34 (`3736acbc8658`, stable) | Harness default | `low` |

The additional Claude transcript identifies `claude-haiku-4-5-20251001`.
Grok's effective model was not established by those reports. These earlier
runs used different requested settings from the confirming 0.4.1 run above.

### Earlier results

| Run | Messaging evidence | Work accuracy | Overall verdict |
| --- | --- | --- | --- |
| Messaging baseline | **9/9 passed:** six ordered round trips and three busy checks | Not selected | **Pass** |
| Fixture A | **15/15 passed**, including all six exchanges during unfinished work | Claude and Grok: 3/3 batches; Codex: 2/3 | Unverified |
| Fixture B, unchanged repeat | Baseline 9/9; four of six work exchanges have complete evidence; two Codex-to-Claude challenges went unanswered | Same Codex error; Claude and Grok: 3/3 | Unverified |

The native tool audits passed: 33/33 matched calls for the baseline, 69/69 for
fixture A, and 65/65 for fixture B, with no unmatched relay calls, violations,
or conflicting call IDs. A clean audit covers calls that occurred; it cannot
replace a missing response.

The baseline took approximately 2 minutes 15 seconds. This is an observation,
not a runtime guarantee. Permission waits, model latency, and failed checks
can make another run substantially longer.

### What the earlier passing checks established

Messages entered persistent native sessions across all three harnesses.
Verified exchanges included the recipient's retained private marker, the fresh
challenge value, a reply back to the sender, and independently observed native
tool calls with matching session identities. Busy checks overlapped an
outstanding `proof_hold` call. Fixture A also completed all six exchanges before
their respective recipients finished the fixture.

This establishes the tested mechanics on that installation. It does not prove
interruption during token generation, arbitrary tool interruption, compatibility
with every harness release, or reliable model behavior across repeated runs.

### Work accuracy failure

Codex omitted synthetic job `b2-job-04` in the second batch in both fixture runs,
reporting 9 failed tests instead of 10. That job's later attempt is `FAIL` with
one failed test; an older `PASS` appears later in the input order. The grader
uses the greatest attempt number, not the last row encountered.

This is a task-answer error alongside successful transport evidence. More capable
models or different reasoning settings are useful experimental variables, but
these observations do not establish which change would correct it.

### Message handling failure under work

Fixture B's diagnostic bundle shows the two missing challenges submitted to
Claude. Its separately supplied native conversation log goes further: both
messages were queued, marked `absorbed_mid_turn`, and attached to the active
conversation after a tool result and before the next assistant response. No
matching replies followed. A later peer reply was absorbed the same way and
was handled successfully.

The evidence therefore distinguishes delivery into the conversation from the
agent acting on it. It shows ingestion between tool calls, not interruption
during token generation. Task-ending instructions, channel presentation, and
model behavior are possible contributors; the available evidence does not
isolate a cause or expose the model's private reasoning.

Version 0.3.2's report stopped evaluating the work burst after its first missing
exchange, leaving all six work cases marked unverified. Rechecking the preserved
events with the unchanged case verifier establishes four complete work exchanges,
or **13/15 qualifying message chains overall**. This does not change the original
overall verdict. Version 0.4.0 evaluates every already-launched work exchange
independently, preserving those partial successes while still failing closed.

## Evidence basis and publication boundary

- Confirming 0.4.1 run: supplied full managed diagnostic bundle, including report,
  event stream, native/MCP traces, lifecycle records, fixture, and captured Claude
  conversation. Case chains, scores, audit, and source hashes were rechecked.
- Earlier baseline: supplied final report.
- Fixture A: supplied final report and all nine work-scoring events.
- Fixture B: supplied final report, full diagnostic bundle, and the native
  Claude conversation log; the bundle's source and fixture hashes matched 0.3.2.

This page publishes aggregate findings and synthetic fixture identifiers only.
Raw artifacts, machine paths, session IDs, credentials, and private markers are
not committed. These are reviewed operator observations, not an independent
rerun by the maintainer's test environment.

## Lessons available for integration

- Persistent native sessions can participate in one common coordination protocol;
  vendor-specific ingest and tool interfaces can be isolated in adapters.
- Track submission, agent response, and verified completion separately. Earlier
  failures show that conversation ingestion does not guarantee action.
- Preserve work accuracy and communication outcomes as separate observations.
  A wrong task answer can coexist with successful coordination.
- Correlated native and relay telemetry makes missing responses and session/tool
  evidence gaps diagnosable. Terminal output alone was insufficient.
- Managed session lifecycle and automatic evidence collection make the experiment
  practical to operate and repeat without manual run naming or routine cleanup.

The proof is confirmed. This repository remains a reference implementation and
compatibility regression project for systems adopting the mechanism. Further
experiments can extend coverage and measure reliability without reopening the
completed mechanical objective.
