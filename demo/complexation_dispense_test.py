#!/usr/bin/env python3
"""Complexation OT-2 — pick-up-tip + variable-volume dispense test.

Drives the ``opentrons-server`` STATUS_SPEC gateway (the ``/control/*`` surface)
step-by-step for the **ot2_complexation** robot ("ot2training", Tailscale
100.64.254.91), standalone — no xArm handoff. It exercises BOTH installed
pipettes and dispenses a gradient of different volumes into a 96-well plate.

Live pipette config on this machine (read from the robot's own API):

    left  = p300_single_gen2   (single channel, 20–300 µL)
    right = p20_multi_gen2      (8 channel,       1–20  µL)

The 8-channel pipette addresses a whole COLUMN at once: dispensing to well
``A4`` actually dispenses to A4..H4. So its "different volumes" are per-column,
addressed by the top-row well; the single-channel's are per individual well.

Each dispense is paired with a matching aspirate from a source column, so the
run engine's per-tip volume tracking stays valid (a dispense with an empty tip
would otherwise error). Source liquid need not physically exist in dry mode.

Modes (``--mode``):

    plan   (default)  Offline. Prints the full step sequence + per-well volume
                      map and makes NO network calls. Safe to run anywhere —
                      this is the "dry test first" you can eyeball before the
                      robot is involved at all.
    dry               Hits the gateway but starts the run with simulation=true,
                      so the robot analyses/simulates without moving liquid.
                      Requires the gateway to be up and reachable.
    wet               Real motion. Requires --yes-run-on-hardware and a person
                      at the e-stop.

Usage:

    # 1) offline preview (default) — run this first
    python demo/complexation_dispense_test.py

    # 2) against the gateway in simulation (once the gateway is deployed)
    python demo/complexation_dispense_test.py --mode dry \
        --url http://sdl2-pc-03-cytation.tail6a1dd7.ts.net:8021

    # 3) real run, human at the e-stop
    python demo/complexation_dispense_test.py --mode wet \
        --url http://...:8021 --yes-run-on-hardware

The gateway for ot2_complexation is not deployed yet (equipment.yaml marks it
``adapter: mock``). Per DEVICE_PC_SETUP / equipment.yaml the plan is an
``opentrons-server`` instance on port 8021 pointed at 100.64.254.91; ``--url``
defaults to that. Adjust to wherever you bring it up.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests

# The device PC's console defaults to cp1252, which can't encode the status
# glyphs (✅ / ❌ / →) this script prints — an unhandled UnicodeEncodeError would
# otherwise abort the run mid-test on Windows. Force utf-8 output.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pre-3.7 or already-wrapped stream
        pass

# --------------------------------------------------------------------------- #
# Configuration — edit here if the machine changes. The dry/wet preflight also
# verifies these against the robot's live /pipettes when --robot-url is given.
# --------------------------------------------------------------------------- #

DEFAULT_GATEWAY_URL = "http://sdl2-pc-03-cytation.tail6a1dd7.ts.net:8021"

# nickname -> load config. Nicknames are what every /control/* call references.
PLATE = {"nickname": "plate", "loadname": "corning_96_wellplate_360ul_flat", "location": "3"}

# Column used as the (nominal) aspirate source so each dispense has liquid to
# move. Kept clear of every dispense target below.
SOURCE_COLUMN = 12  # wells A12..H12

PIPETTES: List[Dict[str, Any]] = [
    {
        "nickname": "p300",
        "instrument_name": "p300_single_gen2",
        "mount": "left",
        "channels": 1,
        "min_ul": 20.0,
        "max_ul": 300.0,
        "tiprack": {"nickname": "tips_300", "loadname": "opentrons_96_tiprack_300ul", "location": "1"},
        # Single channel: one target well per volume, down column 1.
        # (well, volume_ul)
        "dispenses": [
            ("A1", 25.0),
            ("B1", 50.0),
            ("C1", 75.0),
            ("D1", 100.0),
            ("E1", 150.0),
            ("F1", 200.0),
            ("G1", 250.0),
            ("H1", 300.0),
        ],
    },
    {
        "nickname": "p20",
        "instrument_name": "p20_multi_gen2",
        "mount": "right",
        "channels": 8,
        "min_ul": 1.0,
        "max_ul": 20.0,
        "tiprack": {"nickname": "tips_20", "loadname": "opentrons_96_tiprack_20ul", "location": "2"},
        # 8-channel: addressed by the top-row well; each entry fills a full
        # column. Columns 4–8, kept clear of column 1 (single) and 12 (source).
        "dispenses": [
            ("A4", 4.0),
            ("A5", 8.0),
            ("A6", 12.0),
            ("A7", 16.0),
            ("A8", 20.0),
        ],
    },
]

ASPIRATE_BOTTOM_MM = 2.0  # aspirate 2 mm off the source well bottom
DISPENSE_BOTTOM_MM = 2.0  # dispense 2 mm off the target well bottom
CLAIM_TTL_S = 900.0
REQUEST_TIMEOUT_S = 180.0  # SSH startup/setup (REPL bring-up) can take >100 s


# --------------------------------------------------------------------------- #
# Plan building — a flat, ordered list of labelled API steps.
# --------------------------------------------------------------------------- #

Step = Tuple[str, str, str, Optional[Dict[str, Any]]]  # (label, method, path, json_body)


def _source_well(pipette: Dict[str, Any]) -> str:
    # 8-channel aspirates a whole column via its top-row well; single uses A of
    # the source column too (a single well is fine as a nominal source).
    return f"A{SOURCE_COLUMN}"


def build_setup_payload() -> Dict[str, Any]:
    labware = [PLATE] + [p["tiprack"] for p in PIPETTES]
    instruments = [
        {
            "ot_default": True,
            "nickname": p["nickname"],
            "instrument_name": p["instrument_name"],
            "mount": p["mount"],
        }
        for p in PIPETTES
    ]
    return {
        "labware": [
            {"ot_default": True, "nickname": lw["nickname"], "loadname": lw["loadname"], "location": lw["location"]}
            for lw in labware
        ],
        "instruments": instruments,
        "modules": [],
    }


def build_steps(*, simulation: bool) -> List[Step]:
    """The ordered control-plane calls (claim/release handled separately)."""
    steps: List[Step] = []
    steps.append(("startup", "POST", "/control/startup", {"simulation": simulation}))
    steps.append(("setup (load pipettes + tipracks + plate)", "POST", "/control/setup", build_setup_payload()))
    steps.append(("home (safe pose)", "POST", "/control/home", None))

    for p in PIPETTES:
        nick = p["nickname"]
        src = _source_well(p)
        span = "whole column" if p["channels"] > 1 else "single well"
        steps.append(
            (
                f"{nick}: pick up tip @ {p['tiprack']['nickname']} A1",
                "POST",
                "/control/pick-up-tip",
                {"pipette": nick, "labware_nickname": p["tiprack"]["nickname"], "position": "A1"},
            )
        )
        for well, vol in p["dispenses"]:
            steps.append(
                (
                    f"{nick}: aspirate {vol:g} µL from {PLATE['nickname']} {src}",
                    "POST",
                    "/control/aspirate",
                    {
                        "pipette": nick,
                        "volume_ul": vol,
                        "location": {"labware_nickname": PLATE["nickname"], "position": src, "bottom": ASPIRATE_BOTTOM_MM},
                    },
                )
            )
            steps.append(
                (
                    f"{nick}: dispense {vol:g} µL into {PLATE['nickname']} {well} ({span})",
                    "POST",
                    "/control/dispense",
                    {
                        "pipette": nick,
                        "volume_ul": vol,
                        "location": {"labware_nickname": PLATE["nickname"], "position": well, "bottom": DISPENSE_BOTTOM_MM},
                    },
                )
            )
        steps.append((f"{nick}: drop tip", "POST", "/control/drop-tip", {"pipette": nick}))

    steps.append(("shutdown", "POST", "/control/shutdown", None))
    return steps


# --------------------------------------------------------------------------- #
# plan mode — print everything, no network
# --------------------------------------------------------------------------- #


def _volume_map_lines() -> List[str]:
    lines: List[str] = []
    for p in PIPETTES:
        vols = ", ".join(f"{w}={v:g}µL" for w, v in p["dispenses"])
        rng = f"{p['min_ul']:g}-{p['max_ul']:g}µL"
        chan = f"{p['channels']}ch"
        lines.append(f"  {p['nickname']:5s} {p['instrument_name']:18s} ({chan}, {rng}) -> {vols}")
        if p["channels"] > 1:
            lines.append("        (8-channel: each well above fills that whole column A..H)")
    return lines


def print_plan(simulation_note: str) -> None:
    print("=" * 74)
    print("Complexation OT-2 — pick-up-tip + variable-volume dispense test")
    print("=" * 74)
    print("\nDeck layout:")
    print(f"  slot {PIPETTES[0]['tiprack']['location']}: {PIPETTES[0]['tiprack']['loadname']}  ({PIPETTES[0]['tiprack']['nickname']})")
    print(f"  slot {PIPETTES[1]['tiprack']['location']}: {PIPETTES[1]['tiprack']['loadname']}   ({PIPETTES[1]['tiprack']['nickname']})")
    print(f"  slot {PLATE['location']}: {PLATE['loadname']}  ({PLATE['nickname']})")
    print(f"  aspirate source: {PLATE['nickname']} column {SOURCE_COLUMN} (A{SOURCE_COLUMN})")
    print("\nPipettes & per-well volumes:")
    for line in _volume_map_lines():
        print(line)
    print(f"\n{simulation_note}")
    print("\nAPI call sequence (claim first, release last):")
    print("   ->  POST /control/claim   (X-Claim-Token used on every call below)")
    for i, (label, method, path, body) in enumerate(build_steps(simulation=True), start=1):
        print(f"  {i:2d}.  {method} {path:24s} {label}")
    print("   ->  POST /control/release")
    total = sum(len(p["dispenses"]) for p in PIPETTES)
    print(f"\nTotals: {len(PIPETTES)} pipettes, {total} dispenses, {total} distinct aspirate+dispense pairs.")
    print("=" * 74)


# --------------------------------------------------------------------------- #
# dry / wet mode — drive the gateway
# --------------------------------------------------------------------------- #


class GatewayError(RuntimeError):
    pass


def _pp_body(resp: requests.Response) -> str:
    try:
        return str(resp.json())
    except ValueError:
        return (resp.text or "").strip()[:300]


def check_pipettes(robot_url: str) -> None:
    """Best-effort: verify the configured pipettes match what's attached."""
    url = robot_url.rstrip("/") + "/pipettes"
    try:
        data = requests.get(url, headers={"Opentrons-Version": "3"}, timeout=REQUEST_TIMEOUT_S).json()
    except Exception as exc:  # noqa: BLE001 — best effort
        print(f"  ! could not read {url} ({exc}); skipping pipette verification")
        return
    attached = {m: (data.get(m) or {}).get("name") for m in ("left", "right")}
    print(f"  attached pipettes: {attached}")
    for p in PIPETTES:
        if attached.get(p["mount"]) != p["instrument_name"]:
            raise GatewayError(
                f"pipette mismatch on {p['mount']}: config expects {p['instrument_name']!r}, "
                f"robot reports {attached.get(p['mount'])!r}. Update PIPETTES and re-run."
            )
    print("  pipette config matches the robot. ✅")


def run_against_gateway(url: str, *, simulation: bool, robot_url: Optional[str]) -> int:
    base = url.rstrip("/")
    session = requests.Session()

    # Probe (no claim needed).
    try:
        root = session.get(base + "/", timeout=REQUEST_TIMEOUT_S).json()
        status = session.get(base + "/status", timeout=REQUEST_TIMEOUT_S).json()
    except Exception as exc:  # noqa: BLE001
        print(f"❌ cannot reach gateway at {base}: {exc}")
        return 2
    print(f"gateway: protocol_version={root.get('protocol_version')} "
          f"equipment_status={status.get('equipment_status')}")

    if robot_url:
        check_pipettes(robot_url)

    # Claim.
    claim_body = {"owner": "complexation-dispense-test", "session_id": str(uuid.uuid4()), "ttl_s": CLAIM_TTL_S}
    resp = session.post(base + "/control/claim", json=claim_body, timeout=REQUEST_TIMEOUT_S)
    if resp.status_code != 200:
        print(f"❌ claim rejected ({resp.status_code}): {_pp_body(resp)}")
        return 2
    token = resp.json()["claim_token"]
    headers = {"X-Claim-Token": token}
    print(f"claim acquired (token {token[:8]}…)")

    steps = build_steps(simulation=simulation)
    exit_code = 0
    try:
        for i, (label, method, path, body) in enumerate(steps, start=1):
            print(f"[{i:2d}/{len(steps)}] {label}")
            resp = session.request(
                method, base + path, json=body, headers=headers, timeout=REQUEST_TIMEOUT_S
            )
            if resp.status_code >= 400:
                # 412 precondition / 423 claim / 5xx device — surface the body.
                print(f"    ❌ {resp.status_code}: {_pp_body(resp)}")
                raise GatewayError(f"step {i} ({label}) failed with {resp.status_code}")
            print(f"    ok: {_pp_body(resp)}")
    except GatewayError as exc:
        print(f"\n❌ aborting: {exc}")
        exit_code = 1
    finally:
        # Best-effort release; never blocks the operator from moving on.
        try:
            session.post(base + "/control/release", headers=headers, timeout=REQUEST_TIMEOUT_S)
            print("claim released")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! release failed (harmless): {exc}")
    return exit_code


# --------------------------------------------------------------------------- #


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["plan", "dry", "wet"], default="plan",
                        help="plan=offline preview (default); dry=gateway w/ simulation; wet=real motion")
    parser.add_argument("--url", default=DEFAULT_GATEWAY_URL, help="gateway base URL")
    parser.add_argument("--robot-url", default=None,
                        help="optional robot HTTP API base (e.g. http://100.64.254.91:31950) for pipette verification")
    parser.add_argument("--yes-run-on-hardware", action="store_true",
                        help="required confirmation for --mode wet")
    args = parser.parse_args(argv)

    if args.mode == "plan":
        print_plan("MODE: plan (offline preview — no network calls made).")
        return 0

    if args.mode == "wet" and not args.yes_run_on_hardware:
        print("Refusing --mode wet without --yes-run-on-hardware. "
              "A person must be at the e-stop. Run --mode dry first.")
        return 2

    simulation = args.mode == "dry"
    banner = "MODE: dry (robot simulates; no liquid moved)." if simulation else "MODE: WET — real motion."
    print(banner)
    return run_against_gateway(args.url, simulation=simulation, robot_url=args.robot_url)


if __name__ == "__main__":
    sys.exit(main())
