# Validation at initial delivery

Date: 2026-09-19. Runtime: Python 3.12, Linux.

## Verified locally

- 11 standard-library unit/integration tests pass.
- The integration test runs the actual relay, adapters, MCP server, and verifier
  against deterministic **fake** Codex, Claude, and Grok processes. It checks
  all 15 message cases, three work scores, and persistent session identities.
- A Claude MCP channel notification gets through while a proof tool is blocked.
- Missing messages, stale nonces, wrong context values, changed sessions, and
  missing busy overlap cannot produce a verified pass.
- Work grading ignores superseded job attempts and warnings, and rejects
  duplicate submissions without returning answers to the agent.
- Credential redaction and diagnostic-bundle exclusions are checked.
- RPC errors/EOF are errors, not successful responses.
- CLI smoke checks cover startup timeout producing an unverified report,
  diagnostic bundling without run credentials, and refusal to overwrite a run.
- Bash launch scripts pass syntax checks.

## Still requires a local live run

Codex, Claude Code, and Grok Build executables were all absent here. No real
model calls were made. Authentication, account policies, preview-channel access,
exact installed CLI compatibility, model/effort selection, and actual agent
behavior remain unverified. The tmux helper was syntax-checked, not interactively
exercised in a real user's terminal.

The first live run should produce its own `report.json`, `events.jsonl`, native
traces, and a diagnostic ZIP if needed. Preserve that evidence before changing
an adapter. Do not treat this file or fixture-test output as live proof.
