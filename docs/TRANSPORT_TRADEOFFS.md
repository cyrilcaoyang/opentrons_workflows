# SSH vs HTTP Transport — Pros and Cons

**Status:** written 2026-07-18 (branch `feature-http-ssh-parity`).
**Companion docs:** [`HTTP_TRANSPORT.md`](HTTP_TRANSPORT.md) (why the run
engine, the never-played setup-run model), [`HTTP_SSH_PARITY.md`](HTTP_SSH_PARITY.md)
(method-by-method parity table), [`HTTP_DRIVE_VALIDATION.md`](HTTP_DRIVE_VALIDATION.md)
(2026-07-14 hardware validation).

The OT-2 gateway drives the robot through one of two interchangeable control
backends, selected by `OT2_TRANSPORT` (default `ssh`):

- **SSH REPL** (`OT2Control` + `SSHClient`): a live Python interpreter on the
  robot running the Opentrons protocol API (`execute.get_protocol_api('2.21')`);
  commands are string-built Python sent over an interactive SSH channel.
- **HTTP run engine** (`OT2HttpControl` + `RunEngineClient`): typed commands
  posted to the robot-server API on `:31950` — the same engine the Opentrons
  App uses — inside a protocol-less run that is never `play`ed.

Both share the gateway's state machine, claims, tip store, and the
transport-loss policy (a dropped connection during a non-idempotent liquid
action → `unknown_outcome`, never a silent retry).

## SSH REPL transport

### Pros

- **Full protocol-API power.** Everything the Opentrons Python API can do is
  reachable, including the `invoke()` escape hatch for arbitrary robot-side
  Python. Implicit next-tip tracking, automatic fixed-trash routing, native
  `mix`/`air_gap`, per-axis `max_speeds`, live geometry readbacks — all free.
- **Battle-tested.** Production default; months of bench time across both
  robots (HTE + complexation). The HTTP parity surface is newer and partly
  bench-unverified.
- **Live hardware readbacks.** `has_tip`, `current_volume`, flow rates, and
  geometry come from the robot's own protocol object, not client-side ledgers.
- **Simulation mode.** `simulation=true` swaps in
  `simulate.get_protocol_api()` on the robot — a true dry-run path the HTTP
  transport does not have (the run engine always drives hardware).
- **Leaves no run records.** No leftover `current` run to clean up; the robot's
  run history stays empty.
- **Only needs SSH reachability.** Works even where `:31950` is not reachable
  (observed live: a bare host alias reached SSH but not the HTTP port).

### Cons

- **Deck state is trapped in the live REPL.** The deck picture exists only in
  the in-memory `protocol` object: an idle robot shows a blank deck, and a
  gateway crash or restart loses it (this was the original motivation for the
  HTTP migration — see `HTTP_TRANSPORT.md`).
- **Invisible to the robot's own records.** The robot doesn't know the gateway
  exists; the gateway compensates with the `external_control` state and a
  `/runs` boot probe.
- **Cannot coexist with the `opentrons-ot2` connector** (both want the serial
  port).
- **Fragile by construction.** Commands are string-built Python; results are
  parsed out of a REPL transcript (echoed command, printed value, `>>>`
  prompt). Blank lines inside function bodies break the REPL, encodings bite
  (cp1252 bugs found live), and every readback is a bespoke parse.
- **Slow session lifecycle.** Creating the protocol context takes ~1 min;
  SSH reconnect after a rollback measured ~110 s. `OT2_SSH_COMMAND_TIMEOUT`
  defaults to 120 s for a reason.
- **Operational surface.** Needs the passphrase-protected SSH key, per-host
  `~/.ssh/config`, and `root` on the robot; the Windows `systemprofile` /
  `OT2_SSH_HOME` quirks exist solely to serve it.
- **Error semantics are stringly.** A robot-side exception arrives as
  transcript text, not a typed error object.

## HTTP run-engine transport

### Pros

- **Deck state lives on the robot and survives.** The run record holds loaded
  labware/modules; it persists across gateway restarts and robot idle, and it
  is the robot's own record — the Opentrons App pointed at the robot sees the
  same run (validated live 2026-07-14, including idle-persistence).
- **Typed, structured protocol.** JSON commands with confirmed schemas;
  failures come back as structured `error` objects (`CommandFailed` exposes
  `errorType`/`errorCode`/`detail` for branching) instead of transcript text.
- **Clean completion semantics.** `waitUntilComplete` + server-side timeout →
  `CommandNotCompleted` (an `OSError`) → `unknown_outcome`, with no false
  successes on a hung command.
- **Fast session lifecycle.** Creating a run is one instant POST — no ~60 s
  protocol-context wait, no REPL warm-up.
- **No robot-side dependencies for control.** No SSH key, no REPL, no shipped
  source. (The SSH snapshot path ships `state_readers.py` source over the
  wire each poll.)
- **Coexists with the `opentrons-ot2` connector** — the run engine is served
  by both the stock robot-server and the connector, same hardware lock.
- **First-class custom labware.** Definitions register idempotently via
  `POST .../labware_definitions` (validated live), rather than `exec`-ing a
  dict into the REPL.

### Cons

- **Explicit flow rates required.** The run engine has no protocol-API
  defaults; the adapter supplies env/per-pipette/per-call values
  (`OT2_HTTP_*_FLOW_UL_S`). Wrong defaults are a liquid-handling quality risk
  (aspirate was lowered 150→90 µL/s after the first wet run).
- **No protocol-API conveniences.** Implicit next-tip, auto-trash,
  `mix`/`air_gap`/`return_tip` are emulated client-side; `invoke`,
  `max_speeds`, and protocol-API tip tracking are unsupported
  (`NotImplementedError`). See the parity table for exact semantics.
- **Client-tracked readbacks.** `has_tip` / `current_volume` reflect only what
  *this adapter* did — blind to other clients and to state predating a gateway
  restart. (Module telemetry is the exception: `GET /modules` is live.)
- **Never-played-run quirks.** `pause`/`resume` are no-ops (each setup command
  already blocks to completion); `manualMoveWithPause` is unusable; and a
  leftover HTTP `current` run blocks the SSH gateway until deleted — the two
  transports interfere *on the robot* even though the code is separate.
- **Reachability constraints.** `:31950` must be reachable from the gateway
  host — in practice the robot's tailnet IP, not a bare host alias
  (`OT2_HTTP_BASE_URL` must be set explicitly).
- **Long-held sockets.** A blocking command holds one HTTP request open for up
  to `OT2_HTTP_COMMAND_TIMEOUT` (120 s default); the read timeout must ride
  above the server-side wait.
- **Newer, less proven.** Core cycle + custom labware + idle-persistence are
  hardware-validated (2026-07-14); the full parity surface (trash default,
  coordinate liquid handling, module verbs, geometry readbacks) is
  offline-tested only. Production still defaults to SSH.
- **No simulation mode.** `simulation` is accepted for signature parity and
  ignored; there is no run-engine equivalent of `simulate.get_protocol_api()`.

## Summary table

| Dimension | SSH REPL | HTTP run engine |
|---|---|---|
| Deck state durability | in-memory only; lost on crash/idle | on-robot run record; survives restart |
| Robot's own records | invisible (external_control probing) | native run record; App-visible |
| Command/result format | string-built Python / transcript parsing | typed JSON / structured errors |
| Session start | ~60 s protocol context | instant run creation |
| Reconnect cost | ~110 s observed | one POST |
| Protocol-API conveniences | native | emulated or unsupported |
| Flow-rate defaults | pipette factory defaults | must be supplied (env/call) |
| Readbacks (`has_tip`, volume) | live from robot | client-tracked ledger |
| Module telemetry | live via REPL | live via `GET /modules` |
| Simulation / dry-run | yes (`simulate` API) | no |
| Credentials | SSH key + passphrase + root | none (network reachability only) |
| `opentrons-ot2` connector coexistence | no | yes |
| Robot-side footprint | REPL session + shipped reader source | none |
| Maturity | production default, months on bench | core validated; parity surface offline-only |

## Recommendation (as of 2026-07-18)

Keep **SSH as the production default** until the parity surface passes a bench
run (see the bench-unverified list in `HTTP_SSH_PARITY.md`). Prefer **HTTP for
new work** — deck-state durability, structured errors, and connector
coexistence are architectural wins the SSH path can never match, and the
remaining gaps are validation effort, not design blockers. The switch is
env-only and fully reversible (`OT2_TRANSPORT=http` ↔ unset), so the migration
can proceed robot-by-robot.

Flex remains out of scope for the HTTP transport: its absolute-motion/gripper
surface has no run-engine equivalent (`HTTP_TRANSPORT.md`).
