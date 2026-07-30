"""Runtime service and state machine for the OT-2 gateway."""

from __future__ import annotations

import inspect
import json
import logging
import os
import socket
import threading
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

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
    EQUIPMENT_KIND,
    PROTOCOL_VERSION,
    Activity,
    ComponentStatus,
    EquipmentStatus,
    ErrorInfo,
    LoadedPlate,
    MetricValue,
    SlotLabware,
    SlotModule,
    WellSample,
)
from .plate_state import PlateStateStore
from .tip_state import EMPTY, TipStateStore

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
logger = logging.getLogger(__name__)

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


# Actions that drive a protocol command on the robot — this device's primary
# operation. Withheld from `allowed_actions` while one is already in flight
# (STATUS_SPEC §2.3). Everything else on the surface is either abort/stop
# class (`pause`), lifecycle (`startup` / `shutdown`), or pure bookkeeping
# (plate / tip / deck tracking), none of which start a second run.
_RUN_STARTING_ACTIONS = frozenset(
    {
        "setup",
        "home",
        "move_to",
        "pick_up_tip",
        "aspirate",
        "dispense",
        "drop_tip",
        "move_labware",
        "resume",
    }
)


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
        tips: Optional[TipStateStore] = None,
        transport: Optional[str] = None,
        http_run_state_path: Optional[str] = None,
    ) -> None:
        self.equipment_id = equipment_id
        self.equipment_name = equipment_name
        self.host_alias = host_alias
        self.password = password
        self.dry_run = dry_run
        self.simulation = simulation
        # Control-plane transport: "ssh" (default, the SSH REPL) or "http" (the
        # robot-server run engine, docs/HTTP_TRANSPORT.md). Opt-in and fully
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
        # Per-tiprack tip lifecycle (fresh / sample-touched / empty), persisted
        # across restarts. Racks register on /control/setup; picks are validated
        # against it; aspirate/dispense/drop mutate it. Transport-agnostic: the
        # hooks live here, above OT2Control / OT2HttpControl.
        self.tips = (
            tips if tips is not None else TipStateStore(state_path="./ot2_tip_state.json")
        )
        # pipette -> {rack, well, last_sample, origin_status} for the currently
        # mounted (tracked) tip. In-memory session state, like session_recipe.
        self._mounted_tips: Dict[str, Dict[str, Any]] = {}
        self._refresh_stop = threading.Event()
        self.started_at = time.monotonic()
        self.state = OT2ServiceState.DRY_RUN if dry_run else OT2ServiceState.REQUIRES_INIT
        # Either an OT2Control (SSH) or an OT2HttpControl (run engine); both expose
        # the same method surface the service calls.
        self.control: Optional[Any] = None
        self.claims = ClaimManager()
        self.last_error: Optional[ErrorInfo] = None
        self._dry_run_lights_on = False
        # Cached deck-light state. Refreshed off the request path (background
        # refresh loop, startup) so /status never issues a blocking HTTP read
        # to the robot — see _refresh_lights / _lights_component. None => the
        # state is not yet known (reported as "unknown"). In dry-run there is
        # no background loop, so seed it from the simulated in-memory state and
        # let set_lights keep it current.
        self._last_lights: Optional[bool] = self._dry_run_lights_on if dry_run else None
        self.equipment_version: Optional[str] = None
        self._last_probe: Dict[str, Any] = {}
        # Cached labware of an active *external* robot-server run (EXTERNAL_CONTROL).
        # None while the gateway owns the REPL (deck then comes from last_snapshot).
        self._last_run_labware: Optional[Dict[str, Any]] = None
        self._last_run_labware_at: float = 0.0
        self._status_note: Optional[str] = None
        self._boot_started = False
        # Activity span tracking (STATUS_SPEC v1.2 §2.3). `_activity` is the
        # last observed value and `_activity_since` the instant it last
        # changed — the start of the CURRENT span, not of the process or of
        # the enclosing request. `_run_action` stamps the exact edges of a
        # gateway-driven command; `get_status` only reconciles.
        self._activity: Activity = "unknown"
        self._activity_since: Optional[datetime] = None
        # Reserved monotonic counter (§2.3.1). A protocol command is typically
        # far shorter than the aggregator's 60 s poll, so a sampled `activity`
        # series would miss whole commands outright; the poll-to-poll delta of
        # this counter is what makes OT-2 utilization accountable. Counts
        # commands this process completed — it resets on restart, by contract.
        self._cycles_total = 0
        self.last_snapshot: Dict[str, Any] = self._empty_snapshot()
        self.session_recipe: Dict[str, Any] = {
            "labware": [],
            "instruments": [],
            "modules": [],
        }
        # Stamp the opening span so `activity_since` is a real instant from the
        # first poll on, rather than "unknown until someone asks".
        self._sync_activity()

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
        # Track every tiprack in the realized recipe. Non-destructive: a rack
        # already known (e.g. re-setup after a gateway restart) keeps its
        # partially-used statuses; /control/tips/reset marks a physical swap.
        for lw in self.session_recipe.get("labware", []) or []:
            nickname = lw.get("nickname")
            if nickname and self._labware_is_tiprack(lw):
                self.tips.register_rack(str(nickname))

    @staticmethod
    def _labware_is_tiprack(lw: Dict[str, Any]) -> bool:
        config = lw.get("config") or {}
        if isinstance(config, dict):
            params = config.get("parameters") or {}
            if isinstance(params, dict) and params.get("isTiprack"):
                return True
        loadname = str(lw.get("loadname") or lw.get("load_name") or "").lower()
        return "tiprack" in loadname

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

    def move_to(self, request: Any) -> None:
        """Move a pipette to a well or to absolute deck coordinates (no liquid).

        Idempotent: re-issuing the same move is safe (no tip/liquid state at
        risk), so a transport loss mid-move records an error rather than
        ``unknown_outcome`` — same policy as ``home``.
        """

        def _move_to() -> None:
            if request.location is not None:
                self.set_location_from_well(request)
            else:
                coords = request.coordinates
                self._require_control().get_location_absolute(coords.x, coords.y, coords.z)
            self._require_control().move_to_pip(
                request.pipette,
                speed=request.speed,
                # False -> None keeps the SSH invoke minimal (kwargs formatter
                # skips None); the protocol-API default is False anyway.
                force_direct=request.force_direct or None,
                minimum_z_height=request.minimum_z_height,
            )

        self._run_action("move_to", _move_to, idempotent=True)

    def aspirate(self, request: Any) -> None:
        flow_rate = getattr(request, "flow_rate", None)

        def _aspirate() -> None:
            self.set_location_from_well(request)
            self._require_control().aspirate(
                request.pipette, request.volume_ul, flow_rate=flow_rate
            )

        self._run_action("aspirate", _aspirate, idempotent=False)
        self._mark_tip_used(
            request.pipette, request.location.labware_nickname, request.location.position
        )

    def dispense(self, request: Any) -> None:
        flow_rate = getattr(request, "flow_rate", None)

        def _dispense() -> None:
            self.set_location_from_well(request)
            self._require_control().dispense(
                request.pipette, request.volume_ul, flow_rate=flow_rate
            )

        self._run_action("dispense", _dispense, idempotent=False)
        self._mark_tip_used(
            request.pipette, request.location.labware_nickname, request.location.position
        )

    def pick_up_tip(self, request: Any) -> None:
        rack = request.labware_nickname
        well = request.position
        sample_id = getattr(request, "sample_id", None)
        force = bool(getattr(request, "force", False))
        tracked = bool(rack) and self.tips.has_rack(rack)

        # Contamination guard + auto-pick, both only for tracked racks. Raises
        # TipUnavailable (HTTP 412 at the API layer) before any hardware motion.
        if tracked and not well:
            well = self.tips.next_available(rack, sample_id=sample_id)
        prior_status: Optional[str] = None
        if tracked and well:
            prior_status = self.tips.validate_pick(
                rack, well, sample_id=sample_id, force=force
            )

        def _pick_up_tip() -> None:
            if rack and well:
                self._require_control().get_location_from_labware(rack, well)
            self._require_control().pick_up_tip(request.pipette)

        self._run_action("pick_up_tip", _pick_up_tip, idempotent=False)
        if tracked and well:
            self._mounted_tips[request.pipette] = {
                "rack": rack,
                "well": well,
                "last_sample": sample_id or prior_status,
                "origin_status": prior_status,
            }

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
        mounted = self._mounted_tips.pop(request.pipette, None)
        if mounted is not None and self.tips.has_rack(mounted["rack"]):
            self.tips.set_status(mounted["rack"], mounted["well"], EMPTY)

    def _mark_tip_used(self, pipette: str, labware_nickname: str, position: str) -> None:
        """Record what the mounted tip touched, after a successful liquid step.

        Touching a tracked tiprack is not a sample contact; anything else stamps
        the tip's origin well with a sample id — the tracked plate's real
        ``sample_id`` when the target well has one, else ``<labware>_<well>``.
        """

        mounted = self._mounted_tips.get(pipette)
        if mounted is None or self.tips.has_rack(labware_nickname):
            return
        sample = self._resolve_sample_id(labware_nickname, position)
        mounted["last_sample"] = sample
        if self.tips.has_rack(mounted["rack"]):
            self.tips.set_status(mounted["rack"], mounted["well"], sample)

    def _resolve_sample_id(self, labware_nickname: str, position: str) -> str:
        plate = self.plates.get()
        if plate is not None and plate.plate_id == labware_nickname:
            for w in plate.wells:
                if w.well == position and w.sample_id:
                    return w.sample_id
        return f"{labware_nickname}_{position}"

    def reset_tip_rack(self, nickname: str, *, wells: Optional[list[str]] = None):
        """(Re)register a rack with all tips fresh — a physical rack swap."""

        return self.tips.reset_rack(nickname, wells=wells)

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
            self._last_lights = on
            return on
        url = self._robot_lights_url()
        if url is None:
            raise RuntimeError("OT-2 is not initialized; POST /control/startup first")
        response = requests.post(
            url, json={"on": on}, headers=_OPENTRONS_HTTP_HEADERS, timeout=_OT2_HTTP_TIMEOUT
        )
        response.raise_for_status()
        result = bool(response.json().get("on", on))
        # Reflect the new state immediately so the next /status doesn't lag a
        # background-refresh interval behind an operator toggle.
        self._last_lights = result
        return result

    def _refresh_lights(self) -> None:
        """Refresh the cached deck-light state from a best-effort read.

        Runs only *off* the request path — the background refresh loop and
        ``startup`` — so ``/status`` itself never issues the blocking HTTP read
        that would otherwise stall a poll (and, under contention, drop the
        socket before replying). Never raises; an unreachable robot resets the
        cache to ``None`` so the component honestly reports ``unknown``.
        """

        try:
            self._last_lights = self.get_lights()
        except Exception:
            self._last_lights = None

    def _lights_component(self) -> ComponentStatus:
        """Build the ``lights`` component from the cached state (no I/O).

        Reads only ``self._last_lights`` (maintained by ``_refresh_lights`` and
        ``set_lights``), so ``/status`` stays side-effect-free and always
        returns 200. ``None`` (state not yet known / robot unreachable at the
        last refresh) is reported as ``unknown``/disconnected.
        """

        on = self._last_lights
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
        # Fold the deck-light read into the same off-request-path refresh so
        # /status can serve it from cache instead of blocking on robot HTTP.
        self._refresh_lights()
        # Self-heal from a boot-time stand-off: if we deferred to an external
        # (app-driven) run at boot and that run has since finished, reclaim the
        # control plane so the gateway returns to `ready` without a restart.
        self._maybe_resume_from_external_control(probe)

    def _maybe_resume_from_external_control(self, probe: Dict[str, Any]) -> None:
        """Reclaim the REPL control plane once an external run has finished.

        ``EXTERNAL_CONTROL`` is a boot-time stand-off: when the gateway starts
        while the robot already has a run we must not seize (see
        ``boot_reconnect``), it defers. Nothing else transitioned out of it, so
        before this the gateway reported ``busy`` until a manual restart even
        after the external run completed. The background refresh watches the
        live probe and, once the robot is reachable AND no longer running,
        (re)establishes our own session so the gateway self-heals to ``ready``.
        Mirrors the idle branch of ``boot_reconnect``. Best-effort; never raises.

        ``probe`` must be a freshly-read ``probe_robot()`` result — not the
        possibly-stale ``_last_probe`` (which is only updated while reachable) —
        so a robot that has gone unreachable never triggers a reclaim.
        """

        if self.dry_run or self.state != OT2ServiceState.EXTERNAL_CONTROL:
            return
        if not probe.get("reachable") or probe.get("run_active"):
            return
        # Reachable and idle: safe to take the REPL control plane.
        self._status_note = None
        try:
            self.startup()
        except Exception:  # pragma: no cover - startup records its own error/state
            # startup() already recorded last_error and flipped to ERROR.
            pass

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
            logger.warning("boot_reconnect: %s", self._status_note)
            return
        if probe.get("run_active"):
            self.state = OT2ServiceState.EXTERNAL_CONTROL
            self._status_note = (
                "Robot has an active run (external / official app); gateway is standing off"
            )
            logger.info("boot_reconnect: %s", self._status_note)
            return

        # Reachable and idle: safe to take the REPL control plane.
        self._status_note = None
        # Logged at both ends: the SSH session plus protocol-API init routinely
        # takes minutes, and with no log line the gateway looked hung rather
        # than working. The elapsed time also gives a baseline to compare
        # against when a boot really does wedge.
        logger.info(
            "boot_reconnect: robot reachable and idle; starting %s session + protocol init "
            "(this can take several minutes)",
            self.transport,
        )
        started = time.monotonic()
        try:
            self.startup()
        except Exception as exc:
            # startup() already recorded last_error and flipped to ERROR.
            logger.error(
                "boot_reconnect: startup failed after %.1fs: %s",
                time.monotonic() - started,
                exc,
            )
        else:
            logger.info(
                "boot_reconnect: ready after %.1fs", time.monotonic() - started
            )

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

        Container-shape parity with the SSH snapshot: ``pipettes`` is a dict
        keyed by mount and ``labwares``/``modules`` dicts keyed by deck slot
        (falling back to the engine id for off-deck/keyless entries), matching
        ``state_readers.get_all_states``. The *values* remain raw run-engine
        entries — the per-item schemas differ per transport by design (see
        HTTP_SSH_PARITY.md). ``run_id`` is HTTP-only.
        """
        try:
            run = self.control.run_snapshot()
            labware = run.get("labware") or []
            modules = run.get("modules") or []
            pipettes = run.get("pipettes") or []
            # Same {labware, modules} shape probe_run_labware yields; drives the
            # `run` deck source via normalize_run_slots.
            self._last_run_labware = {"labware": labware, "modules": modules}
            self._last_run_labware_at = time.monotonic()
            self.last_snapshot = {
                "run_id": run.get("id"),
                "pipettes": {
                    (entry.get("mount") or entry.get("id") or f"pipette_{i}"): entry
                    for i, entry in enumerate(pipettes)
                },
                "labwares": self._key_run_entries_by_slot(labware),
                "modules": self._key_run_entries_by_slot(modules),
            }
        except Exception as exc:
            self._set_error("snapshot_failed", str(exc), severity="warning")
        return self.last_snapshot

    @staticmethod
    def _key_run_entries_by_slot(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Key run-engine labware/module entries by deck slot, mirroring the SSH
        snapshot's slot-keyed dicts. Off-deck / slotless entries (and slot
        collisions, e.g. mid-move) fall back to the engine id so nothing is
        silently dropped."""
        keyed: Dict[str, Any] = {}
        for i, entry in enumerate(entries):
            location = entry.get("location")
            key = None
            if isinstance(location, dict) and location.get("slotName"):
                key = str(location["slotName"])
            if key is None or key in keyed:
                key = str(entry.get("id") or f"entry_{i}")
            keyed[key] = entry
        return keyed

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
        # Health (§2.2) and activity (§2.3) are answered independently; this
        # only reconciles the span for transitions no command edge stamped
        # (a boot stand-off, a self-heal, an operator reconcile).
        activity = self._sync_activity()
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
        details["tip_racks"] = self.tips.summary()
        details["mounted_tips"] = {
            pip: dict(info) for pip, info in self._mounted_tips.items()
        }
        if self._last_probe:
            details["robot"] = self._last_probe
        claimed_by = self.claims.current()
        if claimed_by is not None:
            details["claimed_by"] = claimed_by.model_dump(mode="json")

        return EquipmentStatus(
            # Explicit: the shared model defaults to "1.0" (the honest reading
            # of a device that does not state a version), so every envelope
            # names the version this gateway actually speaks.
            protocol_version=PROTOCOL_VERSION,
            equipment_id=self.equipment_id,
            equipment_name=self.equipment_name,
            equipment_kind=EQUIPMENT_KIND,
            equipment_version=self.equipment_version,
            equipment_status=status,
            activity=activity,
            activity_since=self._activity_since,
            message=self._message(),
            required_actions=self._required_actions(),
            allowed_actions=actions,
            device_time=now,
            uptime_seconds=time.monotonic() - self.started_at,
            components=self._components(lights),
            metrics={"cycles_total": MetricValue(value=self._cycles_total, unit="count")},
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

    def _declared_slots(self) -> Dict[str, Union[SlotLabware, SlotModule]]:
        """Merge the standalone operator declaration with the realized setup recipe.

        Two declared sub-sources: the persisted :class:`DeckDeclarationStore` (the
        stopgap replacement — set when there is no session) and ``session_recipe``
        (what ``/control/setup`` actually loaded). The setup recipe overlays the
        standalone declaration per slot, since it reflects what the gateway loaded.
        Declared entries may be labware or a sticky module (temperature module, …).
        """

        declared: Dict[str, Union[SlotLabware, SlotModule]] = dict(self.decks.get())
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
        """What this gateway would honor right now (§6.2 / §2.3).

        The state table below is the primary gate; the activity gate after it
        is a belt-and-braces guarantee that no protocol command is ever
        advertised while one is in flight, however the state table evolves.
        """

        return [a for a in self._allowed_for_state() if not self._blocked_by_activity(a)]

    def _blocked_by_activity(self, action: str) -> bool:
        """§2.3: while ``activity == "running"``, omit anything that would
        start or enqueue a *second* concurrent command. Abort/stop-class
        actions (``pause``) and pure bookkeeping stay available."""

        return action in _RUN_STARTING_ACTIONS and self._observed_activity() == "running"

    def _allowed_for_state(self) -> list[str]:
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
                "tips.reset",
            ]
        if self.state == OT2ServiceState.PAUSED:
            return ["resume", "shutdown"]
        if self.state == OT2ServiceState.READY:
            return [
                "shutdown",
                "home",
                "setup",
                "pause",
                "move_to",
                "pick_up_tip",
                "aspirate",
                "dispense",
                "drop_tip",
                "move_labware",
                "plate.load",
                "plate.unload",
                "well.update",
                "tips.reset",
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
        self._sync_activity()  # exact span start (§2.3): the command is in flight
        try:
            func()
            self.state = OT2ServiceState.READY
            self.last_error = None
            self._cycles_total += 1
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
            # Exact span end, whatever the outcome — including the
            # UNKNOWN_OUTCOME path, where "still running?" is genuinely
            # unanswerable until an operator reconciles.
            self._sync_activity()

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
            OT2ServiceState.EXTERNAL_CONTROL,
        }:
            return "busy"
        # CONNECTING is "service up, hardware not initialized yet" — STATUS_SPEC
        # §2.2's requires_init, not busy. `busy` means an operation is running
        # (§2.3's invariant table pairs it with activity: running), so reporting
        # it here made a slow-but-healthy boot indistinguishable from real work
        # and hid the fact that nothing was driving the robot. The REPL protocol
        # init legitimately takes minutes on an OT-2. required_actions stays
        # empty (startup is already in flight — see _required_actions).
        if self.state in {OT2ServiceState.REQUIRES_INIT, OT2ServiceState.CONNECTING}:
            return "requires_init"
        if self.state == OT2ServiceState.DRY_RUN:
            return "dry_run"
        if self.state == OT2ServiceState.PAUSED:
            return "degraded"
        if self.state == OT2ServiceState.UNKNOWN_OUTCOME:
            return "unknown"
        return "error"

    def _observed_activity(self) -> Activity:
        """Is the robot performing its primary operation right now? (§2.3)

        Primary operation for this liquid handler is **a protocol command in
        flight on the robot** — a motion, a liquid transfer, a tip or labware
        move, or the setup that loads them. Two observations, neither read off
        ``equipment_status``:

        * ``BUSY`` brackets exactly one in-flight command (``_run_action``
          sets it around the blocking call to the control plane).
        * ``EXTERNAL_CONTROL`` is entered from the robot-server's own run list
          (``probe_robot``'s ``run_active``) and left only once a fresh probe
          says that run has finished — so it is a live observation of a run
          the gateway deliberately did not seize.

        ``run_active`` is deliberately NOT consulted on its own: the HTTP
        transport keeps a run open between commands (docs/HTTP_TRANSPORT.md),
        so an open run is not evidence of motion. The two control planes are
        mutually exclusive, so an external run cannot start underneath a
        ``READY`` gateway — the ``EXTERNAL_CONTROL`` branch is the only case
        where an outside run is observable.

        ``UNKNOWN_OUTCOME`` is the honest ``unknown``: transport died during a
        non-idempotent command, so whether the robot is still moving is
        exactly what we do not know until an operator reconciles.
        """

        if self.state in {OT2ServiceState.BUSY, OT2ServiceState.EXTERNAL_CONTROL}:
            return "running"
        if self.state == OT2ServiceState.UNKNOWN_OUTCOME:
            return "unknown"
        # Dry run included: the simulation performs no operation between
        # commands, and reporting its real (idle) activity is what keeps a
        # simulated device exercisable end-to-end. Readers exclude simulated
        # devices from utilization; devices do not self-censor.
        return "idle"

    def _note_activity(self, activity: Activity) -> None:
        """Record an observed activity, stamping ``activity_since`` only when
        the value actually changes (§2.3: the start of the CURRENT span)."""

        if activity != self._activity:
            self._activity = activity
            self._activity_since = datetime.now(timezone.utc)

    def _sync_activity(self) -> Activity:
        """Reconcile the tracked span with what is observed right now."""

        self._note_activity(self._observed_activity())
        return self._activity

    def _message(self) -> str:
        if self.last_error is not None:
            return self.last_error.message
        if self.state == OT2ServiceState.EXTERNAL_CONTROL:
            return self._status_note or "Robot under external control; gateway is standing off"
        if self.state == OT2ServiceState.REQUIRES_INIT:
            return self._status_note or "Awaiting startup"
        if self.state == OT2ServiceState.CONNECTING:
            return (
                "Connecting: SSH session + protocol-API init on the robot. "
                "This legitimately takes minutes on an OT-2; no action needed."
            )
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
        # The "ssh" component key predates the HTTP transport and means "control
        # backend session" — renaming it would break dashboards (STATUS_SPEC #14),
        # so the message carries the actual transport instead.
        if self.dry_run:
            transport_note = "dry run (no robot connection)"
        elif self.transport == "http":
            transport_note = "control via HTTP run engine (no SSH session)"
        else:
            transport_note = "control via SSH REPL"
        components: Dict[str, ComponentStatus] = {
            "ssh": ComponentStatus(
                connected=connected,
                state="connected" if connected else "disconnected",
                message=transport_note,
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
