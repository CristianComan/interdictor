# INTERDICTOR Implementation Plan

Status snapshot (2026-09-25): v0.1 connects with reconnect-with-backoff,
completes the Registration/RegistrationAck handshake as a `NODE_TYPE_JAMMER`
effector, and implements the full Task -> TaskAck loop (mode_change,
request, START/STOP/PAUSE, concurrent-task-limit, unsupported-command
rejection) against a simulated `JammerController` that never transmits.
Registration and proto bindings were bootstrapped from spectre's v0.1 (same
BSI Flex 335 v2.0 protocol, opposite node role). This plan sequences what's
left.

## Guiding constraints

- Stay on **BSI Flex 335 v2.0** (`src/sapient_msg/bsi_flex_335_v2_0`),
  matching spectre - don't fork protocol versions between the two sibling
  nodes without a concrete reason.
- `JammerController` stays a pure simulator until a real TX safety layer
  exists (see CLAUDE.md's "Safety / what this is not"). Do not shortcut this
  by wiring a real transmitter call into `handle_task` directly.
- Keep the existing async-loop-per-concern shape in `client.py`
  (`receive_loop`, `status_loop`). New capabilities should extend
  `receive_loop`'s dispatch or add a loop, not a new concurrency model.
- Every new outbound message type gets a `build_*` function in
  `messages.py`, mirroring `build_status`/`build_task_ack`.

## Phase 1 - Region-aware tasking

`Task.region` is currently accepted (a `Task` with regions doesn't get
rejected) but never parsed or stored - `registration.json`'s
`regionDefinition` only declares `AREA_OF_INTEREST` as a placeholder. Real
jamming tasking will need this to scope a jam profile to a geofence:

1. Store `task.region` entries on the active task (parallel to
   `active_task_id`).
2. Surface at least the region count/type on `StatusReport` (a `Status`
   entry, `STATUS_TYPE_OTHER`) so a Fusion Node operator can see the node
   registered the region, even before any enforcement exists.
3. Defer actual geofence enforcement (rejecting/gating jam activation based
   on node_location vs region) until interdictor has a real location source
   rather than the fixed config value.

## Phase 2 - Error handling for malformed Task messages

Per the SAPIENT C-UAS Implementation Guide (referenced in spectre's PLAN.md
Phase 1), a malformed `Task` should get an `Error` message, not a `TaskAck`
- only a well-formed but unsupported/rejected `Task` gets `TaskAck`. v0.1
assumes every received `Task` parses cleanly (protobuf guarantees the wire
format; it doesn't guarantee task_id is present, etc.). Add a `build_error`
message and minimal validation (missing `task_id`, missing `control`) ahead
of `handle_task`'s dispatch.

## Phase 3 - Command-parameter-scoped jamming

`Task.Command.command_parameter` (a string, orthogonal to the oneof) is
unused so far. Once a real Fusion Node's tasking conventions for this field
are known, use it to let a single `mode_change` also carry a target
frequency override or duration, rather than requiring a distinct
`mode_name` per exact frequency. Don't guess the schema/semantics here -
verify against a real Fusion Node the same way spectre's quirks were
reverse-engineered (see spectre/CLAUDE.md).

## Phase 4 - Real TX hardware (gated, not started)

This is explicitly the highest-risk phase and must not start casually:

1. Requires the safety layer described in CLAUDE.md first: default-deny TX,
   explicit env/config gate, lease/watchdog/emergency-stop, dedicated tests
   for every stop path - before any code path can key a transmitter.
2. New module `src/interdictor/sdr/` wrapping whatever SDR abstraction is
   chosen (mirrors spectre's Phase 3 Pluto SDR plan, but TX instead of RX).
3. `JammerController` gains a real backend behind the same interface it
   exposes today (`switch_to`/`revert_to_default`/`active_profile`), so
   `client.py`'s Task-handling logic does not need to change - only what
   `JammerController` does internally.
4. This phase needs real hardware-in-the-loop testing and should stay behind
   a config flag (`jamming.backend: simulated|hardware`) so CI/unit tests
   never require physical TX hardware, mirroring spectre's `source:
   synthetic|pluto` pattern.

## Phase 5 - SAPIENT-X extensions

Scope only once a target SAPIENT-X extension spec is pinned down for
effector/jammer-specific status fields (e.g. reporting actual measured
output power, not just the configured `tx_power_dbm`). Treat as
research-then-implement, same caveat as spectre's own Phase 5.

## Cross-cutting / do alongside whichever phase is active

- **Config validation**: `config.py` currently does zero validation. Once
  Phase 1 (region) or Phase 4 (hardware) add new required config keys, add
  fail-fast validation with clear error messages.
- **Tests**: `tests/test_client.py` is the precedent for testing new Task
  command handling - drive `handle_task` directly against a `FakeWriter`,
  decode frames back into `SapientMessage`, assert on the resulting
  `TaskAck`/`StatusReport` sequence. Don't skip tests for accepted-vs-rejected
  branches when adding a new command type.

## Suggested order

1. Phase 1 (region-aware tasking) - low risk, extends what's already there.
2. Phase 2 (Error handling) - closes a correctness gap in the existing
   Task-handling path.
3. Phase 3 (command_parameter) - needs real Fusion Node verification first.
4. Phase 4 (real TX) - gated on the safety layer existing; highest risk,
   do last and deliberately.
5. Phase 5 (SAPIENT-X) - gated on spec availability, do last.
