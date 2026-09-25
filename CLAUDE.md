# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

INTERDICTOR is a minimal Python **SAPIENT effector (jammer) node** client
implementing plain **BSI Flex 335 v2.0** (the UK Dstl SAPIENT protocol
variant), registering as `NODE_TYPE_JAMMER`. It is the effector-side sibling
of [spectre](../spectre) (a `NODE_TYPE_PASSIVE_RF` sensor node) - same
protocol, same wire format, opposite role: spectre reports detections,
interdictor accepts Tasking to (simulate) jamming and reports its own status.

This codebase was scaffolded from spectre's v0.1 structure and adapted for
the effector role. Treat `src/interdictor` as the real product code - it's
small, deliberately minimal, and every module has a single clear job. Don't
add abstraction ahead of the roadmap below.

## Repository layout

```
src/interdictor/         Product code (async TCP client)
  main.py                 CLI entrypoint (argparse -> load_config -> asyncio.run)
  client.py                SapientEffectorClient: reconnect-with-backoff loop wrapping
                            connect / send / receive_loop / status_loop / handle_task
  config.py                YAML config loader
  framing.py                4-byte LE length-prefix framing over the TCP stream
  messages.py               Builds SapientMessage envelopes (Registration/StatusReport/TaskAck)
  jam.py                    JammerController: tracks current mode / simulated jam profile - never transmits
  mode_history.py           ModeHistory: records every Tasking-driven mode transition; last one is
                            surfaced on StatusReport.status[] (visible on the Fusion Node/C2 side) and
                            logged as a distinct "MODE CHANGE: ..." line
  netmon.py                 NetworkStats: TX/RX message+byte counters, per-type breakdown, connect/
                            send/receive error and reconnect/disconnect counts; summary() is logged
                            once per status interval (see client.send_status)
  proto.py                  Re-exports generated protobuf classes (raises a clear error if ungenerated)
  ids.py                    ULID generation for report_id/task tracking

src/sapient_msg/          Generated protobuf Python bindings (output of scripts/compile_protos.sh)
  bsi_flex_335_v2_0/       v2.0 message set actually used by this client - copied verbatim from
                           spectre's already-generated output since both clients speak the same
                           protocol version; regenerate independently via the scripts below if
                           spectre's copy ever drifts.

config/
  interdictor.yaml         Runtime config: fusion_node host/port, node_id, status interval,
                            default_mode, jamming.profiles (simulated jam bands per mode)
  registration.json         The Registration message body sent on connect: NODE_TYPE_JAMMER,
                            STANDBY (default) + one MODE_TYPE_PERMANENT mode per jamming.profiles
                            entry in interdictor.yaml, each declaring COMMAND_TYPE_MODE_CHANGE and
                            COMMAND_TYPE_REQUEST as acceptable Task commands.

scripts/
  fetch_protos.sh            git-clones dstl/SAPIENT-Proto-Files into vendor/ (regenerable, gitignored)
  compile_protos.sh          Runs grpc_tools.protoc to regenerate src/sapient_msg/**_pb2.py

tests/                     pytest unit tests (framing, JammerController, Task/TaskAck handling)
vendor/                    Fetched upstream proto sources - gitignored, regenerate via fetch_protos.sh
```

## Protocol / wire format

Identical to spectre - see spectre's CLAUDE.md for the full writeup of the
framing, handshake ordering, and Fusion Node registration-validation
quirks (exact `icdVersion` string match, per-field advertisement
requirements, node_id allowlisting). Nothing here changes that; the only
protocol-level difference is which messages this node sends and receives:

- **Sends**: `Registration`, `StatusReport`, `TaskAck`. Never `DetectionReport`
  - interdictor is an effector, not a sensor.
- **Receives**: `RegistrationAck`, `Task`, `Error`.
- Every `Task` gets exactly one `TaskAck` in response (accepted or rejected
  with a reason) - never left un-acked, and never both an `Error` and a
  `TaskAck` for the same message (a malformed `Task` would get an `Error`
  instead, but that path isn't implemented yet - see PLAN.md).

### Tasking model (the core of this node)

Unlike spectre v0.1 (which shipped detection-only, with Tasking as future
work), interdictor's entire purpose is task-driven: a jammer that ignores
Tasking does nothing useful. `client.handle_task` implements:

- `Task.control == CONTROL_START` + `command.mode_change` -> validate the
  mode name against `JammerController.known_modes()` (the registered default
  mode plus every `jamming.profiles[].mode_name` in `interdictor.yaml`);
  switch mode, track `active_task_id`, ack `TASK_STATUS_ACCEPTED`, then push
  an immediate out-of-cycle `StatusReport` so the mode change is visible
  without waiting for the next `status.interval_s` tick.
- `Task.control == CONTROL_START` + `command.request` -> same immediate
  `StatusReport` push, ack `TASK_STATUS_ACCEPTED`, without a mode change.
- `Task.control in (CONTROL_STOP, CONTROL_PAUSE)` -> if it matches
  `active_task_id`, revert to the registered default mode
  (`JammerController.revert_to_default()`), clear `active_task_id`, ack
  `TASK_STATUS_ACCEPTED`; otherwise ack `TASK_STATUS_REJECTED` /
  `resource unavailable`.
- A second concurrent `CONTROL_START` while `active_task_id` is already set
  -> `TASK_STATUS_REJECTED` / `concurrent task limit` (mirrors
  `registration.json`'s `concurrentTasks: 1`).
- Any other command (`look_at`, `move_to`, `patrol`, `follow`,
  `detection_threshold`, `detection_report_rate`,
  `classification_threshold`) -> `TASK_STATUS_REJECTED` / `unsupported
  command`. interdictor is a fixed, non-pointable, non-mobile effector; these
  commands target sensor/platform capabilities it doesn't have.

Reject reasons are drawn verbatim from the SAPIENT C-UAS Implementation
Guide's Appendix B reserved strings (see `client.py`'s `REASON_*`
constants), matching the convention spectre's PLAN.md documents for its own
(not-yet-implemented) Tasking phase.

## Observability: mode changes and network stats

Two small components exist purely for operator/developer visibility, wired
into `client.py` at every relevant call site rather than living behind a
separate polling API:

- **`mode_history.ModeHistory`** - every successful `mode_change` or
  `CONTROL_STOP`/`CONTROL_PAUSE` reversion calls `client._record_mode_change`,
  which both logs a distinct `MODE CHANGE: <from> -> <to> (task_id=...)` line
  and appends a `ModeTransition` (capped at 100 entries). The *latest*
  transition is also passed into `messages.build_status`, which adds it as a
  `StatusReport.status[]` entry (`STATUS_TYPE_OTHER`) - so a mode change is
  visible on the Fusion Node/C2 UI itself, not just in local logs. Note this
  history is **not** reset on reconnect (unlike `jammer`/`active_task_id`,
  which are): the first `StatusReport` after a fresh reconnect can still
  carry the last transition from a *previous* connection. This is
  intentional (it's a historical record, not "current state"), but worth
  remembering if it looks surprising in a Fusion Node log.
- **`netmon.NetworkStats`** - counts messages/bytes sent and received (with
  a per-message-type breakdown), plus send/receive errors, connect attempts/
  failures, and disconnects. Every `send()`/`receive_loop()`/`connect()`/
  `close()` call site updates it (see the `self.net_stats.record_*` calls in
  `client.py`). `client.send_status()` logs `net_stats.summary()` once per
  `status.interval_s` tick, piggybacking on the existing periodic cadence
  instead of adding a second timer loop. Counters persist for the whole
  process lifetime, including across reconnects, by design - they're meant
  to answer "how has this connection behaved overall", not just "since the
  last reconnect".

**Registration gap this required**: like `DetectionReport`/`Signal` fields
on the sensor side (see spectre's CLAUDE.md), a `StatusReport.Status` entry
must be individually advertised in `registration.json`'s
`statusDefinition.statusReport[]` (`category:
STATUS_REPORT_CATEGORY_STATUS`, `type` matching what's actually populated -
here `"Mode Change"`) or the Fusion Node will silently strip it. If a future
change adds another `Status` entry type, add a matching declaration here
too, and re-verify end-to-end (watch for `warning: ... field ignored`, not
just hard errors - same quirk class documented in spectre's CLAUDE.md).

## Safety / what this is *not*

`JammerController` (`jam.py`) is pure bookkeeping: it records which
configured "jam profile" (centre frequency, bandwidth, simulated TX power)
is currently selected, purely so it can be reflected in `StatusReport.mode`.
**It never drives an SDR, opens a TX chain, or emits any RF.** There is no
hardware adapter in this codebase at all yet. If a future task adds one
(Pluto SDR or otherwise), it must follow ROGUE's TX-safety conventions even
though this is a separate repository - default-deny TX, explicit
env/config-gated enablement, lease/watchdog/emergency-stop, dedicated tests
for the stop paths - before any code path can key an actual transmitter.
Do not casually wire `JammerController` state into a real TX call without
that safety layer existing first.

## Build / run workflow

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Only needed if you want to regenerate src/sapient_msg from scratch
# (a copy is already committed, taken from spectre's own generated output):
./scripts/fetch_protos.sh      # clones dstl/SAPIENT-Proto-Files into vendor/
./scripts/compile_protos.sh    # regenerates src/sapient_msg/**_pb2.py

interdictor --config config/interdictor.yaml
```

A SAPIENT Fusion Node (e.g. Apex child endpoint) must be listening at the
configured host/port for the client to connect; otherwise it fails fast on
`asyncio.open_connection`. Default is `127.0.0.1:5100`, deliberately
different from spectre's `5020` default so both clients can run against the
same local test Fusion Node (`CI-map-viewer`) at once without a port
clash - that node's own `config/application.properties` needs a matching
`network.sapient.connections.N` entry (`host=127.0.0.1`, not `0.0.0.0`'s
subnet-typo-prone neighbors - a `172.0.0.1` typo there once caused a
`BindException: Cannot assign requested address` that looked from this side
like a plain refused connection).

Tests: `pytest`. `tests/test_client.py` drives `SapientEffectorClient.handle_task`
directly against a fake writer (no real socket/Fusion Node needed) to assert
the TaskAck/StatusReport behavior above; `tests/test_mode_history.py` and
`tests/test_netmon.py` cover the two observability components in isolation.
This is the precedent to extend when adding new Task command handling or
new counters.

**Node identity**: `node.node_id` in `interdictor.yaml` is read once at
startup (`main.py`) and never regenerated - stays fixed for the whole
process lifetime, including across `client.py`'s automatic reconnects. One
running instance = one stable node_id. `main.py` also accepts `--node-id
<uuid>` to override the config value, for running multiple `interdictor`
instances in parallel against the same Fusion Node.

## Conventions to follow

- Async everywhere in `client.py`, with the same two-level shutdown handling
  spectre uses: `self._shutdown` is the process-level stop signal; each
  connection attempt gets its own fresh `conn_lost` event, forwarded from
  `_shutdown` by a small watcher task. `run()` wraps connect -> register ->
  await ack -> run loops -> close in a `while not self._shutdown.is_set()`
  loop with exponential backoff. Keep new long-running loops following this
  shape.
- All outbound messages are built as small, pure `build_*` functions in
  `messages.py` that return a fully-populated `SapientMessage`, then sent via
  `client.send()`. Keep new message types following this same builder shape.
- `proto.py` is the single import surface for generated protobuf classes -
  import from there, not directly from `sapient_msg.bsi_flex_335_v2_0.*`.
- Config is a plain dict from YAML (`config.py`), not a schema/dataclass, same
  as spectre - don't introduce a config framework without discussing it first.
- No error handling/fallbacks for cases that can't occur; validate only at
  real boundaries (parsing external config/registration files, the network
  socket).

## Current scope (v0.1) vs. not yet implemented

Implemented: TCP connect with reconnect-with-backoff, BSI Flex 335 v2
framing, Registration -> RegistrationAck handshake, periodic StatusReport
(mode + active_task_id + WGS84 location), full Task -> TaskAck handling for
mode_change/request/START/STOP/PAUSE/concurrent-task-limit/unsupported
commands, simulated `JammerController` (no real TX), YAML config, TX/RX
logging.

Not yet implemented: real RF hardware TX of any kind (and the safety layer
that must come with it - see "Safety / what this is not" above), region
filtering (`Task.region` is accepted in the schema sense but not yet parsed
here - interdictor's registration doesn't currently declare region support
beyond the placeholder AREA_OF_INTEREST type), `Error` message handling for
malformed Tasks, SAPIENT-X extensions.

## Git

- `vendor/`, `.venv/` are gitignored - never `git add -f` them.
- Working branch is `develop`; `main` is the default/PR-target branch.
