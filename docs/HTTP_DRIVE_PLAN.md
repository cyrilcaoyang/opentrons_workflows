# HTTP-Drive Plan — replacing the SSH REPL with the OT-2 run engine

**Branch:** `develop-http-drive`
**Status:** design map only — no code yet.
**Scope:** OT-2 gateway (`opentrons-server`). Flex (`sdl2_sampleprep_platform`) is
explicitly out of scope: its absolute-motion/gripper surface has no run-engine
equivalent (see the transport analysis; the run engine is well/labware-level only).

## Why

Today `gateway/service.py` drives the robot by holding an SSH interactive Python
REPL (`OT2Control` → `execute.get_protocol_api('2.21')`) and issuing commands as
string-built Python. That works but:

- the deck picture lives only in the live `protocol` object → an idle robot shows
  a blank deck; a crash loses it;
- the robot's own run records don't know the gateway exists (hence the
  `external_control` state and the `/runs` boot probe);
- it can't coexist with the `opentrons-ot2` connector, which owns the serial port.

The OT-2 run engine (robot-server HTTP API on `:31950`) fixes all three: the run
holds deck state **on the robot**, survives idle/restart, is the robot's own
record, and is served by both the stock robot-server *and* the `opentrons-ot2`
connector (in-process, same hardware lock). So this migration is also what makes
adopting `opentrons-ot2` a no-op for our control path.

## Confirmed against the live robot (2026-07-11, via the gateway probe)

Pulled from `GET :8020/status` → `details.robot` on `sdl2-pc-03-cytation`:

- **Opentrons software `8.7.0`** (`api_version` and `equipment_version`), fw
  `v1.1.0-25e5cea`, `robot_model: "OT-2 Standard"`, `robot_name: "ot2cytation"`.
  8.7.0 is a modern run-engine build — the imperative "create a run, post commands"
  path is exactly what the Opentrons App uses for live/quick-transfer control.
- **Pipettes installed:** left = `p300_multi_v2.0` (`p300_multi_gen2`, 8 ch);
  right = `p1000_single_v2.0` (`p1000_single_gen2`, 1 ch). These pin the
  `loadPipette` params (`mount` + `pipetteName`).
- `run_active: false` at probe time; gateway reports `ready`.
- Exact `:31950` route/command schemas for 8.7.0 are being confirmed from the
  Opentrons source at tag `v8.7.0` (see the "Confirmed endpoint reference" section
  appended below once verified). The robot's `:31950` is only reachable from the
  cytation host, not the dev box, so verification is from source, not a live pull.

## Existing gateway request models → run-engine command params (offline mapping)

Our current request shapes (`gateway/models.py`) already carry almost everything a
command needs; the translation is mechanical:

| Gateway model | Fields today | Maps to command params |
|---|---|---|
| `LiquidMoveRequest` + `WellLocation` | `pipette`, `volume_ul`, `location{labware_nickname, position, top, bottom, center}` | `aspirate`/`dispense`: `pipetteId`(←nickname map), `labwareId`(←nickname map), `wellName`=`position`, `volume`=`volume_ul`, `wellLocation{origin, offset.z}` ← `top`/`bottom`/`center`, **`flowRate` — MISSING, must add or default from pipette** |
| `TipRequest` | `pipette`, `labware_nickname?`, `position?` | `pickUpTip`/`dropTip`: `pipetteId`, `labwareId`, `wellName` |
| `MoveLabwareRequest` | `labware_nickname`, `new_location` | `moveLabware`: `labwareId`, `newLocation`, `strategy` (add) |
| `ProtocolSetupRequest` | `labware[]`, `instruments[]`, `modules[]` | `loadLabware` / `loadPipette` / `loadModule` commands (+ custom-def registration) |
| `PlateLoadRequest` / `LoadedPlate` / `WellSample` | plate/well bookkeeping | unchanged — `PlateStateStore` only |

**Gaps to close in the request models:**
- `LiquidMoveRequest` has no `flow_rate` — either add an optional field (falling back
  to the pipette's default flow rate) or resolve a default server-side.
- `WellLocation.center: bool` needs mapping to a run-engine well `origin` enum value;
  confirm the enum (top/bottom/center/meniscus) from the 8.7.0 schema.
- Need a **nickname → run-engine id** map (labware + pipette), populated from each
  `load*` command's returned id. Replaces the REPL variable names used today.

## Model shift in one line

**SSH "session" → run engine "run".** Opening a session becomes creating + playing
a run; each imperative command becomes one command POSTed to that run with
`waitUntilComplete`; closing the session becomes stopping the run. Deck reads come
from `GET /runs/{runId}` instead of the REPL snapshot.

## HTTP surface we depend on (verify exact spelling against the robot's `/openapi.json`)

| Purpose | Call |
|---|---|
| Create a run (empty, live-driven) | `POST /runs` → `{ data: { id } }` |
| Play / pause / stop a run | `POST /runs/{runId}/actions` `{ data: { actionType: "play"\|"pause"\|"stop" } }` |
| Issue one command, block for result | `POST /runs/{runId}/commands?waitUntilComplete=true[&timeout=ms]` |
| Read run + loaded labware/modules/pipettes (deck picture) | `GET /runs/{runId}` |
| Register a custom labware definition on a run | `POST /runs/{runId}/labware_definitions` (body = our generated definition JSON) |
| Health / external-run probe (already ported) | `GET /health`, `GET /runs`, `GET /instruments` |
| Deck lights (already ported) | `GET`/`POST /robot/lights` |

All calls carry the `Opentrons-Version: 3` header (already set in `service.py`).

### Command intent nuance (verify)

Run-engine commands carry an `intent`. Setup-class commands (`loadLabware`,
`loadPipette`, `loadModule`, `home`) execute immediately; movement commands
(`pickUpTip`, `aspirate`, `dispense`, `dropTip`, `moveLabware`) require the run to
be **playing**. Plan: create run → `play` once at startup → then POST commands with
`waitUntilComplete`. Confirm setup-vs-protocol intent behaviour on our robot
software version before finalising.

## Endpoint-by-endpoint translation (our gateway API is unchanged; only the backend swaps)

The gateway's public `/control/*` surface stays identical — callers see no change.
Only what `service.py` does internally changes: from `self.control.invoke(...)` to
an HTTP command POST.

| Gateway endpoint | SSH today | HTTP run-engine translation |
|---|---|---|
| `/control/startup` | open REPL, `get_protocol_api`, load instruments | `POST /runs`; `play`; `loadPipette` per mount |
| `/control/setup` | `setup_protocol` (load labware/instruments/modules) | `loadLabware` / `loadModule` / `loadPipette` commands; custom defs via `labware_definitions` first |
| `/control/plate/load` | bookkeeping + (labware object) | `loadLabware` for the incoming plate **+ existing `PlateStateStore` bookkeeping unchanged** |
| `/control/pick-up-tip` | `pick_up_tip` invoke | `pickUpTip` command (labware + well) |
| `/control/aspirate` | `aspirate` invoke | `aspirate` command (labware, well, volume, flowRate, offset) |
| `/control/dispense` | `dispense` invoke | `dispense` command (labware, well, volume, flowRate, offset, pushOut) |
| `/control/drop-tip` | `drop_tip` invoke | `dropTip` command (or `dropTipInPlace`) |
| `/control/move-labware` | `move_labware` invoke | `moveLabware` command, `strategy: manualMoveWithPause` (OT-2 has no gripper) |
| `/control/plate/unload` | bookkeeping | `moveLabware` off-deck / mark removed **+ `PlateStateStore` bookkeeping unchanged** |
| `/control/home` | `protocol.home()` | `home` command |
| `/control/pause` `/resume` | REPL pause/resume | `actions` `pause` / `play` |
| `/control/shutdown` | close REPL session | `actions` `stop` (+ drop run reference) |
| `/control/lights` | already HTTP | unchanged |
| `/control/deck/declare`, `/control/well/update` | pure gateway bookkeeping (no robot call) | **unchanged — never touched SSH** |
| deck snapshot (`refresh_snapshot`) | REPL `get_all_states(protocol)` | parse `GET /runs/{runId}` loaded labware/modules (reuse `probe_run_labware` parsing) |

## The plate-in → aspirate/dispense → plate-out cycle, step by step

Assume startup already created + played a run (`runId`) and loaded the pipette(s)
and tip rack. A plate arrives on the deck (placed by the xArm or an operator).

1. **Plate-in (register the incoming plate)** — `/control/plate/load`
   - If the plate uses **custom labware**: `POST /runs/{runId}/labware_definitions`
     with the JSON from `labware_generator.generate_definition()` (reused verbatim).
   - `loadLabware` command → `{ loadName, namespace, version, location: <slot> }`.
   - Update `PlateStateStore` (`details.loaded_plate`) exactly as today — this
     bookkeeping is transport-independent and does not change.
   - Deck now shows the plate via `GET /runs/{runId}` even when idle.

2. **Pick up a tip** — `/control/pick-up-tip`
   - `pickUpTip` command → `{ pipetteId, labwareId: <tiprack>, wellName }`.

3. **Aspirate** — `/control/aspirate`
   - `aspirate` command → `{ pipetteId, labwareId: <source>, wellName, volume,
     flowRate, wellLocation: { origin: "bottom", offset: { z } } }`.
   - `waitUntilComplete=true`; read the command result for success/failure.

4. **Dispense** — `/control/dispense`
   - `dispense` command → same shape against `<dest>` (plus `pushOut` if used).

5. *(optional)* **blow-out / touch-tip / mix** — `blowout` / `touchTip` commands,
   or a mix loop of aspirate/dispense against the same well.

6. **Drop tip** — `/control/drop-tip`
   - `dropTip` command → `{ pipetteId, labwareId: <trash/tiprack>, wellName }`.

7. **Plate-out (hand the plate off)** — `/control/plate/unload`
   - `moveLabware` command with `strategy: manualMoveWithPause` to move it off-deck,
     or simply mark it removed if the xArm lifts it directly.
   - Clear/adjust `PlateStateStore` as today.
   - Deck reflects the removal via `GET /runs/{runId}`.

Errors: each command POST returns a structured result; a failed command yields
`status: failed` + an error object → map to our `CommandResponse`/`last_error`
instead of scraping a REPL traceback. This is cleaner than today.

## What we KEEP as-is (no work)

- **`labware_generator.py`** — pure JSON; reused verbatim. Only the *load* step
  changes (register-then-`loadLabware` instead of inline `load_labware_from_definition`).
- **`PlateStateStore` / `details.loaded_plate`**, `/control/well/update`,
  `/control/deck/declare` — gateway-side bookkeeping, never touched the robot.
- The gateway's public API, claim protocol, and STATUS_SPEC envelope shape.
- The HTTP client plumbing, `/health` + `/runs` + `/instruments` probes, and lights
  control already in `service.py`.

## What is NEW work

1. A small run-engine client in `service.py` (or a new `control/http_run.py`):
   create/play/stop run, `post_command(commandType, params)` with
   `waitUntilComplete`, error mapping.
2. Translate the ~9 command ops in the table above to command payloads (mechanical,
   close to 1:1).
3. Run lifecycle wired into the existing session state machine (`OT2ServiceState`).
4. Swap `refresh_snapshot()` deck source from REPL to `GET /runs/{runId}` parsing.
5. `pipetteId`/`labwareId` bookkeeping: the run engine addresses labware/pipettes by
   the id returned from their `load*` commands. We must keep a nickname → id map
   (small; replaces the REPL variable names we use today).

## Open questions to resolve before coding

- Exact endpoint paths/verbs + command intent semantics on our robot software
  version (pull `/openapi.json` from the live robot).
- `moveLabware` manual-strategy behaviour on OT-2 (pause/resume handshake) vs. the
  xArm physically doing the move — decide whether the gateway issues `moveLabware`
  at all for plate-out or just updates bookkeeping.
- Whether a plain `POST /runs` (empty run) accepts live commands, or a
  maintenance-run flow is required for our version.
- Custom labware `namespace`/`version` collision handling on repeat loads.

## Test path

- Point the gateway at a **simulated** robot or the `opentrons-ot2` connector in
  `use_simulator` mode; drive the full cycle; assert deck state via `GET /runs`.
- Compare deck snapshot parity with the current REPL snapshot for the same recipe.
- Verify idle-deck persistence: load a plate, tear down the gateway session, confirm
  `GET /runs/{runId}` still reports the plate.
