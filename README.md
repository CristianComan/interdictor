# INTERDICTOR SAPIENT Effector Node v0.1

Minimal Python SAPIENT effector (jammer) node client for **plain BSI Flex 335
v2.0 SAPIENT**. Sibling project to [spectre](../spectre), which implements
the sensor side of the same protocol; interdictor implements the effector
side.

Scope:

- TCP connection to a Fusion Node / Apex child endpoint, with reconnect-with-backoff
- BSI Flex 335 v2 protobuf framing
- Registration -> RegistrationAck handshake
- Periodic StatusReport (current mode, active task, WGS84 location)
- Tasking: receive `Task`, always reply with exactly one `TaskAck`
  - `mode_change` -> engage/leave a simulated jam profile
  - `request` -> immediate out-of-cycle `StatusReport`
  - `CONTROL_STOP` / `CONTROL_PAUSE` -> revert to the default (non-transmitting) mode
  - anything else (look_at/move_to/patrol/follow/thresholds) -> rejected, out of scope for a fixed effector
- YAML configuration
- TX/RX logging
- Mode-change history: every Tasking-driven mode transition is logged as a
  distinct `MODE CHANGE: ...` line and surfaced on the next `StatusReport`
  (visible on the Fusion Node/C2 UI, not just local logs)
- Network monitoring: TX/RX message and byte counters, per-message-type
  breakdown, and connection error/reconnect counts, logged periodically

Not included yet:

- Real RF transmission of any kind. `JammerController` (`src/interdictor/jam.py`)
  only tracks *which* simulated jam profile is "active" - it never drives an
  SDR or any TX hardware. This is a protocol-level effector simulator for
  interop testing against a Fusion Node, the same way spectre's
  `EWDetectionSource` simulates detections rather than deriving them from
  real RF.
- SAPIENT-X extensions
- Real hardware TX chain (Pluto SDR or otherwise) and any associated safety
  interlocks/lease/watchdog logic that real TX would require

See [PLAN.md](PLAN.md) for the roadmap and [CLAUDE.md](CLAUDE.md) for
architecture notes.

## 1. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 2. Fetch official BSI Flex 335 v2 proto files

The project intentionally does not duplicate the authoritative Dstl proto
files. Pre-generated bindings for BSI Flex 335 v2.0 are already committed
under `src/sapient_msg/` (copied from spectre, same protocol version), so
this step is only needed to regenerate them:

```bash
./scripts/fetch_protos.sh
./scripts/compile_protos.sh
```

The scripts fetch:

https://github.com/dstl/SAPIENT-Proto-Files

and generate Python bindings under `src/sapient_msg`.

## 3. Configure

Edit:

```text
config/interdictor.yaml
config/registration.json
```

The sample Apex v2 child endpoint is configured at `127.0.0.1:5100` by
default - deliberately different from spectre's `5020`, so both clients can
run against the same local Fusion Node without a port clash.

## 4. Run

```bash
interdictor --config config/interdictor.yaml
```

## Important: this is a protocol simulator, not a real jammer

Every "jam profile" in `config/interdictor.yaml` is a description of a
target RF band (centre frequency, bandwidth, simulated TX power) that this
node reports as active in its `mode` field when tasked to switch into it.
No signal is ever generated or transmitted. Treat this exactly as you would
spectre's synthetic EW emitters: useful for exercising a Fusion Node's
Tasking/StatusReport logic end-to-end, not a source of truth about real RF
effects.
