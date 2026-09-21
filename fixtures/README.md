# Build-log triage fixture

[← Project overview](../README.md) · [Testing guide](../TESTING.md)

Each batch contains synthetic CI job outcomes. Jobs can have multiple attempts,
and rows are deliberately out of order.

## Scoring rules

1. Select the **highest numbered attempt** for each job.
2. Report the IDs whose selected attempt is **`FAIL`**.
3. Sum `failed_tests` for those selected failures. **`WARN` and `PASS` do not count.**

## Coordination during work

Each agent receives the same three batches and is scored independently. The
controller asks each agent to finish one batch and end its turn. After every
agent submits batch 1, each sends two challenges. Later batches remain locked
until all six challenge submissions are observed. This removes the race where
a fast recipient finishes before a slower sender starts its burst.

The test proves message submission while a fixture is unfinished, with correct
round trips and eventual work completion. It does not claim simultaneous model
computation. Reply-before-completion is reported separately; native channels may
process queued messages in a later turn within the same session.
No shell, filesystem editing, package installation, or access to a real project
is needed. This exercises context, tool use, interleaving, and answer accuracy;
it is not a coding benchmark or a load test of a model provider.

The controller scores answers without returning the answer key to the agent.
Telemetry records both the submitted and expected answers for later diagnosis.
The overall verdict requires a native tool audit. Reading telemetry or fixture
files with non-proof tools invalidates the cooperative run; this is not OS isolation.
