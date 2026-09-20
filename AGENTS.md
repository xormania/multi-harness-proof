# Scope and boundaries

This is a small mechanical proof of messages between persistent
Codex, Claude Code, and Grok Build sessions, with observable round trips and
context retention. Keep changes focused on proving those mechanics.

- Work in this repository and generated run directories. Never modify user
  global harness config, authentication, shell profiles, global MCP registries,
  or unrelated repositories as part of setup or troubleshooting.
- Use invocation arguments, native session parameters, or generated run-local
  files for adapter configuration. Never replace a user's existing config.
- Do not install, upgrade, log in, switch accounts, spend model usage, or start
  a live harness test without the user's authorization. Offline tests are fine.
- Do not substitute a model API for a harness or spawn a new session per message.
- Do not weaken permissions to get a test to pass. Diagnose unsupported
  capabilities explicitly. Never silently enable a permissions-bypass mode.
- Keep transport submission, model response, and verified round trip distinct.
  Never report a live pass from mocked processes or a queued-message receipt.
- Preserve native session identity, unique nonces, and private-memory checks.
  Preserve failure evidence; do not replay ambiguously delivered messages.
- Run `bash scripts/test.sh` after protocol/concurrency changes. The fixtures
  test our plumbing, not vendor compatibility. Document live testing separately.
- Keep run tokens, transcripts, generated configs, local paths, and credentials
  out of commits and source distribution ZIPs. Local diagnostic bundles may
  contain redacted test transcripts and paths; never upload them automatically.
  Read only the exact native transcript identified by this run's hook. Never
  modify native history or collect unrelated sessions. Use fresh run directories.
- Do not expand this into benchmarking, role orchestration, or a capability
  registry without a separate request.

Repository-specific session setup lives in `mhproof/adapters.py`. MCP tool and
prompt definitions live in `mhproof/contract.py`. There is deliberately no
static `.codex`, `.grok`, or `.claude` project config for a coding agent to inherit.
