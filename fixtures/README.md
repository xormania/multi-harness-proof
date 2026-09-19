# Build-log triage fixture

Each batch contains synthetic CI job outcomes. A job can have multiple attempts,
and rows are deliberately out of order. Only the highest numbered attempt for
each job counts. Report the IDs of jobs whose latest attempt is `FAIL`, and the
sum of `failed_tests` for those jobs. `WARN` and `PASS` are not failures.

Three batches per agent, with two outbound challenges after its first batch.
Incoming messages must be handled while the agent continues the assigned work.
No shell, filesystem editing, package installation, or access to a real project
is needed. This exercises context, tool use, interleaving, and answer accuracy;
it is not a coding benchmark or a load test of a model provider.

The controller scores answers without returning the answer key to the agent.
Telemetry records both the submitted and expected answers for later diagnosis.
