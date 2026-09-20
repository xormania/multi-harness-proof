# Configurable coordination experiments

A configuration describes the coordination plan and launch settings. Each
invocation creates a fresh timestamped directory with a random suffix, preserves
the input configuration and resolved defaults, copies the work fixture, and
retains all reports and telemetry. Editing the configuration and rerunning never
overwrites previous evidence. Evidence is never automatically deleted and delivered messages are not retried.
The optional managed launcher closes only its own finished harness windows.

## Start with the supplied mock experiment

```bash
bash scripts/experiment.sh proof.mock.example.json
```

The example runs a baseline, delays a Claude reply, and drops Grok's second work
message. Each uses a four-message burst per peer. The first two should pass; the
dropped message should leave the proof unverified. All participants are fake
processes using the real adapters and verifier. No native CLIs or model calls
are involved, regardless of configured model names.

Equivalent command:

```bash
python3 proof.py experiment --config proof.mock.example.json
```

Copy/edit an example to create your own scenario. Paths are relative to the
configuration file, not the shell's working directory. The shipped examples put
results under `runs/experiments/`.

## Configuration fields

| Field | Meaning |
| --- | --- |
| `schema_version` | Must be `1`; unknown fields and duplicate JSON keys are rejected |
| `name` | Human-readable run prefix; letters, digits, underscores and hyphens |
| `mode` | `mock` (default) or explicitly `live` |
| `output_root` | Directory holding separate experiment runs |
| `peers` | Selected native participants; live accepts two or three distinct peers |
| `timeout` | Per-step seconds; default 3 for mocks, 180 for live |
| `startup_timeout` | Registration/MCP startup seconds; default 10 for mocks, 600 for live |
| `fixture` | Optional build-log JSON; copied into the run before work starts |
| `agents` | Per-peer `binary`, `profile`, `model`, and `reasoning` settings |
| `checks` | Message pairs, busy pairs, work selection, and burst size |
| `scenarios` | Named mock scenarios, presets, expectations, and optional fault rules |

The configuration is validated before launching processes or creating a run.
The work fixture must have at least two nonempty batches, valid rows, and no
duplicate job/attempt within a batch. Two batches are necessary to measure
unfinished work across the burst gate. All peers receive the same copied input.

Mocks currently require the three peers in `codex`, `claude`, `grok` order. A
configured live run may select two. Configuration does not inject arbitrary shell
commands, alter permissions, change native ingest paths, or write global settings.
Protocol instructions and the verification predicate remain fixed.

### Choose the coordination plan

```json
"checks": {
  "round_trip_pairs": [["codex", "grok"], ["grok", "codex"]],
  "busy_pairs": [["claude", "grok"]],
  "work": true,
  "burst_per_peer": 6
}
```

Pairs are `[source, target]`. `"all"` selects every ordered round-trip pair and
`"ring"` selects the standard busy checks. An empty list skips that stage. Pairs
must be unique, with different peers. At least one coordination stage is required.
Work bursts follow the selected peer order in a ring, with 1–64 challenges per
peer. Later batches remain gated until the burst has been submitted. Each failed
stage still stops that proof; all exchanges already launched in a work burst are
independently evaluated and retained. Changing the plan never weakens the pass predicate.

For example, two round trips, one busy check, and six work messages per peer with
three peers produce 21 message cases, plus three work scores. Repeating the
experiment uses new sessions and unique challenge values.

### Define a targeted fault

```json
"scenarios": [{
  "name": "second-work-message-lost",
  "preset": "happy",
  "expect": "unverified",
  "faults": [{
    "peer": "grok",
    "on": "challenge",
    "case_prefix": "work-",
    "occurrence": 2,
    "times": 1,
    "action": "drop"
  }]
}]
```

`preset` defaults to `happy`. Any of the 24 presets in [TESTING.md](TESTING.md)
can be selected. Custom rules are checked in listed order as the fake peer
handles a message. They are reproducible rules, not random fault injection.

| Rule field | Values |
| --- | --- |
| `peer` | The fake participant to affect |
| `on` | `instruction`, `challenge`, or `reply` |
| `case_prefix` | Case ID prefix; omitted/empty matches all cases |
| `occurrence` | First matching occurrence to affect, starting at 1 |
| `times` | Consecutive matching occurrences to affect, default 1 |
| `action` | `delay`, `drop`, `wrong-memory`, `stale-nonce`, or `crash` |
| `seconds` | Required only for delay; greater than 0, up to 300 |

`wrong-memory` changes a challenge reply. `stale-nonce` applies to challenges or
replies. Delay blocks the fake peer's message handler; it does not pretend to
model network packet loss or token-generation internals. Drop discards handling
of that message after native submission. Crash exits the fake native process.
Protocol-specific failures, such as a missing Grok method or Claude hook, remain
available through the predefined presets.

Use `round-trip-`, `busy-`, `work-`, or an exact case prefix to target a stage.
Instructions also use `startup`, `work-batch-N`, and `work-burst`. Rules count
matching messages separately per rule. The fault log identifies exactly which
message and occurrence triggered. A proof may stop before a later rule fires;
`untriggered_rules` makes that visible. Required but untriggered rules make an
expected-outcome check mismatch.

`expect` can be `pass`, `unverified`, or `observe`. Custom fault scenarios default
to `observe`, which collects the result without asserting a predicted verdict.
An experiment marked `complete` means its scenarios finished and expectations
matched or were observed. Individual **proof reports** still say `pass` or
`unverified`; an expected failure never becomes a passing proof.

## Repeat and compare

```bash
python3 proof.py history --dir runs/experiments
python3 proof.py compare runs/experiments/FIRST_RUN runs/experiments/SECOND_RUN
```

Substitute the directories printed by the launcher. History lists verdicts,
failing phases, first incomplete cases, and tool audits. Comparison shows changed
settings, source/fixture hashes, case-status changes, native CLI versions and
requested settings, and both sets of outcomes. A missing case is `not_recorded`,
not a pass. Comparing observations does not establish causation.

Keep scenario names stable when comparing revisions of the same experiment.
Change one variable at a time where practical. Status is the last saved state;
a run killed before finalization can remain `running`, which is not a liveness
claim. Start another run instead of reusing its directory.

Each experiment preserves `experiment.json`, `experiment-result.json`, copied
`fixture.json`, launch settings, and per-scenario run directories. Snapshots keep
both requested input and resolved defaults. Reports additionally record actual
launch requests and native version strings. Native effective model/reasoning
selection still requires vendor evidence; a requested setting is not proof that
the vendor honored it. See [TESTING.md](TESTING.md) for the full telemetry list.

## Configured live run

```bash
bash scripts/experiment.sh proof.live.example.json
```

This starts a real relay and prints one `proof.py agent ... --dir ...` command per
selected participant. Run those commands in separate terminals. Each adapter
reads its launch settings from that run's snapshot. Models, reasoning, and
binary paths can be changed centrally in `agents`:

```json
"agents": {
  "codex": {"profile": "existing", "model": "YOUR_MODEL", "reasoning": "low"},
  "claude": {"profile": "coordination", "model": "sonnet", "reasoning": "low"},
  "grok": {"profile": "existing", "binary": "/path/to/grok", "reasoning": "low"}
}
```

`coordination` requests Luna, Sonnet, and Grok 4.5 with low reasoning. It is the
managed launcher's default and is explicit in the shipped live example.
`economy` remains the default when a general experiment plan omits the profile. An explicit `null` model/reasoning removes
that override. Command-line agent flags can override snapshot launch requests;
the actual requested values and binary are recorded with registration, so compare
those records too. Authentication and native channel/permission prompts remain
interactive. To run the same live plan with managed tmux windows, summaries,
and automatic diagnostic ZIPs:

```bash
bash scripts/proof.sh start --config proof.live.example.json
```

Use `bash scripts/proof.sh start --from RUN_DIRECTORY` to repeat a managed saved
plan in a fresh directory. Managed runs use the layout and controls documented
in [OPERATIONS.md](OPERATIONS.md); `history`/`compare` above operate on experiment
runner directories. Mock fault rules are rejected in live configurations.
