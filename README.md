# Multi-Harness Proof

**Three harnesses. Persistent sessions. Verified coordination.**

Codex, Claude Code, and Grok Build exchange messages through a common local relay,
retain earlier context, and continue useful work. Each agent stays in one native
session throughout the experiment. The output is inspectable evidence: native
traces, verified replies, scored work, and diagnostics.

[Quick start](#quick-start) · [Live results](LIVE_RESULTS.md) ·
[Operations](OPERATIONS.md) · [Documentation](#documentation)

> [!NOTE]
> **CONFIRMED — the full live proof passed on 2026-09-20 UTC.**
> Version 0.4.1 completed every selected check, including managed tmux cleanup
> and automatic diagnostic collection. The mechanical objective is complete;
> this repository remains a reference implementation and compatibility
> regression project.

| Message checks | Correct work batches | Matched native tool calls | Elapsed |
| :---: | :---: | :---: | :---: |
| **15 / 15** | **9 / 9** | **69 / 69** | **4m 35s** |

[Read the measured scope, observed settings, and earlier failures →](LIVE_RESULTS.md)

## Quick start

### 1. Check the prerequisites

- Python **3.10+** on Linux, macOS, or WSL; no additional Python packages.
- Installed, authenticated **Codex**, **Claude Code**, and **xAI Grok Build** CLIs,
  available as `codex`, `claude`, and `grok` in the current shell.
- Claude Code access to custom development channels.
- **tmux** for the managed launcher. [Separate terminals](OPERATIONS.md#separate-terminals-and-the-original-launcher) also work.

The launcher uses existing installations and authentication. It does not install
or upgrade harnesses, edit global configuration, or enable permission bypasses.

### 2. Prepare the checkout

```bash
git clone https://github.com/xormania/multi-harness-proof.git
cd multi-harness-proof
python3 proof.py doctor
bash scripts/test.sh
```

Already have a clone? Run `git pull --ff-only` inside it and keep previous run
directories. `doctor` checks executable versions and the installed Codex schema
for `TurnStartParams.toolOutput`. The tests exercise mocks; neither command
makes model calls. Native account policies, hooks, and approvals are checked
during the live run.

### 3. Start a run

```bash
# Full suite: messaging plus the work fixture.
bash scripts/proof.sh start
```

For a first installation, use `bash scripts/proof.sh start --no-work` instead
to run the nine-check messaging baseline. Each launch creates a fresh directory,
saves settings and fixture input, and opens a dedicated tmux session with a
dashboard and one window per harness.

The managed profile requests **Luna, Sonnet, and Grok 4.5 with low reasoning**.
Requested and effective settings can differ; the
[live results](LIVE_RESULTS.md#tested-harnesses-and-observed-settings) record both.

> [!IMPORTANT]
> Accept Claude's development-channel prompt and respond to Grok's proof-tool
> permission requests. These native approvals still need your attention.

| In tmux | Action |
| --- | --- |
| **Ctrl+B**, release, then **W** | Choose the dashboard or a harness window |
| **Ctrl+B**, release, then **D** | Detach while the run continues |

### 4. Read the result

When the run finishes, the launcher closes its harness windows and leaves the
dashboard showing the verdict, work scores, and diagnostic ZIP location.

| Saved in the managed run directory | Purpose |
| --- | --- |
| `summary.txt`, `summary.json` | Readable and structured outcomes |
| `run/report.json`, `run/events.jsonl` | Verdict and ordered evidence |
| `archives/diagnostics-<timestamp>.zip` | Collected diagnostics for review |

Nothing is uploaded automatically. Bundles exclude run credentials and redact
known secrets, but retain local paths and conversation content. Review them
before sharing publicly.

## Manage and repeat runs

Replace `RUN_DIRECTORY` with the path printed by the launcher or `list`.

```bash
bash scripts/proof.sh list
bash scripts/proof.sh status RUN_DIRECTORY
bash scripts/proof.sh stop RUN_DIRECTORY
bash scripts/proof.sh close RUN_DIRECTORY

# Repeat the saved plan and fixture in a NEW run.
bash scripts/proof.sh start --from RUN_DIRECTORY
```

`stop` finalizes an active run and collects diagnostics. `close` removes a
finished dashboard and keeps its files. Names and archives are generated
automatically; no routine manual tmux cleanup is needed.

See the [operations guide](OPERATIONS.md) for model overrides, live plans,
harness updates, transcript capture, recollection, and recovery.

## What gets tested

Each participant receives a distinct context marker. Later replies must contain
that remembered marker and a fresh challenge value. A passing exchange requires
the complete reply chain and independently observed native tool calls.

| Check | Required evidence | Default count |
| --- | --- | ---: |
| **Round trips** | Every ordered pair exchanges a challenge and verified reply in its original sessions. | 6 |
| **Delivery while busy** | Submission overlaps an outstanding proof-tool call, followed by a verified reply. | 3 |
| **Messages during work** | Two challenges per agent reach recipients with unfinished work; each receives a verified reply. | 6 |
| **Work accuracy** | Each agent identifies the latest failed job attempts across three build-log batches. | 9 batches across 3 agents |

The [fixture](fixtures/README.md) contains 72 synthetic log records per agent,
including retries and out-of-order entries. All agents receive the same batches
and are scored independently. The relay routes messages; the agents invoke the
tools and solve the task.

## How coordination works

A Python relay listens on loopback. Adapters deliver envelopes through native
harness interfaces and record evidence independently of the model's replies.

| Harness | Persistent session | Native incoming path |
| --- | --- | --- |
| **Codex** | App-server, one thread | `turn/start.toolOutput` for peer messages |
| **Claude Code** | Native terminal UI, explicit session ID | MCP `notifications/claude/channel` |
| **Grok Build** | ACP over stdio, one session | `session/prompt` when idle; `_x.ai/interject` when active |

Queueing, native submission, and agent handling are recorded separately. Session
IDs, call IDs, exact tool arguments, nonces, and retained context support the
verdict. Missing evidence leaves a check unverified.

The proof launches new persistent sessions. Busy checks measure an outstanding
proof-tool call; work checks measure an unfinished fixture. They do not establish
token-generation interruption, simultaneous model computation, or adversarial
process isolation. Interfaces remain version-sensitive.

[Explore native interfaces, verification rules, and telemetry →](REFERENCE.md)

## Explore failures offline

```bash
# Preserve all 24 predefined behavior scenarios and their telemetry.
bash scripts/behavior.sh --dir runs/mock1

# Repeat configurable scenarios in new timestamped directories.
bash scripts/experiment.sh proof.mock.example.json
```

These use fake vendor processes with the real adapters and verifier. No installed
vendor CLIs or model calls are needed. A deliberately dropped message must leave
the proof unverified to meet that scenario's expected outcome; a matched mock
expectation is separate from a live pass.

## Documentation

| I want to… | Read |
| --- | --- |
| **See what was proved** | [Live results](LIVE_RESULTS.md) — confirmed run, observed settings, earlier failures |
| **Run and manage the proof** | [Operations](OPERATIONS.md) — tmux, profiles, updates, archives, recovery |
| **Understand the mechanics** | [Protocol and evidence reference](REFERENCE.md) — native paths, audit, telemetry, troubleshooting |
| **Configure a scenario** | [Experiments](EXPERIMENTS.md) — plans, fault rules, preserved history, comparisons |
| **Exercise failure behavior** | [Testing](TESTING.md) — process mocks, lifecycle checks, native tmux regressions |
| **Review validation coverage** | [Validation](VALIDATION.md) — live evidence, offline checks, explicit skips |
| **Understand the work task** | [Fixture guide](fixtures/README.md) — latest-attempt scoring and work gates |

## Contributing

Native compatibility, diagnostics, and reproducibility improvements are welcome.
Read [AGENTS.md](AGENTS.md), keep changes focused on the proof, and run
`bash scripts/test.sh` after protocol changes. Identify live results separately
from simulated tests.

For a reproducible [issue](https://github.com/xormania/multi-harness-proof/issues),
include CLI versions, requested model/reasoning settings, the failing case, and
a reviewed diagnostic excerpt. Preserve the failed run before changing adapters.

The main modules are [adapters](mhproof/adapters.py), [relay](mhproof/relay.py),
[MCP server](mhproof/mcp.py), [verifier](mhproof/suite.py), and
[telemetry](mhproof/telemetry.py). GitHub is the maintained source; no checked-in
source ZIP is distributed.

## License

[MIT](LICENSE) · Maintained by [xormania](https://github.com/xormania).
