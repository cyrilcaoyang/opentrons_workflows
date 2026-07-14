# Complexation OT-2 — Standalone Gateway Bring-up

**Robot:** "ot2training", Tailscale `100.64.254.91` · **Gateway:** a second
`opentrons-server` instance on `sdl2-pc-03-cytation`, **port 8021** · **No xArm.**
**Companion:** `demo/complexation_dispense_test.py`, `deploy/ot2_complexation.env.example`.

Goal: stand up the gateway, verify it end-to-end with no motion, then run a
pick-up-tip + variable-volume dispense test on both pipettes (dry, then wet).
This is the standalone (no-arm) counterpart to `HTTP_DRIVE_VALIDATION.md`.

Installed pipettes (read from the robot's `:31950/pipettes`):

    left  = p300_single_gen2   (single channel, 20–300 µL)
    right = p20_multi_gen2      (8 channel,       1–20  µL)

> **Have a person at the machine with the e-stop in reach** for Phase C
> (motion). Phases A–B and `--mode plan`/`dry` involve no liquid motion.

> **Shell (learned on the cytation host, 2026-07-14):** run in **Git Bash on the
> host**, and use **`python`** — `python3` is not on PATH there (swap it in the
> commands below). If you use the **HTTP transport fallback**, set `OT2_HTTP_BASE_URL`
> to the robot's reachable **tailnet IP** (`http://100.64.254.91:31950`) — a bare host
> alias did not reach `:31950` from the gateway host on the sibling `ot2cytation` box.

---

## 0. Rollback / stop

```powershell
nssm stop ot2-complexation          # halt the gateway; robot is untouched
# to remove entirely:
nssm remove ot2-complexation confirm
```

Stopping the gateway cannot move the robot — control only happens through it.
Physical backstop: the hardware **e-stop** (INTERLOCKS.md — this is not a
real-time safety system; the human is).

---

## 1. Prerequisites

- [ ] Robot reachable: `curl -fsS -H 'Opentrons-Version: 3' http://100.64.254.91:31950/health`.
- [ ] **SSH transport (default):** the service user has an SSH config alias
      `ot2training` → `100.64.254.91` with key/password working
      (`ssh ot2training echo ok`). If you can't set up SSH now, use the HTTP
      transport instead (see `deploy/ot2_complexation.env.example`) — but note
      `--mode dry` won't truly simulate there.
- [ ] Deck **physically clear** except the two tipracks + one plate:
      - slot 1: `opentrons_96_tiprack_300ul`  (for the p300 single)
      - slot 2: `opentrons_96_tiprack_20ul`   (for the p20 multi)
      - slot 3: `corning_96_wellplate_360ul_flat`
- [ ] For a **wet** run: a little water in the plate's **column 12** (the test's
      aspirate source; the p300 pulls up to 300 µL/well from it). Not needed for
      plan/dry.
- [ ] `C:\SDL_State\` exists (dedicated state-file dir — see §2).

---

## 2. Install the gateway service (port 8021)

Follows `DEVICE_PC_SETUP.md` §3; this is a *second* instance of the
`opentrons-server` repo on the shared PC, so it gets its own port **and its own
state files**.

```powershell
# Clone into its own directory so its state files can't collide with the HTE ot2.
cd C:\Users\sdl2\Projects
git clone https://github.com/cyrilcaoyang/opentrons-server.git opentrons-server-complexation
cd opentrons-server-complexation
git checkout develop-http-drive          # until this merges to main
C:\SDL_Tools\uv.exe sync

New-Item -ItemType Directory -Force C:\SDL_State | Out-Null

# Register the service (uvicorn factory app is at gateway.api:app).
nssm install ot2-complexation C:\SDL_Tools\uv.exe `
    run uvicorn opentrons_server.gateway.api:app --host 0.0.0.0 --port 8021
nssm set ot2-complexation AppDirectory   C:\Users\sdl2\Projects\opentrons-server-complexation
nssm set ot2-complexation DisplayName    "Opentrons OT-2 (Complexation) gateway"
nssm set ot2-complexation Start          SERVICE_AUTO_START
nssm set ot2-complexation AppStdout      C:\SDL_Logs\ot2-complexation.out.log
nssm set ot2-complexation AppStderr      C:\SDL_Logs\ot2-complexation.err.log
nssm set ot2-complexation AppRotateFiles 1
nssm set ot2-complexation AppRotateBytes 10485760
nssm set ot2-complexation ObjectName     ".\labuser" "<password>"

# Environment — from deploy/ot2_complexation.env.example. NSSM wants one
# NUL-delimited AppEnvironmentExtra; set the fields you filled in:
nssm set ot2-complexation AppEnvironmentExtra `
    OT2_EQUIPMENT_ID=ot2_complexation `
    "OT2_EQUIPMENT_NAME=Opentrons OT-2 (Complexation)" `
    OT2_TRANSPORT=ssh `
    OT2_HOST_ALIAS=ot2training `
    OT2_SSH_PASSWORD=<robot-password> `
    OT2_PLATE_STATE_PATH=C:/SDL_State/ot2_complexation_state.json `
    OT2_DECK_STATE_PATH=C:/SDL_State/ot2_complexation_deck_state.json `
    OT2_DRY_RUN=false

nssm start ot2-complexation
sc resume ot2-complexation                # clear NSSM's spurious SERVICE_PAUSED
```

Verify the process is alive (no robot contact yet):

```powershell
curl http://127.0.0.1:8021/                # {equipment_id: ot2_complexation, protocol_version: "1.1"}
curl http://127.0.0.1:8021/status          # equipment_status: requires_init
```

---

## 3. Phase A — verify, no motion

```bash
# Claim (control endpoints require X-Claim-Token); capture the token.
TOKEN=$(curl -fsS -X POST localhost:8021/control/claim \
  -H 'Content-Type: application/json' \
  -d '{"owner":"complexation-bringup","session_id":"bring-1","ttl_s":900}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["claim_token"])')
H="-H X-Claim-Token:$TOKEN -H Content-Type:application/json"

# Startup in SIMULATION — connects/validates without moving liquid.
curl -fsS $H -X POST localhost:8021/control/startup -d '{"simulation":true}'
#   PASS: state: ready
curl -fsS localhost:8021/status | python3 -m json.tool | grep -E 'equipment_status|p300|p20'
curl -fsS $H -X POST localhost:8021/control/release
```

**PASS if** startup returns `ready` in simulation. If it fails, fix transport/SSH
before any motion. (`--mode dry` in the test does this same startup for you.)

---

## 4. Phase B/C — the dispense test

The test script does claim → startup → setup → home → per-pipette
pick-up-tip + (aspirate+dispense)×N → drop-tip → shutdown → release.

```bash
# 1) Offline preview — no network. Read the step list + volume map.
python demo/complexation_dispense_test.py

# 2) Dry — gateway starts the run with simulation=true (no liquid moves).
#    --robot-url makes it verify the attached pipettes match before running.
python demo/complexation_dispense_test.py --mode dry \
    --url http://localhost:8021 \
    --robot-url http://100.64.254.91:31950

# 3) Wet — REAL motion. Person at the e-stop. Column 12 has water.
python demo/complexation_dispense_test.py --mode wet \
    --url http://localhost:8021 --yes-run-on-hardware
```

**PASS if** dry completes every step with 2xx and the pipette-match check is ✅;
then wet runs the full sequence without an unplanned stop, the p300 fills column
1 (A1..H1, 25→300 µL) and the p20 multi fills columns 4–8 (4→20 µL each).

Volumes are hard-coded near the top of the script (`PIPETTES`), stay inside each
pipette's range, and are easy to edit.

---

## 5. Register on the dashboard (AFTER the gateway answers on :8021)

Only now flip the `ac-organic-lab` registry — flipping earlier makes the
aggregator poll a dead endpoint and render the tile "unreachable". In
`equipment.yaml`, replace the `ot2_complexation` entry's `adapter: mock` with:

```yaml
  - id: ot2_complexation
    name: Opentrons OT-2 (Complexation)
    kind: liquid_handler
    adapter: http
    protocol: "1.1"
    base_url: http://sdl2-pc-03-cytation.tail6a1dd7.ts.net:8021
    status_path: /status
    tailscale_ip: 100.64.254.91
    poll_timeout_seconds: 8.0
    do_not_call_connect: true          # SDK must never auto-connect the OT-2
    tiles:
      echem: { w: 2, h: 3 }
    pills: {}
```

Confirm the tile goes green, then commit + push `equipment.yaml` (the central
server pulls it).

---

## 6. Sign-off

- [ ] Gateway answers `/`, `/health`, `/status` on :8021; `requires_init` at rest.
- [ ] Phase A startup succeeds in simulation.
- [ ] `--mode dry` completes all steps; pipette-match check passed.
- [ ] `--mode wet` completed the full cycle without an unplanned stop.
- [ ] `equipment.yaml` flipped to `http` and the tile is green.
- [ ] State files land in `C:\SDL_State\` (not shared with the HTE ot2).

Record date / operator / observations in the branch PR.
