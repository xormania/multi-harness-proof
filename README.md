# Multi-harness coordination proof

A small standalone experiment for Agentscient. It tests whether **Codex, Claude
Code, and Grok Build can exchange messages inside persistent native sessions**,
reply without a human copying messages, and remember earlier conversation state.

**Status: implemented; offline protocol tests included. Live compatibility and
model behavior still need a run on your machine.** None of the three real CLIs
was available in the build environment. A simulated pass is not a live pass.

## Quick start

Requires **Python 3.10+**, Linux/macOS/WSL, and installed, authenticated `codex`,
`claude`, and **xAI Grok Build's** `grok`. Python has no extra dependencies.
This project does not install or update harnesses or change your login.

From the extracted ZIP or cloned repository:

```bash
python3 proof.py doctor
bash scripts/test.sh                 # offline; no model calls
bash scripts/start.sh runs/first     # starts relay, prints three commands
```

Leave that terminal running. In three more terminals, from this repository:

```bash
bash scripts/agent.sh codex runs/first
bash scripts/agent.sh claude runs/first
bash scripts/agent.sh grok runs/first
```

Each command goes in its **own terminal**. The relay also prints absolute-path
commands you can use from any directory. Use a **new run directory** each time.
The test starts automatically when all selected peers and their tools are ready.

Claude opens its normal TUI. Accept its native development-channel prompt and
workspace trust prompt if shown. Grok permission requests appear in its terminal;
approve the named proof tools once when prompted. You never need to copy agent
messages between terminals. Normal model usage/subscription limits apply.

**Optional, if you already use tmux:**

```bash
bash scripts/tmux.sh
```

This creates one new tmux session with relay, codex, claude, and grok windows,
initially showing Claude so you can accept the channel prompt. Use `Ctrl-b w`
to switch windows. It does not edit tmux's global configuration. Close the
proof's tmux session when finished; existing tmux sessions are untouched.

## What it checks

1. Each harness starts one native session and receives a distinct private memory
   value. It must acknowledge that value through a proof tool.
2. Every ordered pair exchanges a fresh challenge and reply: six round trips
   with three harnesses. The recipient must return its previously supplied memory
   value. The sender must report the reply it actually received.
3. Each recipient then enters a blocking `proof_hold` tool. Another agent sends
   a challenge while that tool is outstanding. The controller releases the hold
   after native delivery is submitted, then requires the same verified reply.
4. All agents triage three small batches of synthetic build logs concurrently.
   They must account for retried jobs and report the latest failures correctly.
   Each sends a two-message burst after its first batch, handles incoming
   messages, and completes the remaining work. The controller scores the work
   and checks whether each message submission overlapped the recipient's work.

That's **15 message checks and three work scores**, plus onboarding, for three peers. The code
routes messages; the actual models must invoke the send/report tools correctly.
The private value is not reinserted into later challenges. The harness session
stays the same throughout the run.

The work fixture is 72 short records per agent, split into three batches. Its
inputs and rules are in `fixtures/`. It creates a little pressure on context and
tool/message handling without running code or touching a real project. A work
pass requires correct answers and observed message/work overlap; if a recipient
finishes too quickly for overlap, that is reported as unverified, not a pass.
Use `--no-work` on `run` to isolate the nine baseline message checks.

A busy pass means **submission overlapped a real outstanding proof tool call,
followed by a correct model reply**. It does not mean a model was interrupted
mid-token, or that every arbitrary tool can be interrupted. Claude channels do
not expose native idle-state observations here; round trips start after completed
proof operations and a brief quiet interval. Codex and Grok expose turn boundaries.

## Native interfaces

| Harness | Persistent session | Incoming peer messages | Outgoing tools |
| --- | --- | --- | --- |
| Codex | `codex app-server --listen stdio://`, one thread | `turn/start.toolOutput`; idle start or active-turn queue | Session-scoped dynamic tools |
| Claude Code | Native TUI, explicit `--session-id` | MCP `notifications/claude/channel` | Stdio MCP tools |
| Grok Build | `grok --no-auto-update agent stdio`, one ACP session | `session/prompt` when idle; `_x.ai/interject` when active | Stdio MCP tools |

These are **new sessions launched by this proof**, kept alive across messages.
Codex and Grok terminals show adapter output, not their native TUIs. Attaching to
unrelated already-running TUI sessions is outside this experiment. MCP by itself
is not used as a universal push mechanism: each adapter handles inbound delivery.

The Codex dynamic-tool and tool-output APIs and Grok interjection extension can
vary by CLI build. Claude custom channels are a preview feature. A missing method,
policy block, unconsumed message, or incorrect model response remains unverified
or unsupported. There are no silent API/model substitutions or blind retries.

## Results and stopping

Watch the relay terminal, then inspect:

```bash
cat runs/first/report.json
```

`report.json` contains the overall verdict, case verdicts, evidence event numbers,
native session IDs, reported CLI versions, and round-trip times. `events.jsonl`
records queueing, adapter submissions, tool reports, and hold boundaries.
Codex/Grok stderr goes to their respective run-local log files. Timing measures
this experiment's round trips, not model quality or a stable performance benchmark.

Detailed local telemetry is enabled on every run:

| File | Contents |
| --- | --- |
| `manifest.json` | Python/platform, selected peers, timeouts, SHA-256 of the proof source |
| `events.jsonl` | Ordered routing, tool reports, session/turn events, holds, HTTP errors |
| `codex-trace.jsonl`, `grok-trace.jsonl` | Native requests/responses/notifications, launch argv/cwd/PID, stderr, timeouts, exceptions, exit codes |
| `mcp-claude-trace.jsonl`, `mcp-grok-trace.jsonl` | MCP initialization, tools, tool arguments/results, channel messages |
| `claude-trace.jsonl`, `claude-debug.log` | Claude launch/exit and native debug output |

Traces are flushed after each entry and have wall-clock and monotonic timestamps.
They retain available native usage/model metadata as it appears on the wire;
there is no universal cost or reasoning-setting measurement. Claude TUI text is
not screen-scraped. No telemetry is uploaded automatically, and the proof does
not collect environment values or read global configuration files.

After a run, make one diagnostic ZIP:

```bash
python3 proof.py bundle --dir runs/first --out first-diagnostics.zip
```

The bundle includes only named diagnostic files. It excludes `run.json`, MCP
configuration, and workspaces; it redacts run tokens and known credential values.
Structured protocol traces also redact credential fields when written. Claude's
native debug file is produced by Claude itself; bundling redacts known secrets
but cannot guarantee removal of every possible sensitive string. Inspect a bundle
before sharing it: test prompts, replies, and local paths are intentionally kept.
The bundle command writes a local ZIP only and refuses to overwrite an existing one.

- `pass`: the complete evidence chain matched the nonce, context value, and
  original session IDs. Busy cases also require the observed hold overlap.
- `unverified`: missing/incorrect evidence, timeout, launch error, or interruption.
- `unsupported` on a case: an adapter received a native method-not-found error.

The runner stops at the first incomplete check and reports how many checks were
not run. Earlier passes remain visible. This is a diagnosis aid; an incomplete
run is not a verdict that multi-harness coordination is impossible.

`Ctrl-C` in the relay writes an incomplete report and releases pending holds.
Codex/Grok wrappers then exit; use `/exit` in Claude. For a hung harness you can
also use `Ctrl-C` in its own terminal. Do not restart a peer inside an existing
run: registration is intentionally single-use. Start a new run instead.

## Options

Start with only two installed peers:

```bash
bash scripts/start.sh runs/two --peers codex grok
# In separate terminals:
bash scripts/agent.sh codex runs/two
bash scripts/agent.sh grok runs/two
```

Launchers use a small **economy preset** by default:

| Peer | Requested model | Requested reasoning |
| --- | --- | --- |
| Codex | `gpt-5.6-luna` | `low` |
| Claude | `haiku` | Not overridden; no added reasoning setting |
| Grok | Existing Grok model | `low` |

This is a practical starting point, not an automatic cheapest-price selector.
Grok's available models depend on your installation/account; choose your light
model with `--model` if its current default is heavier than you want. These
settings are passed to the new session, never saved globally. No premium fast
service tier is requested. Model IDs and supported effort levels can change.

Override model/reasoning for an invocation:

```bash
bash scripts/agent.sh claude runs/first --model sonnet --reasoning low
bash scripts/agent.sh codex runs/first --model YOUR_CODEX_MODEL --reasoning low
bash scripts/agent.sh grok runs/first --model YOUR_GROK_MODEL --reasoning low
```

Use `--profile existing` on an agent command to retain that harness's normal
model/reasoning defaults. Explicit overrides still work with that profile.
Grok receives model/effort CLI arguments before `agent stdio`; the protocol trace
retains whatever model state the native harness advertises. The report records
the requested model or "harness default" and reasoning override; it does not claim
to have observed the exact resolved model/reasoning settings. This proof makes
no CO/XO/worker/model routing decisions.

Other options:

```bash
python3 proof.py run --dir runs/slow --timeout 300 --startup-timeout 900
bash scripts/agent.sh grok runs/slow --binary /absolute/path/to/grok
PROOF_PYTHON=/path/to/python3 bash scripts/start.sh runs/custom
```

Timeouts are per step; authentication and model/tool failures can consume them.
The tmux helper is the all-three/economy-preset shortcut; use separate terminals
for custom peers, models, or binary paths.

## Configuration and boundaries

The proof writes its own files **only below the selected run directory**:
relay credentials, evidence, per-peer working directories, and `claude-mcp.json`.
Codex tools are passed in `thread/start`; Grok MCP configuration in `session/new`;
Claude configuration through `--mcp-config --strict-mcp-config`.

There are no global config edits, global MCP registration, package installations,
credential copying, or permission-bypass flags in this project. Claude's required
`--dangerously-load-development-channels` flag enables a **custom development
channel**; it is not `--dangerously-skip-permissions`. The proof permits only its
proof MCP tools in Claude and disables Claude's built-in tools. Codex uses a
read-only sandbox and declines approval requests. Grok retains local permission
prompts. Peer messages cannot approve permissions.

Harnesses still run with your normal identity and may read their normal settings
and write their own normal session history/cache. This is **not an OS isolation
sandbox** and does not promise that a third-party CLI or an existing hook has no
side effects. Run from an expendable checkout if that matters for your setup.
Do not place the checkout inside a sensitive project whose ancestor instructions
you do not want the harnesses to inherit.

Run tokens protect a loopback HTTP listener from accidental unauthenticated use.
They are not isolation between agents running as the same OS user. Memory checks
assume cooperative agents following the prompt, not agents trying to read logs.
Run folders contain transcripts/test markers and machine-specific paths; keep
them local. They are excluded from Git and the source ZIP.

`AGENTS.md` records development boundaries; `CLAUDE.md` points Claude developers
to it. Static `.codex`, `.grok`, and `.claude` directories are intentionally
unnecessary: this experiment's settings belong to the launched test sessions.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `doctor` says not installed | Confirm PATH in this shell or supply `--binary`; install/authenticate separately using vendor instructions. |
| Claude never reports ready | Accept the development-channel prompt, check `/mcp`, and confirm your account/organization allows channels. Do not bypass an org policy. |
| Grok never reports ready | Confirm this is xAI Grok Build with `agent stdio`, a valid existing login or `XAI_API_KEY`, and inspect its stderr log. |
| Tool/method missing in Codex | Check the installed app-server build against the interfaces below. Experimental schemas can change; retain the failed report. |
| Busy Grok case is unsupported | The build lacks `_x.ai/interject`. Idle ACP prompts alone do not prove busy delivery. |
| Queued but no pass | Look for the matching `submitted`, reply, and `report` events. Queuing alone is not delivery. |
| Run directory exists | Choose a fresh name; the proof deliberately does not overwrite prior evidence. |

## Development and source references

`mhproof/contract.py` contains the prompt and tool schemas; `adapters.py` the native
interfaces; `mcp.py` the small MCP/channel server; `relay.py` message routing;
`suite.py` the verifier; `telemetry.py` traces and diagnostic bundling.
`tests/fake_harness.py` is explicitly a deterministic
wire fixture. Offline tests exercise all three adapter paths and concurrent
delivery with fake harnesses, plus negative verdict cases. They cannot certify
vendor compatibility, authentication, account policy, or actual model behavior.
See `VALIDATION.md` for the initial checks. Rebuild a source-only ZIP with
`python3 scripts/package.py`; it excludes runs, logs, and diagnostic bundles.

Interfaces checked on 2026-09-19:

- [Codex app-server](https://developers.openai.com/codex/app-server), including
  dynamic tools, initialization, and `turn/start.toolOutput`.
- [Codex model selection](https://developers.openai.com/codex/models), including
  the ChatGPT-authenticated replacement of GPT-5.4 mini with GPT-5.6 Luna.
- [Codex dynamic-tool schema](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/schema/typescript/v2/DynamicToolSpec.ts).
- [Claude channels reference](https://code.claude.com/docs/en/channels-reference)
  and [CLI reference](https://code.claude.com/docs/en/cli-reference).
- [Grok ACP/headless documentation](https://docs.x.ai/build/cli/headless-scripting).
- [Grok CLI model/effort flags](https://docs.x.ai/build/cli/reference).
- [Grok interjection implementation at a reviewed commit](https://github.com/xai-org/grok-build/blob/482711333c7195dc16a272777f86086d615e2afb/crates/codegen/xai-grok-shell/src/extensions/interject.rs).
- [ACP session/MCP setup](https://agentclientprotocol.com/protocol/v1/session-setup).

This is an independent proof project for possible later integration with
[Agentscient](https://github.com/uscient/agentscient). It does not modify or depend
on Agentscient. It adds no role hierarchy, benchmark registry, or orchestration
framework. The existing repository's MIT license is preserved.
