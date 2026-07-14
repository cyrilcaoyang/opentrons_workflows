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

**HTTP transport VALIDATED on real hardware (2026-07-14, `ot2cytation`)** — full
cycle incl. step 12 plate-out, idle-persistence, custom labware, and transport-loss
→ `unknown_outcome`; one live bug found + fixed (off-deck `/status` 500, `ea58a5b`).
Only wait-timeout not force-triggered (shared `unknown_outcome` path + unit-tested).
See `HTTP_DRIVE_VALIDATION.md`. Complexation bring-up still not run.

## At the machine — remaining physical-access work

1. **HTTP-drive validation — DONE** (functional). Optional leftovers: force the
   wait-timeout box live (tiny `OT2_HTTP_COMMAND_TIMEOUT` + slow move), and a wet
   custom-labware run (today's custom-labware test was bookkeeping-only). Deploy
   note: set `OT2_HTTP_BASE_URL` to the robot's reachable **tailnet IP** (bare host
   alias didn't reach `:31950`); on the host use Git Bash + `python`, or SSH into WSL
   and use the tailnet name + `/mnt/c` paths.
2. **Complexation gateway bring-up** — follow `COMPLEXATION_BRINGUP.md`:
   deploy the second `opentrons-server` instance on port 8021 (own checkout,
   own `C:\SDL_State\` files), set up the `ot2training` SSH alias (or the HTTP
   fallback), run the dispense test `plan → dry → wet`, then flip the
   `equipment.yaml` `ot2_complexation` entry `mock → http` **only after** the
   gateway answers on :8021.

## Desk work — unblocked (can do remotely now)

- ✅ **Aspirate flow default lowered** 150 → 90 µL/s (`e0566bf`). Per-call override
  and dispense/blow-out unchanged.
- ◐ **`drop_tip` → trash — mechanism done** (`e0566bf`): `/control/drop-tip` now
  honors an explicit `labware_nickname`+`position`, so HTTP drops into a named
  loaded trash; no location → `dropTipInPlace` (SSH already auto-trashes). **Left:**
  auto-load the OT-2 fixed trash + default the HTTP path to it (bench-unverified),
  and update the complexation test to drop-to-trash.
- ✅ **Multi-channel addressing** — no code change needed: the complexation test
  already uses row-A column addressing for the p20 multi; the `A1→B1` hazard was
  runbook-only and is fixed there. (New finding from the 2026-07-14 run.)
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
