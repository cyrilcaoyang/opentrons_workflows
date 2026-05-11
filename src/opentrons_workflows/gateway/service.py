"""Runtime service and state machine for the OT-2 gateway."""

from __future__ import annotations

import socket
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Optional

import paramiko

from ..control import OT2Control
from .claims import ClaimManager
from .models import ComponentStatus, EquipmentStatus, ErrorInfo


class OT2ServiceState(str, Enum):
    REQUIRES_INIT = "requires_init"
    CONNECTING = "connecting"
    READY = "ready"
    BUSY = "busy"
    PAUSED = "paused"
    DRY_RUN = "dry_run"
    ERROR = "error"
    UNKNOWN_OUTCOME = "unknown_outcome"


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
    ) -> None:
        self.equipment_id = equipment_id
        self.equipment_name = equipment_name
        self.host_alias = host_alias
        self.password = password
        self.dry_run = dry_run
        self.simulation = simulation
        self.started_at = time.monotonic()
        self.state = OT2ServiceState.DRY_RUN if dry_run else OT2ServiceState.REQUIRES_INIT
        self.control: Optional[OT2Control] = None
        self.claims = ClaimManager()
        self.last_error: Optional[ErrorInfo] = None
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
        if password is not None:
            self.password = password
        if simulation is not None:
            self.simulation = simulation

        try:
            self.control = OT2Control(
                host_alias=self.host_alias,
                password=self.password,
                simulation=self.simulation,
            )
            self.state = OT2ServiceState.READY
            self.last_error = None
            self.refresh_snapshot()
        except Exception as exc:
            self._set_error("startup_failed", str(exc), severity="error")
            raise

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
        def _aspirate() -> None:
            self.set_location_from_well(request)
            self._require_control().aspirate(request.pipette, request.volume_ul)

        self._run_action("aspirate", _aspirate, idempotent=False)

    def dispense(self, request: Any) -> None:
        def _dispense() -> None:
            self.set_location_from_well(request)
            self._require_control().dispense(request.pipette, request.volume_ul)

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
        self._run_action(
            "drop_tip",
            lambda: self._require_control().drop_tip(request.pipette),
            idempotent=False,
        )

    def move_labware(self, request: Any) -> None:
        self._run_action(
            "move_labware",
            lambda: self._require_control().move_labware(
                request.labware_nickname,
                request.new_location,
            ),
            idempotent=False,
        )

    def refresh_snapshot(self) -> Dict[str, Any]:
        """Refresh cached state from the remote session when possible."""

        if self.dry_run:
            self.last_snapshot = self._dry_run_snapshot()
            return self.last_snapshot

        if self.control is None:
            self.last_snapshot = self._empty_snapshot()
            return self.last_snapshot

        try:
            # The reader functions need to execute where the protocol object
            # lives: inside the robot-side Python interpreter.
            code = (
                "from opentrons_workflows.opentrons_states import get_all_states\n"
                "import json\n"
                "print(json.dumps(get_all_states(protocol), default=str))"
            )
            output = self.control.invoke(code)
            self.last_snapshot = {"raw": output, "note": "remote JSON is embedded in REPL output"}
        except Exception as exc:
            self._set_error("snapshot_failed", str(exc), severity="warning")
        return self.last_snapshot

    def get_status(self) -> EquipmentStatus:
        """Return a side-effect-free AC equipment status envelope."""

        now = datetime.now(timezone.utc)
        status = self._equipment_state()
        details: Dict[str, Any] = {
            "service_state": self.state.value,
            "dry_run": self.dry_run,
            "simulation": self.simulation,
            "snapshot": self.last_snapshot,
            "session_recipe": self.session_recipe,
        }
        claimed_by = self.claims.current()
        if claimed_by is not None:
            details["claimed_by"] = claimed_by.model_dump(mode="json")

        return EquipmentStatus(
            equipment_id=self.equipment_id,
            equipment_name=self.equipment_name,
            equipment_status=status,
            message=self._message(),
            required_actions=self._required_actions(),
            allowed_actions=self.allowed_actions(),
            device_time=now,
            uptime_seconds=time.monotonic() - self.started_at,
            components=self._components(),
            metrics={},
            last_error=self.last_error,
            details=details,
        )

    def allowed_actions(self) -> list[str]:
        if self.state in {OT2ServiceState.REQUIRES_INIT, OT2ServiceState.ERROR}:
            return ["startup"]
        if self.state == OT2ServiceState.DRY_RUN:
            return ["startup", "shutdown", "home", "setup"]
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
            ]
        if self.state == OT2ServiceState.BUSY:
            return ["pause"]
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
        if (
            severity in {"error", "critical"}
            and self.state not in {OT2ServiceState.UNKNOWN_OUTCOME, OT2ServiceState.DRY_RUN}
        ):
            self.state = OT2ServiceState.ERROR

    def _equipment_state(self) -> str:
        if self.state == OT2ServiceState.READY:
            return "ready"
        if self.state in {OT2ServiceState.BUSY, OT2ServiceState.CONNECTING}:
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
        if self.state == OT2ServiceState.REQUIRES_INIT:
            return "Awaiting startup"
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

    def _components(self) -> Dict[str, ComponentStatus]:
        connected = self.control is not None or self.dry_run
        return {
            "ssh": ComponentStatus(
                connected=connected,
                state="connected" if connected else "disconnected",
            ),
            "protocol": ComponentStatus(
                connected=self.state
                in {OT2ServiceState.READY, OT2ServiceState.BUSY, OT2ServiceState.PAUSED, OT2ServiceState.DRY_RUN},
                state=self.state.value,
            ),
        }

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
