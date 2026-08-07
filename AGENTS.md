# AGENTS.md — shared agent instructions (`opentrons-server`)

**Read this before proposing or editing anything.** It is the shared,
model-agnostic instruction file for every coding agent working in this repo.
Anything specific to one agent goes in that agent's own file (e.g.
[`CLAUDE.md`](CLAUDE.md)), never here.

This repo layers on the canonical base in
[`ac-organic-lab/AGENTS.md`](../ac-organic-lab/AGENTS.md). Read that first for
the lab-wide picture; this file adds only this repo's specifics and never
weakens anything inherited.

## 1. The binding contract — do not weaken

Three documents are binding and take precedence over everything here. Agents
reference them; they do not restate, reinterpret, or work around them.

- **[`AGENT_RULES.md`](AGENT_RULES.md)** — this repo's rules, which link back to
  the canonical [`ac-organic-lab/docs/AGENT_RULES.md`](../ac-organic-lab/docs/AGENT_RULES.md).
- **[`ac-organic-lab/docs/STATUS_SPEC.md`](../ac-organic-lab/docs/STATUS_SPEC.md)**
  — the device contract this gateway implements. **v1.2**, via the shared
  `sdl-lab-contract` package pinned to tag `v1.2.0`.
- **[`ac-organic-lab/docs/INTERLOCKS.md`](../ac-organic-lab/docs/INTERLOCKS.md)**
  — the four-layer safety model. This repo owns layers 1 and 2 (hardware limits
  in the Pydantic request bodies; the device state machine in
  `gateway/service.py`). Layers 3 and 4 live in `lab-skills` and project repos.

The short list agents most often need:

- **This repo is the device side of the boundary**, not a caller of it. It
  implements `/status` and `/control/*`; it never orchestrates other devices.
- Never report a state the gateway has not observed. `unknown` is the honest
  answer; a convenient `ready` is a contract violation (§2.2).
- Never derive `activity` from `equipment_status` — observe it (§2.3).
- `/status` is side-effect-free. Always.
- When something is irreversible, ambiguous, or uncovered: stop and ask a
  human. The absence of a rule is not permission.

## 2. What this repo is

An AC-conformant REST gateway fronting an **Opentrons OT-2** liquid handler.
Two instances of this one codebase run as separate services against two
different robots.

```
lab-skills / dashboard / agents          this repo                        robot
  ──────────────────────────────▶  gateway/api.py (FastAPI)
                                   gateway/service.py (OT2Service) ──SSH──▶ OT-2 REPL
                                   control/http_control.py        ──HTTP─▶ robot-server :31950
```

- **`src/opentrons_server/gateway/`** — the STATUS_SPEC surface. `api.py`
  (routes, auth, CORS), `service.py` (the state machine and `/status` builder —
  1.8k lines, the heart of the repo), `models.py` (this gateway's domain
  vocabulary + re-exported contract types), `claims.py`, `deck.py`,
  `tip_state.py`, `plate_state.py`, `events_exporter.py`.
- **`src/opentrons_server/control/`** — two interchangeable transports to the
  robot: SSH REPL (`ot2_control.py`) and the run-engine HTTP API
  (`http_control.py` / `http_run.py`). Their parity is a tested invariant; see
  `docs/HTTP_SSH_PARITY.md` and `docs/TRANSPORT_TRADEOFFS.md`.
- **`ui/`** — the operator SPA (React 19 + Vite + Tailwind 4), built into
  `src/opentrons_server/ui_dist/` and shipped inside the wheel. The dashboard
  **frames** this panel rather than reimplementing it, so it is the operator
  surface for both robots.
- **`docs/`** — start at `docs/DEVICE_BRINGUP.md` to bring a robot up,
  `docs/DECK_STATE.md` for the normalized deck model.

## 3. Working conventions

- **Environment: `uv`**, on Windows. From WSL the binary is
  `/mnt/c/SDL_Tools/uv.exe`; there is no `uv` on the WSL `PATH`.
- **Extras matter.** `uv sync --extra labware` — a plain `uv sync` **strips**
  `opentrons-shared-data`, which silently empties the `/labware` catalog and
  the UI's deck-declare picker. Same class of trap as the Cytation's
  `--extra plr`.
- **Tests: use `.venv.test`, not `.venv`.**
  `./.venv.test/Scripts/python.exe -m pytest tests/unit -q` — 400 tests, no
  hardware, a couple of minutes. `.venv/` is the **running services'**
  environment; syncing or installing into it can disturb a live gateway (§4).
- **Never actuate hardware to check a change.** Everything in `tests/unit/` runs
  against `dry_run=True`, mocks, and `tests/fixtures/status_*.json`. Add a
  fixture rather than reaching for a robot.
- **Live testing happens on Complexation only**, through the edge-gated panel
  at `http://100.64.254.6/ot2/complexation/ui/`. **Never HTE** (`ot2_hte`,
  :8020) — it runs real campaigns. This applies to any hands-on check: manual
  clicks, `curl` against `/control/*`, bench acceptance. The two gateways share
  a checkout, so a change is live for both; the *testing* is not.
- **UI:** `cd ui && npm run typecheck` / `npm run build`. The build must be
  committed as `ui_dist/` for the wheel to serve `/ui`.
- **Fail-fast style.** Do not add defensive code that swallows exceptions and
  hides failures — on this device a swallowed error becomes a robot whose state
  nobody can trust. Report truthfully.
- **Prefer reading source in `.venv/Lib/site-packages/`** over searching online
  for a dependency's usage (`sdl_lab_contract`, `paramiko`, `opentrons`).

## 4. Recurring pitfalls (project-specific)

- **One checkout, one venv, two robots.** The `ot2-gateway` (:8020, HTE) and
  `ot2-gateway-complexation` (:8021, Complexation) NSSM services run from *this
  same working tree and `.venv/`*. Every edit is a two-robot edit; every
  instance-specific value is an env var (`OT2_EQUIPMENT_ID`, `OT2_HOST_ALIAS`,
  and **distinct** `OT2_PLATE_STATE_PATH` / `OT2_DECK_STATE_PATH` /
  `OT2_TIP_STATE_PATH` — shared state files corrupt each other).
- **`uv sync` can break a running service.** If a release adds or bumps a
  dependency, uv must replace the console-script `.exe` the running service
  holds open, aborts the whole transaction on `os error 32`, and can leave the
  new dependency **not installed at all** while the service keeps running off
  memory-resident code. Stop only that service first, sync, start. Full
  recovery notes in
  [`DEVICE_PC_SETUP.md`](../ac-organic-lab/docs/DEVICE_PC_SETUP.md) §8.
- **Elevation and cache ACLs.** Never run `uv sync` from an elevated shell — it
  poisons the uv cache's ACLs for the service accounts. UAC prompts only appear
  in an RDP session, so an elevated command from a headless shell simply hangs.
- **Python version is pinned by the labware extra.** `opentrons-shared-data`
  pulls `numpy~=1.26.4`, which has no wheels past cp312. Do not bulk-upgrade
  this venv's Python.
- **Single-PC concentration.** xArm (8000), PlateLoc (8010), both OT-2 gateways
  (8020/8021), and Cytation 5 (8040) all live on `sdl2-pc-03-cytation`. One
  reboot takes out five workflow-critical services, and the USB-enumeration
  race on boot is a known failure mode for the serial devices.
- **The SSH REPL breaks on the first `>>>` prompt.** Snapshot reads are sent as
  two separate `invoke`s routed through `compile()`/`exec()` for exactly this
  reason (`service.py` `_REMOTE_SNAPSHOT_*`). Do not "simplify" them into one
  multi-statement send.
- **`equipment_version` is the gateway's, not the robot's.** The robot's
  `api_version` lives in `details.robot`. These were conflated until
  2026-08-07; stored history before that date has the robot's number.

## 5. Memory & instruction policy

Inherited from the canonical base; the scope boundaries for this repo:

- **`AGENTS.md` (this file)** — durable, model-agnostic repo knowledge:
  conventions, commands, architecture facts, recurring pitfalls. When you learn
  one, update this file.
- **`AGENT_RULES.md`** — binding rules; changes only when a human asks.
- **`CLAUDE.md`** — Claude-Code-specific only. Nothing another agent needs.
- **Cross-repo or device-PC-wide facts** (the shared `sdl2-pc-03` layout, uv/
  NSSM behaviour, other repos' roles) belong in the agent's **global** memory,
  *proposed for approval* — not written into this repo.
- **Never** commit temporary debugging notes, stale TODOs, or one-off
  observations to any instruction file.

## 6. Safety protocol for edits outside this repo

Before editing anything outside this repository — another device repo,
`../ac-organic-lab`, `~/.claude`, service configs on the device PC — first show
the human the exact path, the reason, the proposed change, and whether it
affects only this repo or future global behavior. **Do not proceed until they
approve.** In particular, `../ac-organic-lab` is the central server's repo
mirrored here: contract changes are preferably made there, and any edit from
this PC must be coordinated so the two do not diverge.
