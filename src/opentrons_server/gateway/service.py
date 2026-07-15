"""Runtime service and state machine for the OT-2 gateway."""

from __future__ import annotations

import inspect
import json
import os
import socket
import threading
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Optional

import paramiko
import requests

from ..control import OT2Control, OT2HttpControl, RunEngineClient
from ..control import state_readers as _state_readers
from .claims import ClaimManager
from .deck import (
    SLOTS,
    DeckDeclarationStore,
    build_deck,
    make_slot_labware,
    normalize_repl_slots,
    normalize_run_slots,
)
from .models import (
    ComponentStatus,
    EquipmentStatus,
    ErrorInfo,
    LoadedPlate,
    SlotLabware,
    WellSample,
)
from .plate_state import PlateStateStore

# Snapshot is run on the OT-2's Python REPL in two invokes. The OT-2 runs
# only the official Opentrons SDK — `opentrons_server` is NOT installed
# there by design — so we ship the reader source over the wire and exec()
# it as a single string literal. state_readers has zero imports from
# opentrons_server, so the source is self-contained against the Opentrons
# SDK + stdlib.
#
# Two invokes (not one multi-statement send) because the SSH REPL reader
# breaks on the FIRST `>>>` prompt; sending the exec + print together would
# capture only the exec's empty output. The exec defines `get_all_states`
# in the REPL's module globals so the second invoke can call it.
# (Blank lines inside function bodies break a naïve paste of the source
# directly into the REPL — interactive mode treats blank as end-of-compound
# — so we route the source through compile()/exec() to bypass that quirk.)
_REMOTE_SNAPSHOT_DEFS = f"exec({inspect.getsource(_state_readers)!r})"
_REMOTE_SNAPSHOT_CALL = "import json; print(json.dumps(get_all_states(protocol), default=str))"

# Deck (rail) lights are driven through the robot's own Opentrons HTTP API
# (GET/POST /robot/lights), not the SSH REPL: it is a stateless, side-effect
# -free read on the GET path, so a direct HTTP call avoids contending with
# the shared REPL session that snapshot reads and protocol commands use. The
# robot host is the same one SSH already reaches; the HTTP server listens on
# port 31950 and requires an Opentrons-Version header. Both are overridable
# for non-standard deployments.
_OT2_HTTP_PORT = os.getenv("OT2_HTTP_PORT", "31950")
_OT2_HTTP_TIMEOUT = float(os.getenv("OT2_HTTP_TIMEOUT", "2.0"))
_OPENTRONS_HTTP_HEADERS = {"Opentrons-Version": "3"}

# Minimum spacing between robot-server run-labware probes. The probe is best-
# effort and NEVER runs inside the /status handler (which stays side-effect-free
# and uses only the cached `_last_run_labware`); it is refreshed on boot and on
# startup. The TTL guards against hammering the robot if refresh points cluster.
_OT2_DECK_PROBE_TTL = float(os.getenv("OT2_DECK_PROBE_TTL", "3.0"))

# How often the optional background thread refreshes the external-run probe so an
# EXTERNAL_CONTROL deck stays fresh without the (side-effect-free) /status handler
# ever issuing HTTP. Only runs when auto_reconnect is on and not in dry-run.
_OT2_RUN_REFRESH_INTERVAL = float(os.getenv("OT2_RUN_REFRESH_INTERVAL", "5.0"))


class OT2ServiceState(str, Enum):
    REQUIRES_INIT = "requires_init"
    CONNECTING = "connecting"
    READY = "ready"
    BUSY = "busy"
    PAUSED = "paused"
    DRY_RUN = "dry_run"
    ERROR = "error"
    UNKNOWN_OUTCOME = "unknown_outcome"
    # Robot reachable over HTTP but a run is active outside this gateway
    # (e.g. started in the official Opentrons app). We deliberately do NOT
    # take the REPL control plane in this state — see boot_reconnect().
    EXTERNAL_CONTROL = "external_control"


class UnknownOutcomeError(RuntimeError):
    """Raised when transport failed during a non-idempotent operation."""


class OT2Service:
    """Owns one OT-2 connection and exposes safe gateway operations."""

    non_idempotent_actions = {
        "aspirate",
        "dispense",
        "pick_up_tip",
        "drop_tip",
        "move_labware",
    }

    def __init__(
        self,
        *,
        equipment_id: str = "ot2",
        equipment_name: str = "Opentrons OT-2",
        host_alias: Optional[str] = None,
        password: str = "",
        dry_run: bool = False,
        simulation: bool = False,
        plates: Optional[PlateStateStore] = None,
        decks: Optional[DeckDeclarationStore] = None,
        transport: Optional[str] = None,
    ) -> None:
        self.equipment_id = equipment_id
        self.equipment_name = equipment_name
        self.host_alias = host_alias
        self.password = password
        self.dry_run = dry_run
        self.simulation = simulation
        # Control-plane transport: "ssh" (default, the SSH REPL) or "http" (the
        # robot-server run engine, docs/HTTP_DRIVE_PLAN.md). Opt-in and fully
        # reversible: unset OT2_TRANSPORT (or pass transport="ssh") to restore the
        # SSH path with zero behaviour change. HTTP is not yet robot-validated.
        self.transport = (transport or os.getenv("OT2_TRANSPORT") or "ssh").lower()
        # Orchestrator-owned plate/well tracking, persisted across restarts.
        # Mirrors the Cytation contract so a plate round-trips across devices.
        self.plates = (
            plates if plates is not None else PlateStateStore(state_path="./ot2_state.json")
        )
        # Operator/recipe-declared deck layout, persisted across restarts. The
        # source of truth that retires the dashboard's deck_layouts.json stopgap.
        self.decks = (
            decks if decks is not None else DeckDeclarationStore(state_path="./ot2_deck_state.json")
        )
        self._refresh_stop = threading.Event()
        self.started_at = time.monotonic()
        self.state = OT2ServiceState.DRY_RUN if dry_run else OT2ServiceState.REQUIRES_INIT
        # Either an OT2Control (SSH) or an OT2HttpControl (run engine); both expose
        # the same method surface the service calls.
        self.control: Optional[Any] = None
        self.claims = ClaimManager()
        self.last_error: Optional[ErrorInfo] = None
        self._dry_run_lights_on = False
        self.equipment_version: Optional[str] = None
        self._last_probe: Dict[str, Any] = {}
        # Cached labware of an active *external* robot-server run (EXTERNAL_CONTROL).
        # None while the gateway owns the REPL (deck then comes from last_snapshot).
        self._last_run_labware: Optional[Dict[str, Any]] = None
        self._last_run_labware_at: float = 0.0
        self._status_note: Optional[str] = None
        self._boot_started = False
        self.last_snapshot: Dict[str, Any] = self._empty_snapshot()
        self.session_recipe: Dict[str, Any] = {
            "labware": [],
            "instruments": [],
            "modules": [],
        }

    def startup(
        self,
        *,
        host_alias: Optional[str] = None,
        password: Optional[str] = None,
        simulation: Optional[bool] = None,
    ) -> None:
        """Connect and initialize the remote protocol session."""

        if self.dry_run:
            self.state = OT2ServiceState.DRY_RUN
            self.last_error = None
            return

        self.state = OT2ServiceState.CONNECTING
        self.host_alias = host_alias or self.host_alias
        # Truthy check: an empty-string `password` in the request body
        # is treated as "no opinion" and falls through to the env-var
        # default set at __init__. This keeps device secrets in the
        # gateway's service config and out of workflow repos.
        if password:
            self.password = password
        if simulation is not None:
            self.simulation = simulation

        try:
            if self.transport == "http":
                self.control = self._connect_http()
            else:
                self.control = OT2Control(
                    host_alias=self.host_alias,
                    password=self.password,
                    simulation=self.simulation,
                )
            self.state = OT2ServiceState.READY
            self.last_error = None
            self._status_note = None
            self.refresh_snapshot()
            self._refresh_identity()
        except Exception as exc:
            self._set_error("startup_failed", str(exc), severity="error")
            raise

    def _connect_http(self) -> OT2HttpControl:
        """Build the run-engine control backend and create the (unplayed) run.

        Reuses ``_probe_base_url()`` (``OT2_HTTP_BASE_URL`` or the configured host)
        so the same address the boot probe already uses drives control.
        """
        base_url = self._probe_base_url()
        if not base_url:
            raise RuntimeError(
                "http transport requires OT2_HTTP_BASE_URL or a configured host_alias"
            )
        control = OT2HttpControl(RunEngineClient(base_url))
        control.initialize_protocol(simulation=self.simulation)
        return control

    def shutdown(self) -> None:
        """Close the robot session and return to requires-init."""

        if self.control is not None:
            try:
                self.control.shutdown()
            finally:
                self.control = None
        self.claims.force_clear()
        self.state = OT2ServiceState.DRY_RUN if self.dry_run else OT2ServiceState.REQUIRES_INIT

    def setup_protocol(self, setup: Dict[str, Any]) -> None:
        self.session_recipe = {
            "labware": list(setup.get("labware", [])),
            "instruments": list(setup.get("instruments", [])),
            "modules": list(setup.get("modules", [])),
        }

        def _setup() -> None:
            if self.control is None:
                raise RuntimeError("OT-2 is not initialized")
            self.control.setup_protocol(**self.session_recipe)

        self._run_action("setup", _setup, idempotent=True)

    def home(self) -> None:
        self._run_action("home", lambda: self._require_control().home(), idempotent=True)

    def pause(self) -> None:
        self._run_action("pause", lambda: self._require_control().pause(), idempotent=True)
        self.state = OT2ServiceState.PAUSED

    def resume(self) -> None:
        self._run_action("resume", lambda: self._require_control().resume(), idempotent=True)
        self.state = OT2ServiceState.READY

    def set_location_from_well(self, request: Any) -> None:
        self._require_control().get_location_from_labware(
            request.location.labware_nickname,
            request.location.position,
            top=request.location.top or 0,
            bottom=request.location.bottom or 0,
            center=1 if request.location.center else 0,
        )

    def aspirate(self, request: Any) -> None:
        flow_rate = getattr(request, "flow_rate", None)

        def _aspirate() -> None:
            self.set_location_from_well(request)
            self._require_control().aspirate(
                request.pipette, request.volume_ul, flow_rate=flow_rate
            )

        self._run_action("aspirate", _aspirate, idempotent=False)

    def dispense(self, request: Any) -> None:
        flow_rate = getattr(request, "flow_rate", None)

        def _dispense() -> None:
            self.set_location_from_well(request)
            self._require_control().dispense(
                request.pipette, request.volume_ul, flow_rate=flow_rate
            )

        self._run_action("dispense", _dispense, idempotent=False)

    def pick_up_tip(self, request: Any) -> None:
        def _pick_up_tip() -> None:
            if request.labware_nickname and request.position:
                self._require_control().get_location_from_labware(
                    request.labware_nickname,
                    request.position,
                )
            self._require_control().pick_up_tip(request.pipette)

        self._run_action("pick_up_tip", _pick_up_tip, idempotent=False)

    def drop_tip(self, request: Any) -> None:
        def _drop_tip() -> None:
            # Optional explicit drop location (e.g. a loaded trash labware). When
            # given, HTTP drops into that well; when omitted, HTTP drops in place
            # and SSH auto-routes to the fixed trash. Mirrors pick_up_tip.
            if request.labware_nickname and request.position:
                self._require_control().get_location_from_labware(
                    request.labware_nickname,
                    request.position,
                )
            self._require_control().drop_tip(request.pipette)

        self._run_action("drop_tip", _drop_tip, idempotent=False)

    def move_labware(self, request: Any) -> None:
        self._run_action(
            "move_labware",
            lambda: self._require_control().move_labware(
                request.labware_nickname,
                request.new_location,
            ),
            idempotent=False,
        )

    # ---- plate / well tracking (orchestrator-owned bookkeeping) --------
    #
    # These mutate persisted metadata only; they do not drive the robot, so
    # they bypass the BUSY state machine in _run_action and work in any state
    # (including dry-run). Claim enforcement still applies at the API layer.

    def load_plate(
        self,
        *,
        plate_id: str,
        model: str,
        wells: Optional[list[WellSample]] = None,
    ) -> LoadedPlate:
        return self.plates.load_plate(plate_id=plate_id, model=model, wells=wells)

    def unload_plate(self) -> Optional[LoadedPlate]:
        return self.plates.unload_plate()

    def update_well(self, well: str, **kwargs: Any) -> WellSample:
        return self.plates.update_well(well, **kwargs)

    def _robot_lights_url(self) -> Optional[str]:
        """Resolve the robot's /robot/lights HTTP endpoint, or None if unknown.

        Prefers an explicit ``OT2_HTTP_BASE_URL`` override; otherwise reuses
        the hostname the SSH transport already resolved. Returns None when no
        session is connected so callers can report the lights as unreachable
        without attempting a doomed request.
        """

        base = os.getenv("OT2_HTTP_BASE_URL")
        if base:
            return base.rstrip("/") + "/robot/lights"
        if self.control is None:
            return None
        host = getattr(self.control.client, "hostname", None)
        if not host:
            return None
        return f"http://{host}:{_OT2_HTTP_PORT}/robot/lights"

    def get_lights(self) -> Optional[bool]:
        """Return the deck-light state, or None when it cannot be determined.

        Side-effect-free: a GET against the robot's HTTP API. In dry-run mode
        the in-memory simulated state is returned.
        """

        if self.dry_run:
            return self._dry_run_lights_on
        url = self._robot_lights_url()
        if url is None:
            return None
        response = requests.get(url, headers=_OPENTRONS_HTTP_HEADERS, timeout=_OT2_HTTP_TIMEOUT)
        response.raise_for_status()
        return bool(response.json().get("on"))

    def set_lights(self, on: bool) -> bool:
        """Set the deck lights via the robot's HTTP API; return the new state."""

        if self.dry_run:
            self._dry_run_lights_on = on
            return on
        url = self._robot_lights_url()
        if url is None:
            raise RuntimeError("OT-2 is not initialized; POST /control/startup first")
        response = requests.post(
            url, json={"on": on}, headers=_OPENTRONS_HTTP_HEADERS, timeout=_OT2_HTTP_TIMEOUT
        )
        response.raise_for_status()
        return bool(response.json().get("on", on))

    def _lights_component(self) -> ComponentStatus:
        """Build the ``lights`` component, tolerating an unreachable robot.

        Never raises: a failed read is reported as ``unknown``/disconnected so
        ``/status`` stays side-effect-free and always returns 200.
        """

        try:
            on = self.get_lights()
        except Exception:
            on = None
        if on is None:
            return ComponentStatus(connected=False, state="unknown")
        return ComponentStatus(connected=True, state="on" if on else "off")

    def _probe_base_url(self) -> Optional[str]:
        """Resolve the robot's HTTP API base, usable *before* a session exists.

        Unlike the lights path, this falls back to the configured host alias so
        the boot probe can run while ``self.control`` is still None.
        """

        base = os.getenv("OT2_HTTP_BASE_URL")
        if base:
            return base.rstrip("/")
        host = getattr(self.control.client, "hostname", None) if self.control is not None else None
        host = host or self.host_alias
        if not host:
            return None
        return f"http://{host}:{_OT2_HTTP_PORT}"

    def probe_robot(self) -> Dict[str, Any]:
        """Read-only probe of the robot-server HTTP API. Never raises.

        Derives reachability, identity (api/fw version, model, name), whether a
        run is active outside this gateway, and the attached instruments and
        modules — all durable, session-independent state the SSH/REPL plane
        cannot give us. Surfaces on ``/status`` as ``details.robot`` (see
        ``get_status``), so the dashboard can show/constrain module setup from
        what is physically attached.
        """

        result: Dict[str, Any] = {
            "reachable": False,
            "run_active": False,
            "api_version": None,
            "fw_version": None,
            "robot_model": None,
            "robot_name": None,
            "instruments": [],
            "modules": [],
        }
        base = self._probe_base_url()
        if base is None:
            return result
        try:
            resp = requests.get(
                base + "/health", headers=_OPENTRONS_HTTP_HEADERS, timeout=_OT2_HTTP_TIMEOUT
            )
            resp.raise_for_status()
            health = resp.json()
        except Exception:
            return result  # unreachable
        result["reachable"] = True
        result["api_version"] = health.get("api_version")
        result["fw_version"] = health.get("fw_version")
        result["robot_model"] = health.get("robot_model")
        result["robot_name"] = health.get("name")
        # Best-effort extras: a failure here must not flip reachability.
        try:
            runs = requests.get(
                base + "/runs", headers=_OPENTRONS_HTTP_HEADERS, timeout=_OT2_HTTP_TIMEOUT
            ).json()
            result["run_active"] = any(
                r.get("current") and r.get("status") not in {"succeeded", "failed", "stopped"}
                for r in runs.get("data", []) or []
            )
        except Exception:
            pass
        try:
            instruments = requests.get(
                base + "/instruments", headers=_OPENTRONS_HTTP_HEADERS, timeout=_OT2_HTTP_TIMEOUT
            ).json()
            result["instruments"] = [
                {
                    "mount": d.get("mount"),
                    "model": d.get("instrumentModel"),
                    "name": d.get("instrumentName"),
                    "channels": (d.get("data") or {}).get("channels"),
                }
                for d in instruments.get("data", []) or []
            ]
        except Exception:
            pass
        try:
            modules = requests.get(
                base + "/modules", headers=_OPENTRONS_HTTP_HEADERS, timeout=_OT2_HTTP_TIMEOUT
            ).json()
            result["modules"] = [
                {
                    "model": m.get("moduleModel"),
                    "type": m.get("moduleType"),
                    "serial": m.get("serialNumber"),
                    "id": m.get("id"),
                    # Live per-module telemetry from the robot-server — available
                    # whenever the module is powered, independent of any run, so
                    # the dashboard can show the reading before/after an experiment.
                    "status": (m.get("data") or {}).get("status"),
                    "current_temperature": (m.get("data") or {}).get("currentTemperature"),
                    "target_temperature": (m.get("data") or {}).get("targetTemperature"),
                }
                for m in modules.get("data", []) or []
            ]
        except Exception:
            pass
        return result

    def probe_run_labware(self) -> Optional[Dict[str, Any]]:
        """Read the active robot-server run's loaded labware. Never raises.

        Returns ``{"labware": [...], "modules": [...]}`` for the current run, or
        ``None`` when there is no active run or the robot is unreachable. This is
        the ``run`` source for the deck merge and is session-independent (it works
        even when the gateway holds no REPL session — e.g. a run started in the
        official Opentrons app). It is deliberately NOT called from ``get_status``.
        """

        base = self._probe_base_url()
        if base is None:
            return None
        try:
            runs = requests.get(
                base + "/runs", headers=_OPENTRONS_HTTP_HEADERS, timeout=_OT2_HTTP_TIMEOUT
            ).json()
        except Exception:
            return None
        run_id = None
        for r in runs.get("data", []) or []:
            if r.get("current") and r.get("status") not in {"succeeded", "failed", "stopped"}:
                run_id = r.get("id")
                break
        if not run_id:
            return None
        try:
            run = requests.get(
                base + f"/runs/{run_id}",
                headers=_OPENTRONS_HTTP_HEADERS,
                timeout=_OT2_HTTP_TIMEOUT,
            ).json()
        except Exception:
            return None
        data = run.get("data") or {}
        return {"labware": data.get("labware") or [], "modules": data.get("modules") or []}

    def _refresh_run_labware(self, *, force: bool = False) -> None:
        """Refresh the cached run-labware, TTL-guarded. Best-effort; never raises."""

        if (
            not force
            and self._last_run_labware_at
            and (time.monotonic() - self._last_run_labware_at) < _OT2_DECK_PROBE_TTL
        ):
            return
        self._last_run_labware = self.probe_run_labware()
        self._last_run_labware_at = time.monotonic()

    def _refresh_identity(self) -> None:
        """Update cached probe/version from a best-effort HTTP read."""

        probe = self.probe_robot()
        if probe.get("reachable"):
            self._last_probe = probe
            if probe.get("api_version"):
                self.equipment_version = probe["api_version"]
        self._refresh_run_labware(force=True)

    def boot_reconnect(self) -> None:
        """Guarded one-shot reconnect at process start.

        Probe the robot's HTTP API and only (re)establish the SSH/REPL protocol
        context when the robot is reachable AND idle, so a service restart
        self-heals to ``ready`` without ever seizing the hardware from an active
        run (e.g. one started in the official Opentrons app). The REPL plane and
        the robot-server run engine are mutually exclusive — this is the guard
        that keeps them from colliding.
        """

        if self.dry_run or self._boot_started:
            return
        self._boot_started = True

        probe = self.probe_robot()
        self._last_probe = probe
        if probe.get("api_version"):
            self.equipment_version = probe["api_version"]
        # Capture any active external run's labware so the deck reflects it even
        # while the gateway stands off (EXTERNAL_CONTROL). Cheap, best-effort.
        self._refresh_run_labware(force=True)

        if not probe.get("reachable"):
            self._status_note = (
                f"Robot unreachable at {self._probe_base_url() or 'unknown host'}; awaiting startup"
            )
            return
        if probe.get("run_active"):
            self.state = OT2ServiceState.EXTERNAL_CONTROL
            self._status_note = (
                "Robot has an active run (external / official app); gateway is standing off"
            )
            return

        # Reachable and idle: safe to take the REPL control plane.
        self._status_note = None
        try:
            self.startup()
        except Exception:
            # startup() already recorded last_error and flipped to ERROR.
            pass

    def refresh_snapshot(self) -> Dict[str, Any]:
        """Refresh cached state from the remote session when possible."""

        if self.dry_run:
            self.last_snapshot = self._dry_run_snapshot()
            return self.last_snapshot

        if self.control is None:
            self.last_snapshot = self._empty_snapshot()
            return self.last_snapshot

        if self.transport == "http":
            return self._refresh_snapshot_http()

        try:
            # The reader functions need to execute where the protocol object
            # lives: inside the robot-side Python interpreter. We send the
            # reader source over the wire rather than importing it, because
            # the OT-2 only runs the Opentrons SDK — see _REMOTE_SNAPSHOT_*.
            self.control.invoke(_REMOTE_SNAPSHOT_DEFS)
            output = self.control.invoke(_REMOTE_SNAPSHOT_CALL)
            self.last_snapshot = self._parse_remote_snapshot(output)
        except Exception as exc:
            self._set_error("snapshot_failed", str(exc), severity="warning")
        return self.last_snapshot

    def _refresh_snapshot_http(self) -> Dict[str, Any]:
        """Deck snapshot from the run engine (GET /runs/{id}).

        The run resource carries the loaded labware/modules under the same
        top-level keys the external-run probe returns, so we feed them into
        ``_last_run_labware`` — the ``run`` source of ``_build_deck_state`` — and
        the deck tile gets full parity through the existing ``build_deck``
        precedence (run > repl > declared) with no bespoke parser. There is no
        REPL deck in HTTP mode, so ``last_snapshot`` carries no ``deck`` key and
        the repl source stays empty. Never crashes the side-effect-free path.
        """
        try:
            run = self.control.run_snapshot()
            labware = run.get("labware") or []
            modules = run.get("modules") or []
            # Same {labware, modules} shape probe_run_labware yields; drives the
            # `run` deck source via normalize_run_slots.
            self._last_run_labware = {"labware": labware, "modules": modules}
            self._last_run_labware_at = time.monotonic()
            # Raw passthrough for the details panel (run-engine list shape).
            self.last_snapshot = {
                "run_id": run.get("id"),
                "pipettes": run.get("pipettes") or [],
                "labwares": labware,
                "modules": modules,
            }
        except Exception as exc:
            self._set_error("snapshot_failed", str(exc), severity="warning")
        return self.last_snapshot

    @staticmethod
    def _parse_remote_snapshot(output: str) -> Dict[str, Any]:
        """Extract the structured snapshot dict from the REPL transcript.

        ``output`` is the raw SSH-REPL transcript captured by the transport:
        the echoed ``json.dumps(...)`` command, the printed JSON object, and a
        trailing ``>>>`` prompt. The reader prints a single ``json.dumps``
        line, so the JSON object spans from the first ``{`` to the last ``}``
        — neither the echoed command nor the prompt contains a brace. Slice
        that span and parse it so ``details.snapshot`` carries the same
        ``{deck, pipettes, labwares, modules}`` shape the dry-run path returns.

        Falls back to ``{"raw": output, "note": ...}`` on any parse failure so
        a garbled read never crashes the side-effect-free ``/status`` handler.
        """

        start = output.find("{")
        end = output.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(output[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                return parsed
        return {"raw": output, "note": "remote JSON could not be parsed from REPL output"}

    def get_status(self) -> EquipmentStatus:
        """Return a side-effect-free AC equipment status envelope."""

        now = datetime.now(timezone.utc)
        status = self._equipment_state()
        lights = self._lights_component()
        # Lights are a convenience control, not gated on equipment_status, so
        # advertise lights.set whenever the robot answered the lights read.
        actions = self.allowed_actions()
        if lights.connected and "lights.set" not in actions:
            actions.append("lights.set")
        # Declaring the deck layout is pure metadata (no hardware), so it is a
        # convenience action available in every state except EXTERNAL_CONTROL,
        # where the gateway advertises nothing while an external app owns the robot.
        if self.state != OT2ServiceState.EXTERNAL_CONTROL and "deck.declare" not in actions:
            actions.append("deck.declare")
        raw = self.last_snapshot if isinstance(self.last_snapshot, dict) else {}
        # `snapshot.deck` is the normalized, provenance-tagged DeckState (built from
        # cached inputs only — see _build_deck_state); pipettes/labwares/modules stay
        # as the raw REPL read for now.
        details: Dict[str, Any] = {
            "service_state": self.state.value,
            "dry_run": self.dry_run,
            "simulation": self.simulation,
            "snapshot": {
                "deck": self._build_deck_state().model_dump(mode="json"),
                "pipettes": raw.get("pipettes", {}),
                "labwares": raw.get("labwares", {}),
                "modules": raw.get("modules", {}),
            },
            "session_recipe": self.session_recipe,
        }
        loaded_plate = self.plates.get()
        details["loaded_plate"] = loaded_plate.model_dump(mode="json") if loaded_plate else None
        if self._last_probe:
            details["robot"] = self._last_probe
        claimed_by = self.claims.current()
        if claimed_by is not None:
            details["claimed_by"] = claimed_by.model_dump(mode="json")

        return EquipmentStatus(
            equipment_id=self.equipment_id,
            equipment_name=self.equipment_name,
            equipment_version=self.equipment_version,
            equipment_status=status,
            message=self._message(),
            required_actions=self._required_actions(),
            allowed_actions=actions,
            device_time=now,
            uptime_seconds=time.monotonic() - self.started_at,
            components=self._components(lights),
            metrics={},
            last_error=self.last_error,
            details=details,
        )

    def _build_deck_state(self):
        """Merge the cached deck sources into a normalized DeckState.

        Side-effect-free: reads only cached fields (`last_snapshot`,
        `_last_run_labware`, the plate store, `session_recipe`) — no HTTP, no REPL.
        `declared` is wired in Phase 2. Precedence run > repl > declared > empty.
        """

        run_active = bool(self._last_probe.get("run_active")) if self._last_probe else False
        busy = self.state in {OT2ServiceState.BUSY, OT2ServiceState.EXTERNAL_CONTROL} or run_active
        repl = normalize_repl_slots((self.last_snapshot or {}).get("deck"))
        run = normalize_run_slots(self._last_run_labware) if self._last_run_labware else None
        return build_deck(
            run=run,
            repl=repl,
            declared=self._declared_slots(),
            loaded_plate=self.plates.get(),
            nickname_to_slot=self._nickname_to_slot(),
            busy=busy,
            now=datetime.now(timezone.utc),
        )

    def _declared_slots(self) -> Dict[str, SlotLabware]:
        """Merge the standalone operator declaration with the realized setup recipe.

        Two declared sub-sources: the persisted :class:`DeckDeclarationStore` (the
        stopgap replacement — set when there is no session) and ``session_recipe``
        (what ``/control/setup`` actually loaded). The setup recipe overlays the
        standalone declaration per slot, since it reflects what the gateway loaded.
        """

        declared: Dict[str, SlotLabware] = dict(self.decks.get())
        for lw in self.session_recipe.get("labware", []) or []:
            loadname = lw.get("loadname") or lw.get("load_name")
            location = lw.get("location")
            if loadname and location is not None and str(location) in SLOTS:
                declared[str(location)] = make_slot_labware(
                    loadname, display_name=lw.get("nickname")
                )
        return declared

    def declare_deck(self, mapping: Dict[str, Any]):
        """Replace the operator-declared layout. Raises ValueError on a bad slot."""

        return self.decks.declare(mapping)

    def clear_deck(self) -> None:
        self.decks.clear()

    def run_background_refresh(self) -> None:
        """Daemon loop: periodically refresh the external-run probe.

        Keeps an EXTERNAL_CONTROL deck (and the run-active busy flag) fresh between
        boots without the status handler ever issuing HTTP. Best-effort; a failed
        probe is swallowed and retried next tick.
        """

        while not self._refresh_stop.wait(_OT2_RUN_REFRESH_INTERVAL):
            try:
                self._refresh_identity()
            except Exception:  # pragma: no cover - best-effort background loop
                pass

    def _nickname_to_slot(self) -> Dict[str, str]:
        """Map labware nicknames to deck slots from the current setup recipe.

        organic-solubility sends the plate nickname (e.g. "D") as the gateway
        `plate_id`, and `/control/setup` maps that nickname to a slot; this join
        is what places the tracked plate's wells on the right slot.
        """

        out: Dict[str, str] = {}
        for lw in self.session_recipe.get("labware", []) or []:
            nickname = lw.get("nickname")
            location = lw.get("location")
            if nickname is not None and location is not None:
                out[str(nickname)] = str(location)
        return out

    def allowed_actions(self) -> list[str]:
        if self.state in {OT2ServiceState.REQUIRES_INIT, OT2ServiceState.ERROR}:
            return ["startup"]
        if self.state == OT2ServiceState.DRY_RUN:
            return [
                "startup",
                "shutdown",
                "home",
                "setup",
                "plate.load",
                "plate.unload",
                "well.update",
            ]
        if self.state == OT2ServiceState.PAUSED:
            return ["resume", "shutdown"]
        if self.state == OT2ServiceState.READY:
            return [
                "shutdown",
                "home",
                "setup",
                "pause",
                "pick_up_tip",
                "aspirate",
                "dispense",
                "drop_tip",
                "move_labware",
                "plate.load",
                "plate.unload",
                "well.update",
            ]
        if self.state == OT2ServiceState.BUSY:
            return ["pause"]
        if self.state == OT2ServiceState.EXTERNAL_CONTROL:
            # Robot is driven from outside this gateway; we issue nothing.
            return []
        return []

    def reconcile(self, snapshot: Optional[Dict[str, Any]] = None) -> None:
        """Acknowledge an unknown outcome after manual or external inspection."""

        if snapshot is not None:
            self.last_snapshot = snapshot
        if self.state == OT2ServiceState.UNKNOWN_OUTCOME:
            self.last_error = None
            self.state = OT2ServiceState.READY

    def _run_action(self, name: str, func: Callable[[], None], *, idempotent: bool) -> None:
        if self.dry_run:
            self.state = OT2ServiceState.DRY_RUN
            self.last_error = None
            return
        if self.state in {OT2ServiceState.BUSY, OT2ServiceState.UNKNOWN_OUTCOME}:
            raise RuntimeError(f"OT-2 is not ready for {name}; current state is {self.state.value}")

        previous_state = self.state
        self.state = OT2ServiceState.BUSY
        try:
            func()
            self.state = OT2ServiceState.READY
            self.last_error = None
            self.refresh_snapshot()
        except (socket.timeout, paramiko.SSHException, OSError) as exc:
            if idempotent:
                self._set_error(f"{name}_transport_failed", str(exc), severity="error")
            else:
                self.state = OT2ServiceState.UNKNOWN_OUTCOME
                self._set_error(f"{name}_unknown_outcome", str(exc), severity="critical")
                raise UnknownOutcomeError(str(exc)) from exc
            raise
        except Exception as exc:
            self._set_error(f"{name}_failed", str(exc), severity="error")
            raise
        finally:
            if self.state == OT2ServiceState.BUSY:
                self.state = previous_state

    def _require_control(self) -> OT2Control:
        if self.control is None:
            raise RuntimeError("OT-2 is not initialized")
        return self.control

    def _set_error(self, code: str, message: str, *, severity: str) -> None:
        self.last_error = ErrorInfo(
            code=code,
            message=message,
            severity=severity,  # type: ignore[arg-type]
            timestamp=datetime.now(timezone.utc),
        )
        if severity in {"error", "critical"} and self.state not in {
            OT2ServiceState.UNKNOWN_OUTCOME,
            OT2ServiceState.DRY_RUN,
        }:
            self.state = OT2ServiceState.ERROR

    def _equipment_state(self) -> str:
        if self.state == OT2ServiceState.READY:
            return "ready"
        if self.state in {
            OT2ServiceState.BUSY,
            OT2ServiceState.CONNECTING,
            OT2ServiceState.EXTERNAL_CONTROL,
        }:
            return "busy"
        if self.state == OT2ServiceState.REQUIRES_INIT:
            return "requires_init"
        if self.state == OT2ServiceState.DRY_RUN:
            return "dry_run"
        if self.state == OT2ServiceState.PAUSED:
            return "degraded"
        if self.state == OT2ServiceState.UNKNOWN_OUTCOME:
            return "unknown"
        return "error"

    def _message(self) -> str:
        if self.last_error is not None:
            return self.last_error.message
        if self.state == OT2ServiceState.EXTERNAL_CONTROL:
            return self._status_note or "Robot under external control; gateway is standing off"
        if self.state == OT2ServiceState.REQUIRES_INIT:
            return self._status_note or "Awaiting startup"
        if self.state == OT2ServiceState.DRY_RUN:
            return "Dry-run mode - no hardware connected"
        if self.state == OT2ServiceState.UNKNOWN_OUTCOME:
            return "Transport failed during a non-idempotent operation; reconcile before continuing"
        return f"OT-2 service state: {self.state.value}"

    def _required_actions(self) -> list[str]:
        if self.state == OT2ServiceState.REQUIRES_INIT:
            return ["startup"]
        if self.state == OT2ServiceState.UNKNOWN_OUTCOME:
            return ["manual_reconcile"]
        if self.state == OT2ServiceState.ERROR:
            return ["startup"]
        return []

    def _components(self, lights: ComponentStatus) -> Dict[str, ComponentStatus]:
        connected = self.control is not None or self.dry_run
        components: Dict[str, ComponentStatus] = {
            "ssh": ComponentStatus(
                connected=connected,
                state="connected" if connected else "disconnected",
            ),
            "protocol": ComponentStatus(
                connected=self.state
                in {
                    OT2ServiceState.READY,
                    OT2ServiceState.BUSY,
                    OT2ServiceState.PAUSED,
                    OT2ServiceState.DRY_RUN,
                },
                state=self.state.value,
            ),
            "lights": lights,
        }
        # Attached pipettes, surfaced from the (session-independent) HTTP probe.
        for instrument in self._last_probe.get("instruments", []) or []:
            mount = instrument.get("mount")
            if not mount:
                continue
            components[f"pipette_{mount}"] = ComponentStatus(
                connected=True,
                state=instrument.get("name") or instrument.get("model") or "attached",
            )
        return components

    def _empty_snapshot(self) -> Dict[str, Any]:
        return {
            "deck": {"slots": {}, "occupied_slots": 0, "empty_slots": 12},
            "pipettes": {},
            "labwares": {},
            "modules": {},
        }

    def _dry_run_snapshot(self) -> Dict[str, Any]:
        return {
            "deck": {
                "slots": {str(i): None for i in range(1, 13)},
                "occupied_slots": 0,
                "empty_slots": 12,
            },
            "pipettes": {},
            "labwares": {},
            "modules": {},
        }
