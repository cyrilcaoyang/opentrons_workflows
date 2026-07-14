# Next Steps — `develop-http-drive`

Snapshot as of 2026-07-14. Tracks the remaining work on the opt-in HTTP
run-engine transport and the standalone complexation bring-up. Companion to
`HTTP_DRIVE_PLAN.md`, `HTTP_DRIVE_VALIDATION.md`, and `COMPLEXATION_BRINGUP.md`.

## Done (on this branch)

- Opt-in HTTP run-engine transport (`OT2_TRANSPORT=http`), SSH path unchanged.
- `execute()` raises `CommandNotCompleted` (OSError) on a wait-timeout →
  non-idempotent actions land in `unknown_outcome`, not a false success.
- Per-call `flow_rate` (µL/s) on `LiquidMoveRequest`, threaded through both
  control backends (HTTP falls back to `OT2_HTTP_*_FLOW_UL_S`).
- HTTP deck-snapshot parity via `_last_run_labware` → `normalize_run_slots`.
- Test-isolation fix for the repo-root-anchored state stores.
- Complexation kit: `demo/complexation_dispense_test.py`,
  `deploy/ot2_complexation.env.example`, `docs/COMPLEXATION_BRINGUP.md`.
- Full offline suite green (`178 passed`); `--mode plan` validated offline.

**HTTP transport partially validated on real hardware (2026-07-14, `ot2cytation`)**
— see `HTTP_DRIVE_VALIDATION.md`. Complexation bring-up still not run.

## At the machine — remaining physical-access work

1. **Finish HTTP-drive validation on the cytation OT-2** — core cycle (home →
   setup → plate-in → pick-up → aspirate → dispense → drop-tip) PASSED, plus the
   flow-rate / explicit-tip / drop-location gaps + deck-parity (first half). Still
   to run (unticked boxes in `HTTP_DRIVE_VALIDATION.md`):
   - **step 12 plate-out** (`move-labware OFF_DECK` → no pause prompt =
     `manualMoveWithoutPause`);
   - **deck idle-persistence** (restart gateway, no startup, deck still readable);
   - **custom labware** register + load (idempotent);
   - **transport-loss** → `unknown_outcome`;
   - **command wait-timeout** → `unknown_outcome` (tiny `OT2_HTTP_COMMAND_TIMEOUT`).
   Deploy note: set `OT2_HTTP_BASE_URL` to the robot's reachable **tailnet IP**
   (bare host alias didn't reach `:31950`); run in Git Bash with `python`.
2. **Complexation gateway bring-up** — follow `COMPLEXATION_BRINGUP.md`:
   deploy the second `opentrons-server` instance on port 8021 (own checkout,
   own `C:\SDL_State\` files), set up the `ot2training` SSH alias (or the HTTP
   fallback), run the dispense test `plan → dry → wet`, then flip the
   `equipment.yaml` `ot2_complexation` entry `mock → http` **only after** the
   gateway answers on :8021.

## Desk work — unblocked (can do remotely now)

- **Lower the aspirate flow default.** Validation judged the default ≈150 µL/s
  slightly fast → set `OT2_HTTP_ASPIRATE_FLOW_UL_S` to ~80–100 in the gateway env
  (dispense/blow-out left as-is pending a real recipe).
- **`drop_tip` → fixed trash.** Confirmed live: HTTP `drop_tip` without a location
  falls back to `dropTipInPlace` (drops where the pipette is; `home`-first lands at
  slot 12 trash region). Load the trash labware id and target its well; add a test.
  Then update the complexation test to drop-to-trash.
- **`blow_out` endpoint + flow rate.** `blow_out` exists in the control adapters
  but has no `/control/blow-out` route and no request field. If a complexation
  step needs it, add the route + a `flow_rate` field (mirroring
  aspirate/dispense) and a SkillDef.
- **OT-2 protocol-action SkillDefs** (`ac-organic-lab` roadmap item): typed
  labware args for `setup`/`home`/`aspirate`/`dispense`/`pick_up_tip`/
  `drop_tip`/`move_labware` so `lab.skills()` covers the OT-2 surface.
- **Modules-before-labware** ordering + `loadModule` adapter support — only if
  the complexation protocol uses a temperature / heater-shaker module (flagged
  in `http_control.py`; not handled today).
- **Test-script enhancements** (`demo/complexation_dispense_test.py`):
  source-refill handling for the wet run (the p300 depletes column 12),
  CLI/JSON-driven volume maps instead of hard-coded `PIPETTES`, and an optional
  per-call `flow_rate`.
- **Low-pri cleanup:** `tests/test_ssh_methods.py` / `tests/test_states.py` emit
  `PytestReturnNotNoneWarning` (tests `return` instead of `assert`).

## Housekeeping

- Keep the branch rebased on `main` as other OT-2 work lands.
- Once robot-validated (both items above signed off), open the
  `develop-http-drive → main` PR; paste the validation observations into it.
- The `lab-status-contract` shared-package extraction and MCP milestone (v0.4)
  are `ac-organic-lab` concerns, tracked in that repo's `ROADMAP.md` — not here.
