# Observed live results

**Basic cross-harness coordination has been demonstrated. Reliable handling
during work has not.** These are operator-supplied results from 2026-09-19 UTC,
using version 0.3.2 ([source commit](https://github.com/xormania/multi-harness-proof/commit/4af8216a8230e07a30cdc7d40b3bc60812c4eb59)).
They are separate from the repository's mocked tests.

## Tested installation

| Harness | CLI version | Requested model | Requested reasoning |
| --- | --- | --- | --- |
| Codex | 0.155.1 | `gpt-5.6-luna` | `low` |
| Claude Code | 2.1.278 | `haiku` | Not overridden |
| Grok Build | 1.0.34 (`3736acbc8658`, stable) | Harness default | `low` |

The additional Claude transcript identifies `claude-haiku-4-5-20251001`.
Grok's effective model was not established by these reports. **Grok 4.5 and
Sonnet are new defaults for future managed runs; these results do not test them.**

## Results

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

### What the passing checks establish

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

- Baseline: supplied final report.
- Fixture A: supplied final report and all nine work-scoring events.
- Fixture B: supplied final report, full diagnostic bundle, and the native
  Claude conversation log; the bundle's source and fixture hashes matched 0.3.2.

This page publishes aggregate findings and synthetic fixture identifiers only.
Raw artifacts, machine paths, session IDs, credentials, and private markers are
not committed. These are reviewed operator observations, not an independent
rerun by the maintainer's test environment.

Next validation: repeat the unchanged protocol with the managed launcher's
Luna/Sonnet/Grok 4.5 low-reasoning settings, retaining every run and comparing
message handling separately from work accuracy. There is still no observed
overall pass of the full live fixture suite.
