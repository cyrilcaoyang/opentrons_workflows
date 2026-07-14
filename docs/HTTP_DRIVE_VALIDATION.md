# HTTP-Drive Validation — Runbook & Results

**Branch:** `develop-http-drive` · **Companion:** `docs/HTTP_DRIVE_PLAN.md`
**Goal:** validate the opt-in HTTP (run-engine) transport against the real robot
(`ot2cytation`, Opentrons 8.7.0) for one full plate-in → aspirate/dispense →
plate-out cycle, and close each flagged gap with a real observation.

This is a live checklist: boxes are ticked **only** with a real hardware
observation. Tick top-down; everything still unchecked is outstanding.

**Status: PARTIAL sign-off.** Core cycle (bring-up → home → setup → plate-in →
pick-up → aspirate → dispense → drop-tip) validated; flow-rate / explicit-tip /
drop-location gaps + deck-parity (first half) closed. Step 12 and several §C checks
remain — see the unticked boxes.

### Run log

| | |
|---|---|
| **Session** | 2026-07-13 evening (local) / 2026-07-14 UTC |
| **Operator** | Cyril Cao (`yangcyril.cao@utoronto.ca`) |
| **Robot** | `ot2cytation` — OT-2 Standard, Opentrons `8.7.0`, fw `v1.1.0-25e5cea`, tailnet `100.64.254.90` (SSH host `192.168.254.50`) |
| **Gateway** | `ot2-gateway` NSSM service, port `8020`, host `sdl2-pc-03-cytation` (Windows hostname `DESKTOP-OVJ3SSL`) |
| **Run id** | `40cdcab8-3eba-416b-a075-…` (created `2026-07-14T01:48:22Z`; deleted at end) |

> **Run this ON the gateway host, in Git Bash** (native Windows — `localhost:8020`
> and the robot's `:31950` do not resolve from WSL). Use **`python`**, not `python3`.
> Service is **NSSM**, not systemd. Have a person at the machine with the **e-stop in
> reach** for the whole session.

---

## 0. Rollback — know this before you start

Return to the known-good SSH path at any moment (elevated cmd): drop the transport
overrides and restart.

```bat
C:\SDL_Tools\nssm.exe set ot2-gateway AppEnvironmentExtra OT2_HOST_ALIAS=192.168.254.50 OT2_SSH_PASSWORD=<pw>
C:\SDL_Tools\nssm.exe restart ot2-gateway
```

Confirm SSH is back:
```bash
curl -fsS localhost:8020/status | python -c 'import sys,json;print(json.load(sys.stdin)["equipment_status"])'
```

The SSH REPL path is byte-for-byte unchanged, so rollback is a restart, not a
redeploy. Physical backstop: the hardware **e-stop** (per INTERLOCKS.md — not a
real-time safety system; the human at the machine is).

> **Two things to expect on rollback (observed 2026-07-14):**
> 1. **SSH reconnect takes ~110 s** — the gateway sits in `busy`/`"connecting"` while
>    the REPL/protocol context comes up. Wait it out. (This latency is what HTTP avoids.)
> 2. **A leftover HTTP `current` run makes the SSH gateway stand off** (`busy`, *"Robot
>    has an active run … standing off"*; `boot_reconnect` is one-shot). Clear it, then
>    restart:
>    ```bash
>    curl -X DELETE -H 'Opentrons-Version: 3' http://100.64.254.90:31950/runs/<run-id>
>    ```
>    ```bat
>    C:\SDL_Tools\nssm.exe restart ot2-gateway
>    ```

---

## Pre-flight

- [x] Deck **clear** except tiprack (`opentrons_96_tiprack_300ul`, slot 1) + plate
      (`corning_96_wellplate_360ul_flat`, slot 2). Water in plate column 1.
- [x] Pipettes confirmed: **left `p300_multi_gen2`** (8-ch, used), right
      `p1000_single_gen2` (`curl localhost:8020/status`).
- [x] **Robot identity verified first** — `GET :31950/health` → `name`. There are
      multiple OT-2s on the tailnet; `100.64.254.91`=`ot2training` was excluded,
      `100.64.254.90`=`ot2cytation` used.
- [x] Git: on `develop-http-drive`, working tree clean.

### Claim helper (control endpoints require a claim; hard-enforced → 423 if stale)

```bash
TOKEN=$(curl -fsS -X POST localhost:8020/control/claim -H 'Content-Type: application/json' \
  -d '{"owner":"http-validation","session_id":"val-1","ttl_s":1800}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["claim_token"])')
echo "$TOKEN"; H="-H X-Claim-Token:$TOKEN -H Content-Type:application/json"
```
> Use a generous `ttl_s` (30 min); a lapsed claim returns **423** on `/control/*`.
> Re-run this block to refresh (idempotent for the same `session_id`).

---

## Phase A — bring up HTTP mode (no motion)

- [x] **1. Enable transport** (elevated cmd) — `AppEnvironmentExtra` replaces the whole
      block, so pass **all** vars. **Set `OT2_HTTP_BASE_URL` to the robot's reachable
      tailnet IP** — the bare host alias did *not* reach `:31950` from the gateway host.
      ```bat
      C:\SDL_Tools\nssm.exe set ot2-gateway AppEnvironmentExtra OT2_HOST_ALIAS=192.168.254.50 OT2_SSH_PASSWORD=<pw> OT2_TRANSPORT=http OT2_HTTP_BASE_URL=http://100.64.254.90:31950
      C:\SDL_Tools\nssm.exe restart ot2-gateway
      ```
- [x] **2. Gateway up, robot still probed** — `api_version: 8.7.0`, `robot_name: ot2cytation`.
      ```bash
      curl -fsS localhost:8020/status | python -m json.tool | grep -E 'equipment_status|api_version|robot_name'
      ```
- [x] **3. Startup (unplayed run, no motion).** Happened **automatically** —
      `OT2_AUTO_RECONNECT` defaults true, so boot_reconnect ran startup (guarded: only
      because the robot was reachable **and** idle). Returned `ready`.
- [x] **4. Run exists, NOT playing** — created `idle`, `current: true`.
      ```bash
      curl -fsS -H 'Opentrons-Version: 3' http://100.64.254.90:31950/runs \
        | python -c 'import sys,json;r=json.load(sys.stdin)["data"][-1];print(r["id"],r["status"])'
      ```

---

## Phase B — the cycle (motion; human at the e-stop)

`home` first whenever the gantry might be in the way (manual moves do NOT retract).

> **Multi-channel addressing:** the p300 is 8-channel — address by **column using
> row-A wells only** (`A1`, `A2`, …); the head auto-spans the 8 rows. Telling a multi
> to go to `B1` shifts the head **down one row**, putting the bottom channel **off the
> plate** (observed). The old `A1→B1` example was single-channel logic.

- [x] **5. Home** → `ready`. `curl -fsS $H -X POST localhost:8020/control/home`
- [x] **6. Setup** (load pipette + tiprack + plate) → `ready`; robot shows all three.
      ```bash
      curl -fsS $H -X POST localhost:8020/control/setup -d '{"instruments":[{"ot_default":true,"nickname":"p300","instrument_name":"p300_multi_gen2","mount":"left"}],"labware":[{"ot_default":true,"nickname":"tips","loadname":"opentrons_96_tiprack_300ul","location":"1"},{"ot_default":true,"nickname":"plate","loadname":"corning_96_wellplate_360ul_flat","location":"2"}],"modules":[]}'
      ```
- [x] **§C deck-parity (first half)** — after step 6, gateway `/status` deck flipped
      `source: declared → run`, slots 1&2 `source=run`, matching `GET /runs/{id}`.
      ```bash
      curl -fsS localhost:8020/status | python -c 'import sys,json;d=json.load(sys.stdin)["details"]["snapshot"]["deck"];print(d["source"]);import json as j;[print(k,d["slots"][k]["slot_state"],d["slots"][k]["source"]) for k in ("1","2")]'
      ```
- [x] **7. Plate-in bookkeeping** (sample metadata; no motion).
      ```bash
      curl -fsS $H -X POST localhost:8020/control/plate/load -d '{"plate_id":"VAL-1","model":"corning_96_wellplate_360ul_flat","wells":[{"well":"A1","sample_id":"water","volume_ul":200}]}'
      ```
- [x] **8. Pick up tip** — explicit `labware_nickname`+`position` REQUIRED.
      ```bash
      curl -fsS $H -X POST localhost:8020/control/pick-up-tip -d '{"pipette":"p300","labware_nickname":"tips","position":"A1"}'
      ```
- [x] **9. Aspirate** (watch flow rate — §C).
      ```bash
      curl -fsS $H -X POST localhost:8020/control/aspirate -d '{"pipette":"p300","volume_ul":50,"location":{"labware_nickname":"plate","position":"A1","bottom":2}}'
      ```
- [x] **10. Dispense** (use `A2` for the multi, not `B1`).
      ```bash
      curl -fsS $H -X POST localhost:8020/control/dispense -d '{"pipette":"p300","volume_ul":50,"location":{"labware_nickname":"plate","position":"A2","bottom":2}}'
      ```
- [x] **11. Drop tip** — `home` first so `dropTipInPlace` lands tips at slot 12 (trash).
      ```bash
      curl -fsS $H -X POST localhost:8020/control/home
      curl -fsS $H -X POST localhost:8020/control/drop-tip -d '{"pipette":"p300"}'
      ```
- [ ] **12. Plate-out (safety-critical handoff) — NOT YET RUN.** `home`, then
      `move-labware OFF_DECK` (must return immediately, **no pause prompt** → confirms
      `manualMoveWithoutPause`), then `plate/unload`; run then shows slot 2 empty.
      ```bash
      curl -fsS $H -X POST localhost:8020/control/home
      curl -fsS $H -X POST localhost:8020/control/move-labware -d '{"labware_nickname":"plate","new_location":"OFF_DECK"}'
      curl -fsS $H -X POST localhost:8020/control/plate/unload
      ```

---

## Phase C — flagged gaps

- [x] **Flow rates — CLOSED.** Per-call `"flow_rate":30` visibly slowed the plunger vs.
      default ≈150 µL/s (judged *slightly fast*). **Decision:** lower
      `OT2_HTTP_ASPIRATE_FLOW_UL_S` to ~80–100; keep per-call override; blow-out is
      env-only (no request field). Factory flow defaults are **not** exposed by
      `:31950/pipettes` (motor API only). Re-tested with:
      ```bash
      curl -fsS $H -X POST localhost:8020/control/aspirate  -d '{"pipette":"p300","volume_ul":50,"location":{"labware_nickname":"plate","position":"A1","bottom":2},"flow_rate":30}'
      curl -fsS $H -X POST localhost:8020/control/dispense -d '{"pipette":"p300","volume_ul":50,"location":{"labware_nickname":"plate","position":"A2","bottom":2},"flow_rate":30}'
      ```
- [x] **Explicit-tip requirement — CLOSED.** Bare `{"pipette":"p300"}` pick-up → **409**;
      explicit well works. **Decision:** keep the explicit-well contract (no gateway-side
      next-tip tracking).
- [x] **Drop location — OBSERVED (fix deferred).** No location → `dropTipInPlace`
      (drops where the head is). `home` first lands tips at slot 12 (fixed-trash region)
      — usable stopgap. Real drop-to-`fixedTrash` remains future work.
- [ ] **Deck-snapshot parity — idle-persistence NOT YET RUN.** (Run-sourced deck already
      confirmed above.) After the cycle: `nssm restart ot2-gateway`, do **not** call
      startup, confirm the last run's deck is still readable from `:31950` (the whole
      point — idle deck no longer blanks).
- [ ] **Custom labware — NOT YET RUN.** Repeat step 6 with an `ot_default:false` entry
      carrying `{"config": <labware_generator JSON>}`; confirm it registers
      (`POST /runs/{id}/labware_definitions`) then loads; re-running the same def is
      idempotent.
- [ ] **Claim / transport loss — NOT YET RUN.** Kill the robot's network mid-idle;
      confirm a non-idempotent control call surfaces as `unknown_outcome`
      (`RunEngineUnreachable` handled like an SSH drop).
- [ ] **Command wait-timeout — NOT YET RUN.** Tiny `OT2_HTTP_COMMAND_TIMEOUT` + a slow
      move; confirm `CommandNotCompleted` → `unknown_outcome`, not a false `ready`.

---

## Sign-off

- [ ] Full cycle (steps 5–12) completed on hardware without an unplanned stop —
      **steps 5–11 done; step 12 outstanding.**
- [ ] Every §C gap has a recorded observation + decision — **flow-rate / explicit-tip /
      drop-location done; deck idle-persistence, custom labware, transport-loss,
      wait-timeout outstanding.**
- [ ] Deck parity idle-persistence confirmed, OR deferred with the limitation written
      into `HTTP_DRIVE_PLAN.md`.
- [x] Flow-rate handling resolved (per-call works; lower the aspirate env default before
      production).
- [x] Reverted to SSH at end of session (transport overrides removed; leftover run
      deleted; claim released; gateway back to `ready`).

**Before production HTTP use:** wire the lowered aspirate flow default; run the
outstanding boxes above (especially step 12 + idle-persistence); implement
drop-to-`fixedTrash` if required; keep the multi-channel + leftover-run-standoff notes.

**Security:** the robot SSH password appeared in `nssm get` output this session and is
now in operator transcripts — rotate when convenient.
