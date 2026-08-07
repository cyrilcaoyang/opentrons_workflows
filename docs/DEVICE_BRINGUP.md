# Opentrons Device Bring-up — adding another robot to the fleet

**What this is:** the parameterized runbook for standing up an
`opentrons-server` gateway for an additional Opentrons robot, verifying it
end-to-end (no motion → simulated → real motion), and registering it on the
dashboard. Generalized 2026-07-19 from the complexation bring-up (the
original per-robot doc; its signed-off record is preserved in
[§8](#8-completed-bring-ups-records) below).

The architecture is **one gateway process per robot**: each instance gets its
own port, its own state files, and its own NSSM service on the (shared)
gateway PC. Nothing is hard-coded to a single robot — every per-robot fact is
an environment variable read at startup (see the README's *Running multiple
OT-2 gateways* for the full env table).

> **Have a person at the machine with the e-stop in reach** for any phase
> involving motion. The no-motion and simulation phases are safe to run
> remotely. Stopping the gateway (`nssm stop <service>`) can never move the
> robot — control only happens through it; the hardware e-stop is the
> physical backstop.

---

## 1. Fill in the placeholders

Pick these before starting; everything below is written in terms of them.

| Placeholder | Meaning | Convention / example |
|---|---|---|
| `<id>` | `equipment.yaml` id + `OT2_EQUIPMENT_ID` | `ot2_<bench>` — e.g. `ot2_complexation` |
| `<name>` | human-readable name | `Opentrons OT-2 (<Bench>)` |
| `<port>` | gateway listen port | next free on the PC: 8020 (HTE), 8021 (complexation), 8022… |
| `<robot-ip>` | robot's reachable IP (tailnet preferred) | e.g. `100.64.254.91` |
| `<ssh-alias>` | SSH config `Host` for the robot | the robot's name, e.g. `ot2training` |
| `<service>` | NSSM service name | `ot2-gateway-<bench>` (the first instance is plain `ot2-gateway`) |
| `<dir>` | repo clone directory | `C:\Users\sdl2\Projects\opentrons-server-<bench>` |
| state files | plate / deck / tip stores | `C:\SDL_State\<id>_state.json`, `<id>_deck_state.json`, `<id>_tip_state.json` |

**Current fleet** (for port/name collision checks):

| id | robot | robot IP | port | service |
|---|---|---|---|---|
| `ot2_hte` | `ot2cytation` | `100.64.254.90` | 8020 | `ot2-gateway` |
| `ot2_complexation` | `ot2training` | `100.64.254.91` | 8021 | `ot2-gateway-complexation` |

> ⚠️ **Distinct state paths are mandatory.** The stores default to
> `./ot2_*.json` relative to the working directory; two instances started
> from the same directory with defaults would corrupt each other. Always set
> all three per-instance paths.

---

## 2. Prerequisites

- [ ] Robot on the network and reachable:
      `curl -fsS -H 'Opentrons-Version: 3' http://<robot-ip>:31950/health`.
- [ ] **Transport decision.** HTTP is what both existing robots run, so it is
      the default choice for a new one: set `OT2_TRANSPORT=http` and
      `OT2_HTTP_BASE_URL=http://<robot-ip>:31950` explicitly (a bare host alias
      has been observed not to reach `:31950`). It needs no SSH at all, but has
      no simulation mode. Choosing SSH instead (the code default for an unset
      `OT2_TRANSPORT`) means the service user needs an SSH config alias
      `<ssh-alias>` → `<robot-ip>` with the AC key working
      (`ssh <ssh-alias> echo ok`).
      Trade-offs: [`TRANSPORT_TRADEOFFS.md`](TRANSPORT_TRADEOFFS.md).
- [ ] Read the attached pipettes — they parameterize the motion test:
      `curl -fsS -H 'Opentrons-Version: 3' http://<robot-ip>:31950/pipettes`.
- [ ] Deck **physically clear** except what the motion test needs (§5).
- [ ] `C:\SDL_State\` exists on the gateway PC.
- [ ] Shell note (learned on the cytation host): use **Git Bash** and
      **`python`** (not `python3`) for the test-script commands.

---

## 3. Install the gateway service

Follows `ac-organic-lab` `DEVICE_PC_SETUP.md` §3. Clone into a robot-specific
directory so state files and checkouts can't collide:

```powershell
cd C:\Users\sdl2\Projects
git clone https://github.com/cyrilcaoyang/opentrons-server.git opentrons-server-<bench>
cd opentrons-server-<bench>
C:\SDL_Tools\uv.exe sync --extra labware

New-Item -ItemType Directory -Force C:\SDL_State | Out-Null

# --extra labware in AppParameters is load-bearing: `uv run` self-syncs the
# project environment at every service start, and without the extra it prunes
# opentrons-shared-data, silently emptying the UI's GET /labware catalog.
nssm install <service> C:\SDL_Tools\uv.exe `
    run --extra labware uvicorn opentrons_server.gateway.api:app --host 0.0.0.0 --port <port>
nssm set <service> AppDirectory   C:\Users\sdl2\Projects\opentrons-server-<bench>
nssm set <service> DisplayName    "<name> gateway"
nssm set <service> Start          SERVICE_AUTO_START
nssm set <service> AppStdout      C:\SDL_Logs\<service>.out.log
nssm set <service> AppStderr      C:\SDL_Logs\<service>.err.log
nssm set <service> AppRotateFiles 1
nssm set <service> AppRotateBytes 10485760
nssm set <service> ObjectName     ".\labuser" "<password>"

# Environment — copy deploy/ot2_complexation.env.example as the template.
nssm set <service> AppEnvironmentExtra `
    OT2_EQUIPMENT_ID=<id> `
    "OT2_EQUIPMENT_NAME=<name>" `
    OT2_TRANSPORT=ssh `
    OT2_HOST_ALIAS=<ssh-alias> `
    OT2_SSH_PASSWORD=<robot-key-passphrase> `
    OT2_HTTP_BASE_URL=http://<robot-ip>:31950 `
    OT2_PLATE_STATE_PATH=C:/SDL_State/<id>_state.json `
    OT2_DECK_STATE_PATH=C:/SDL_State/<id>_deck_state.json `
    OT2_TIP_STATE_PATH=C:/SDL_State/<id>_tip_state.json `
    OT2_DRY_RUN=false

nssm start <service>
sc resume <service>          # clear NSSM's spurious SERVICE_PAUSED
```

Set `OT2_HTTP_BASE_URL` even on the SSH transport — the robot probe and the
deck-lights control use the robot's HTTP API in both modes.

Verify the process is alive (no robot contact yet):

```powershell
curl http://127.0.0.1:<port>/          # {equipment_id: <id>, protocol_version: "1.1"}
curl http://127.0.0.1:<port>/status    # equipment_status: requires_init
```

Rollback at any point: `nssm stop <service>` (robot untouched);
`nssm remove <service> confirm` to remove entirely.

---

## 4. Phase A — verify, no motion

```bash
# Claim (control endpoints require X-Claim-Token); capture the token.
TOKEN=$(curl -fsS -X POST localhost:<port>/control/claim \
  -H 'Content-Type: application/json' \
  -d '{"owner":"bringup","session_id":"bring-1","ttl_s":900}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["claim_token"])')
H="-H X-Claim-Token:$TOKEN -H Content-Type:application/json"

# Startup in SIMULATION — connects/validates without motion (SSH transport
# only; the HTTP transport has no simulation mode and this flag is ignored).
curl -fsS $H -X POST localhost:<port>/control/startup -d '{"simulation":true}'
#   PASS: state: ready
curl -fsS localhost:<port>/status | python -m json.tool | grep -E 'equipment_status|pipette'
curl -fsS $H -X POST localhost:<port>/control/release
```

**PASS if** startup returns `ready` in simulation. If it fails, fix the
transport/SSH before any motion.

---

## 5. Phase B/C — the motion test (dry, then wet)

`demo/complexation_dispense_test.py` is the reusable test harness: claim →
startup → setup → home → per-pipette pick-up-tip + (aspirate+dispense)×N →
drop-tip → shutdown → release. **It is parameterized by the `PIPETTES` table
near the top of the script** — edit it to match the new robot's attached
pipettes (mounts, tiprack load-names, volumes within each pipette's range)
and the deck layout in §2's prerequisite. `--robot-url` makes the script
verify the attached pipettes match the table before running anything.

```bash
# 1) Offline preview — no network. Read the step list + volume map.
python demo/complexation_dispense_test.py

# 2) Dry — gateway starts with simulation=true (no motion).
python demo/complexation_dispense_test.py --mode dry \
    --url http://localhost:<port> \
    --robot-url http://<robot-ip>:31950

# 3) Wet — REAL motion. Person at the e-stop. Aspirate source filled.
python demo/complexation_dispense_test.py --mode wet \
    --url http://localhost:<port> --yes-run-on-hardware
```

**PASS if** dry completes every step with 2xx and the pipette-match check is
✅; then wet runs the full sequence without an unplanned stop and the
dispense pattern matches the volume map.

Known harness caveat (from the complexation run): the default volume map
draws its whole gradient from a single source well, which depletes — the wet
run validates **motion**, not liquid accuracy. Tune volumes / add
source-refill before using it quantitatively.

---

## 6. Register on the dashboard

Only **after** the gateway answers on `<port>` — flipping earlier makes the
aggregator poll a dead endpoint and render the tile "unreachable". In
`ac-organic-lab` `equipment.yaml`:

```yaml
  - id: <id>
    name: <name>
    kind: liquid_handler
    adapter: http
    protocol: "1.1"
    base_url: http://sdl2-pc-03-cytation.tail6a1dd7.ts.net:<port>
    status_path: /status
    tailscale_ip: <robot-ip>
    poll_timeout_seconds: 8.0          # /status builds from an SSH snapshot; slower than most
    do_not_call_connect: true          # SDK must never auto-connect the OT-2
    tiles:
      <section>: { w: 2, h: 3 }
    pills: {}
```

Add `<id>` to the appropriate section in `platforms.yaml`, restart
`ac-organic-lab-api.service`, and confirm the tile goes green. The dashboard
tile, full-page control interface (`/equipment/<id>/control`), and skill
catalog are keyed by `kind`/`id` — **no per-robot dashboard code is needed**
(add a short-URL alias page only if wanted).

---

## 7. What about a Flex?

Not covered by this runbook. The SSH control wrapper (`OT2Control`) carries
some Flex-era methods (`load_trash_bin`, gripper `move_labware`) and
`demo/` has Flex experiments, but the **gateway is OT-2-only today**: the
deck model assumes 12 slots, the tip/plate stores and dispense harness are
OT-2-shaped, and the HTTP run-engine transport explicitly excludes the Flex
(its absolute-motion/gripper surface has no run-engine equivalent — see
[`HTTP_TRANSPORT.md`](HTTP_TRANSPORT.md)). Bringing up a Flex is gateway
work, not a bring-up exercise.

---

## 8. Completed bring-ups (records)

### `ot2_hte` — robot `ot2cytation` (100.64.254.90), port 8020

The original instance (service `ot2-gateway`), brought up with the repo
itself; it doubled as the hardware-validation robot for the HTTP transport
(2026-07-14 — [`HTTP_DRIVE_VALIDATION.md`](HTTP_DRIVE_VALIDATION.md)).
Pipettes: left `p300_multi_gen2` (8-ch), right `p1000_single_gen2`.

### `ot2_complexation` — robot `ot2training` (100.64.254.91), port 8021 — COMPLETE (2026-07-14, operator: Cyril Cao)

Standalone (no xArm) Echem-bench robot; gateway deployed as NSSM service
`ot2-gateway-complexation`. Pipettes: left `p300_single_gen2`, right
`p20_multi_gen2` (8-ch). Deck for the test: tipracks in slots 1
(`opentrons_96_tiprack_300ul`) and 2 (`opentrons_96_tiprack_20ul`), plate in
slot 3 (`corning_96_wellplate_360ul_flat`), water in column 12.

- [x] Gateway answers `/`, `/health`, `/status` on :8021.
- [x] Startup succeeds in simulation (via `--mode dry`).
- [x] `--mode dry` completes all 34 steps; pipette-match check passed.
- [x] `--mode wet` completed the full cycle (real motion, no unplanned stop):
      p300 filled column 1 (A1..H1, 25→300 µL), p20 multi filled columns 4–8
      (4→20 µL each).
- [x] `equipment.yaml` `ot2_complexation` on `adapter: http`.
- [x] State files separated (`C:\ProgramData\ot2_complexation\…`).

**Bugs found + fixed during bring-up:** demo-script cp1252 encoding +
30→180 s timeout (`f28cc87`); `tip_length`-on-non-tiprack SSH snapshot 500
(`68c0803`); off-deck `/status` 500 (`ea58a5b`, from the parallel HTTP-drive
work).

**Caveat:** the p300 drew its whole 25→300 µL gradient from A12 alone, so
A12 depleted after the first couple aspirates — motion validated, liquid
accuracy not (harness caveat in §5).

## See also

- `README.md` → *Running multiple OT-2 gateways* — the per-instance env table.
- `deploy/ot2_complexation.env.example` — env template to copy per instance.
- `ac-organic-lab` `docs/DEVICE_PC_SETUP.md` — the host-level uv + NSSM recipe.
- `ac-organic-lab` `docs/EQUIP_GUIDE.md` — dashboard registry / maintenance runbook.
