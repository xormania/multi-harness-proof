# Validation status

Date: 2026-09-19. Runtime: Python 3.12, Linux.

## Verified locally

- 24 standard-library unit/integration tests pass for version 0.2.
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

## Still requires a local live run

Codex, Claude Code, and Grok Build executables were all absent here. No real
model calls were made. Authentication, account policies, preview-channel access,
exact installed CLI compatibility, model/effort selection, and actual agent
behavior remain unverified. The tmux helper was syntax-checked, not interactively
exercised in a real user's terminal.

In particular, confirm that Claude loads the channel from the invocation's MCP
configuration and executes the run-local hooks, and that the installed Grok
build emits the tool and completion notifications used by the adapter. Startup
requires a channel-delivered memory marker and matching native tool observation;
MCP readiness alone is not a pass. Grok permission-rule flags remain unapplied;
the adapter retains serialized human approval. The MCP server implements only
protocol version 2025-06-18; clients must accept that negotiated version.

The first live run should produce its own `report.json`, `events.jsonl`, native
traces, and a diagnostic ZIP if needed. Preserve that evidence before changing
an adapter. Do not treat this file or fixture-test output as live proof.
