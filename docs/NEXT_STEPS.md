# Next Steps — `develop-http-drive`

Snapshot as of 2026-07-12. Tracks the remaining work on the opt-in HTTP
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

Nothing here is robot-validated yet.

## At the machine — blocked on physical access (do these first)

1. **Complexation gateway bring-up** — follow `COMPLEXATION_BRINGUP.md`:
   deploy the second `opentrons-server` instance on port 8021 (own checkout,
   own `C:\SDL_State\` files), set up the `ot2training` SSH alias (or the HTTP
   fallback), run the dispense test `plan → dry → wet`, then flip the
   `equipment.yaml` `ot2_complexation` entry `mock → http` **only after** the
   gateway answers on :8021.
2. **HTTP-drive validation on the cytation OT-2** — follow
   `HTTP_DRIVE_VALIDATION.md` end to end (`OT2_TRANSPORT=http`). Specifically
   confirm the four items this branch touched:
   - claim lifecycle (claim → move → heartbeat → release; `details.claimed_by`
     populates);
   - `flow_rate` in an aspirate/dispense body **visibly** changes pipette speed;
   - deck parity: `/status.details.snapshot.deck.source == "run"` matches
     `GET /runs/{id}`, and survives an idle gateway restart (idle deck no longer
     blanks);
   - command wait-timeout → `unknown_outcome` (force with a tiny
     `OT2_HTTP_COMMAND_TIMEOUT` + a slow move).
   Record observations for the flagged gaps (flow-rate defaults, explicit-tip,
   drop-in-place) in the branch PR.

## Desk work — unblocked (can do remotely now)

- **`drop_tip` → fixed trash.** Today HTTP `drop_tip` without a location falls
  back to `dropTipInPlace` (drops where the pipette is, not the trash). Load the
  trash labware id and target its well; add a test. Then update the complexation
  test to drop-to-trash.
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
