# HTTP-Drive Validation Runbook

**Branch:** `develop-http-drive` · **Companion to:** `docs/HTTP_DRIVE_PLAN.md`
**Goal:** validate the opt-in HTTP (run-engine) transport against the real robot
(`ot2cytation`, Opentrons 8.7.0) for one full plate-in → aspirate/dispense →
plate-out cycle, and close each flagged gap with a real observation.

**Run this ON the cytation host** (`sdl2-pc-03-cytation`) — the gateway (`:8020`)
and the robot's HTTP API (`:31950`) are both reachable only from there. Have a
person at the machine with the **e-stop in reach** for the whole session.

> This is the step that turns "not robot-validated" into "validated." Nothing in
> the codebase should be trusted for production until every PASS box below is
> ticked. If anything looks wrong, hit **Rollback (§0)** first, diagnose second.

---

## 0. Rollback — know this before you start

To return to the known-good SSH path **at any moment**:

```bash
# in the gateway service env (systemd/NSSM): remove OT2_TRANSPORT, then restart
sudo systemctl restart ot2-gateway     # or the NSSM service name on this host
```

Confirm SSH is back:

```bash
curl -fsS localhost:8020/status | python3 -c 'import sys,json;print(json.load(sys.stdin)["equipment_status"])'
```

The SSH REPL path is byte-for-byte unchanged, so rollback is a restart, not a
redeploy. Physical backstop: the hardware **e-stop** (per INTERLOCKS.md, this is
not a real-time safety system — the human at the machine is).

---

## Pre-flight

- [ ] Deck **physically clear** except the tiprack + plate you'll use for the test.
- [ ] Know the two installed pipettes: **left = `p300_multi_gen2`**,
      **right = `p1000_single_gen2`** (confirm with `curl localhost:8020/status`).
- [ ] Pick a tiprack matching a pipette (e.g. `opentrons_96_tiprack_300ul` in slot 1
      for the p300) and a plate (e.g. `corning_96_wellplate_360ul_flat` in slot 2).
- [ ] A source well with a little water, a destination well, for a visible transfer.
- [ ] Git: on `develop-http-drive`, working tree clean.

### Claim helper (gateway control endpoints require a claim)

```bash
# Acquire a claim; capture the token for X-Claim-Token on every /control/* call.
TOKEN=$(curl -fsS -X POST localhost:8020/control/claim \
  -H 'Content-Type: application/json' \
  -d '{"owner":"http-validation","session_id":"val-1","ttl_s":600}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["claim_token"])')
echo "$TOKEN"
H="-H X-Claim-Token:$TOKEN -H Content-Type:application/json"
```

(Send a `POST /control/heartbeat` with the token if the session runs past the TTL.)

---

## Phase A — bring up HTTP mode (no motion yet)

1. [ ] Enable the transport in the gateway service env and restart:
   ```
   OT2_TRANSPORT=http
   # OT2_HTTP_BASE_URL=http://<robot-host>:31950   # only if it differs from the SSH host
   ```
   ```bash
   sudo systemctl restart ot2-gateway
   ```
2. [ ] Gateway is up and still reports the robot:
   ```bash
   curl -fsS localhost:8020/status | python3 -m json.tool | grep -E 'equipment_status|api_version'
   ```
   **PASS if** `equipment_status` is `requires_init` (awaiting startup) and the robot
   probe still shows `api_version: 8.7.0`.
3. [ ] `POST /control/startup` (creates the unplayed run — no motion):
   ```bash
   curl -fsS $H -X POST localhost:8020/control/startup -d '{"simulation":false}'
   ```
   **PASS if** it returns `state: ready`.
4. [ ] **Confirm a run exists on the robot** and is NOT playing:
   ```bash
   curl -fsS -H 'Opentrons-Version: 3' localhost:31950/runs \
     | python3 -c 'import sys,json;r=json.load(sys.stdin)["data"][-1];print(r["id"],r["status"])'
   ```
   **PASS if** status is `idle` / setup-phase (NOT `running`). Record the run id.

> If Phase A fails, rollback (§0). Do not proceed to motion.

---

## Phase B — the cycle (motion; human at the e-stop)

Do **A→B for real** but start conservative. `home` first every time the gantry
might be in the way (manual moves do NOT retract — HTTP_DRIVE_PLAN.md §handoff).

5. [ ] **Home** (safe pose):
   ```bash
   curl -fsS $H -X POST localhost:8020/control/home
   ```
   **PASS if** the gantry homes and returns `ready`.
6. [ ] **Setup** — load pipette + tiprack + plate:
   ```bash
   curl -fsS $H -X POST localhost:8020/control/setup -d '{
     "instruments":[{"ot_default":true,"nickname":"p300","instrument_name":"p300_multi_gen2","mount":"left"}],
     "labware":[
       {"ot_default":true,"nickname":"tips","loadname":"opentrons_96_tiprack_300ul","location":"1"},
       {"ot_default":true,"nickname":"plate","loadname":"corning_96_wellplate_360ul_flat","location":"2"}
     ],
     "modules":[]
   }'
   ```
   **PASS if** `ready` AND the robot shows all three loaded:
   ```bash
   curl -fsS -H 'Opentrons-Version: 3' localhost:31950/runs/<run-id> \
     | python3 -c 'import sys,json;d=json.load(sys.stdin)["data"];print([l.get("loadName") for l in d.get("labware",[])]); print([p.get("pipetteName") for p in d.get("pipettes",[])])'
   ```
7. [ ] **Plate-in bookkeeping** (records sample metadata; deck occupancy is already
   in the run from step 6):
   ```bash
   curl -fsS $H -X POST localhost:8020/control/plate/load \
     -d '{"plate_id":"VAL-1","model":"corning_96_wellplate_360ul_flat","wells":[{"well":"A1","sample_id":"water","volume_ul":200}]}'
   ```
8. [ ] **Pick up tip** — note `labware_nickname`+`position` are REQUIRED in HTTP mode
   (no implicit next-tip):
   ```bash
   curl -fsS $H -X POST localhost:8020/control/pick-up-tip -d '{"pipette":"p300","labware_nickname":"tips","position":"A1"}'
   ```
   **PASS if** the pipette picks up the A1 tip. **FAIL/observe:** if it errors about a
   missing location, that's the expected explicit-tip gap — record it.
9. [ ] **Aspirate** from the source well:
   ```bash
   curl -fsS $H -X POST localhost:8020/control/aspirate -d '{"pipette":"p300","volume_ul":50,"location":{"labware_nickname":"plate","position":"A1","bottom":2}}'
   ```
   **WATCH the aspirate speed** — see §C (flow-rate gap).
10. [ ] **Dispense** to a destination well:
    ```bash
    curl -fsS $H -X POST localhost:8020/control/dispense -d '{"pipette":"p300","volume_ul":50,"location":{"labware_nickname":"plate","position":"B1","bottom":2}}'
    ```
11. [ ] **Drop tip:**
    ```bash
    curl -fsS $H -X POST localhost:8020/control/drop-tip -d '{"pipette":"p300"}'
    ```
    **OBSERVE where the tip drops** — see §C (drop-location gap).
12. [ ] **Plate-out** — home, then record the plate leaving. **This is the
    safety-critical handoff.** In a real workflow the xArm lifts the plate; for this
    solo test, home first, then move it off-deck in the engine:
    ```bash
    curl -fsS $H -X POST localhost:8020/control/home
    curl -fsS $H -X POST localhost:8020/control/move-labware -d '{"labware_nickname":"plate","new_location":"OFF_DECK"}'
    curl -fsS $H -X POST localhost:8020/control/plate/unload
    ```
    **PASS if** `move-labware` returns immediately with **no pause prompt** and no
    hang (confirms `manualMoveWithoutPause`, not `manualMoveWithPause`), and the run
    now shows slot 2 empty:
    ```bash
    curl -fsS -H 'Opentrons-Version: 3' localhost:31950/runs/<run-id> \
      | python3 -c 'import sys,json;print([ (l.get("location"),l.get("loadName")) for l in json.load(sys.stdin)["data"].get("labware",[]) ])'
    ```

---

## Phase C — close each flagged gap (record real values)

Tick these only with a real observation written down.

- [ ] **Flow rates.** `LiquidMoveRequest` now carries an optional `flow_rate`
      (µL/s), threaded through `OT2HttpControl.aspirate/dispense`; when omitted the
      adapter falls back to the `OT2_HTTP_*_FLOW_UL_S` defaults (aspirate 150 /
      dispense 300 / blow-out 100). During steps 9–10 judge whether the *default*
      speed is acceptable, then re-run one transfer with an explicit
      `"flow_rate": <µL/s>` in the body and confirm the pipette visibly changes
      speed. Capture the pipette's real defaults to pick sensible env values:
      ```bash
      curl -fsS -H 'Opentrons-Version: 3' localhost:31950/pipettes | python3 -m json.tool
      ```
      **Decision to record:** the env defaults to ship, and whether callers should
      always pass `flow_rate` per transfer. (blow-out still has no request field —
      it uses the env default only.)
- [ ] **Explicit-tip requirement.** Confirm step 8 works *with* location and that a
      bare `{"pipette":"p300"}` pick-up is rejected. Decide whether the gateway should
      track a "next tip" itself or callers always pass the well. Record the choice.
- [ ] **Drop location.** From step 11, note whether the tip landed in the trash or
      **in place** (expected: in place, via `dropTipInPlace`). If trash-drop is
      required, implement drop-to-`fixedTrash` (load the trash labware + pass its
      well) and re-test.
- [ ] **Deck-snapshot parity.** `_refresh_snapshot_http` now feeds the run's
      loaded labware/modules into `_last_run_labware`, so the deck tile is built
      through the same `normalize_run_slots` → `build_deck` path as SSH (run source).
      **Verify** after step 6 that `curl localhost:8020/status | jq
      '.details.snapshot.deck'` shows `source: "run"` with slots 1/2 occupied and
      matches what `GET /runs/{id}` reports. **Then test idle persistence:** after
      the cycle, `sudo systemctl restart ot2-gateway`, do NOT call
      `POST /control/startup`, and confirm the last run's deck is still readable
      from `:31950` — the whole point of the migration (idle deck no longer blanks).
- [ ] **Custom labware.** Repeat step 6 with an `ot_default:false` entry carrying a
      generated definition (`{"config": <labware_generator JSON>}`) and confirm it
      registers (`POST /runs/{id}/labware_definitions`) then loads. Re-running the
      same definition must not error (idempotent).
- [ ] **Claim/transport loss.** Kill the robot's network briefly mid-idle and confirm
      a control call surfaces as `unknown_outcome` (non-idempotent) via the existing
      `_run_action` path — i.e. `RunEngineUnreachable` (OSError) is handled like an
      SSH drop.
- [ ] **Command wait-timeout.** A blocking command that outlives the server-side
      `waitUntilComplete` timeout now raises `CommandNotCompleted` (an OSError), so a
      non-idempotent action lands in `unknown_outcome` rather than a false success.
      Optionally force it by setting a tiny `OT2_HTTP_COMMAND_TIMEOUT` and issuing a
      slow move; confirm the gateway reports `unknown_outcome`, not `ready`.

---

## Sign-off

- [ ] Full cycle (steps 5–12) completed on hardware without an unplanned stop.
- [ ] Every §C gap has a recorded observation + a decision (fix now / accept / defer).
- [ ] Deck parity implemented and idle-persistence confirmed, OR explicitly deferred
      with the limitation written into `HTTP_DRIVE_PLAN.md`.
- [ ] Flow-rate handling resolved (real values wired, not guessed defaults) before any
      production use.
- [ ] Reverted to SSH (`OT2_TRANSPORT` unset) at end of session unless the team has
      agreed to leave HTTP enabled.

Record results (date, operator, run id, per-step notes, decisions) alongside this
file or in the branch PR. Only after sign-off should `OT2_TRANSPORT=http` be
considered for anything beyond a supervised bench test.
