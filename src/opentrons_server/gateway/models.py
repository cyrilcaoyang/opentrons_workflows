"""Models for the OT-2 gateway and AC equipment status contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


PROTOCOL_VERSION = "1.1"

EquipmentKind = Literal[
    "solid_doser",
    "liquid_handler",
    "press",
    "fume_hood",
    "robot_arm",
    "environmental_sensor",
    "hplc",
    "plate_reader",
    "plate_sealer",
    "plate_stacker",
    "other",
]

EquipmentState = Literal[
    "ready",
    "busy",
    "requires_init",
    "degraded",
    "dry_run",
    "error",
    "e_stop",
    "unknown",
]

ErrorSeverity = Literal["info", "warning", "error", "critical"]


class ComponentStatus(BaseModel):
    connected: bool
    state: str
    message: Optional[str] = None
    last_event_at: Optional[datetime] = None


class MetricValue(BaseModel):
    value: Union[float, int, str, bool]
    unit: Optional[str] = None
    timestamp: Optional[datetime] = None


class ErrorInfo(BaseModel):
    message: str
    severity: ErrorSeverity
    timestamp: datetime
    code: Optional[str] = None


class ClaimedBy(BaseModel):
    session_id: str
    owner: str
    expires_at: datetime


class EquipmentStatus(BaseModel):
    protocol_version: str = PROTOCOL_VERSION
    equipment_id: str = "ot2"
    equipment_name: str = "Opentrons OT-2"
    equipment_kind: EquipmentKind = "liquid_handler"
    equipment_version: Optional[str] = None
    host: Optional[str] = None
    equipment_status: EquipmentState
    message: Optional[str] = None
    required_actions: List[str] = Field(default_factory=list)
    allowed_actions: List[str] = Field(default_factory=list)
    device_time: datetime
    uptime_seconds: Optional[float] = None
    components: Dict[str, ComponentStatus] = Field(default_factory=dict)
    metrics: Dict[str, MetricValue] = Field(default_factory=dict)
    last_error: Optional[ErrorInfo] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class ProbeResponse(BaseModel):
    equipment_id: str
    equipment_name: str
    protocol_version: str


class HealthResponse(BaseModel):
    status: Literal["healthy"] = "healthy"


class ClaimRequest(BaseModel):
    owner: str
    session_id: str
    ttl_s: float = 30.0


class ClaimResponse(BaseModel):
    claim_token: str
    heartbeat_interval_s: float
    expires_at: datetime


class ClaimRejection(BaseModel):
    detail: str
    claimed_by: Optional[ClaimedBy] = None
    retry_after_s: Optional[float] = None


class CommandResponse(BaseModel):
    ok: bool = True
    message: Optional[str] = None
    state: Optional[str] = None


class StartupRequest(BaseModel):
    host_alias: Optional[str] = None
    password: str = ""
    simulation: bool = False


class ProtocolSetupRequest(BaseModel):
    labware: List[Dict[str, Any]] = Field(default_factory=list)
    instruments: List[Dict[str, Any]] = Field(default_factory=list)
    modules: List[Dict[str, Any]] = Field(default_factory=list)


class WellLocation(BaseModel):
    labware_nickname: str
    position: str
    top: Optional[float] = None
    bottom: Optional[float] = None
    center: bool = False


class LiquidMoveRequest(BaseModel):
    pipette: str
    volume_ul: float
    location: WellLocation


class TipRequest(BaseModel):
    pipette: str
    labware_nickname: Optional[str] = None
    position: Optional[str] = None


class MoveLabwareRequest(BaseModel):
    labware_nickname: str
    new_location: str


class LightsRequest(BaseModel):
    on: bool


# ---------------------------------------------------------------------------
# Per-well sample / plate tracking
#
# Mirrors the agilent-cytation-server contract (WellSample / LoadedPlate) so a
# plate's orchestrator-owned state round-trips cleanly across devices: the
# workflow hydrates each device on plate.load with the previous device's
# `wells`, and reads `details.loaded_plate` back from /status. Ownership split:
# the orchestrator owns `plate_id` / `sample_id` / `notes`; the device owns
# `volume_ul` and `loaded_at`. See agilent-cytation-server/docs/PLATE_STATE.md.
# ---------------------------------------------------------------------------


WellId = str  # "A1" .. "H12"


class WellSample(BaseModel):
    """One well of the currently-loaded plate."""

    well: WellId
    sample_id: Optional[str] = None
    volume_ul: Optional[float] = Field(default=None, ge=0.0)
    notes: Optional[str] = None


class LoadedPlate(BaseModel):
    """The plate the orchestrator currently considers loaded on the deck.

    ``model`` is a free-form labware identifier (typically an Opentrons
    ``load_name`` such as ``corning_96_wellplate_360ul_flat``). ``plate_id``
    is an orchestrator-assigned identifier (typically a barcode or
    run-prefixed UUID). Surfaced under ``EquipmentStatus.details.loaded_plate``.
    """

    plate_id: str
    model: str
    loaded_at: datetime
    wells: List[WellSample] = Field(default_factory=list)


class PlateLoadRequest(BaseModel):
    plate_id: str = Field(..., min_length=1, max_length=128)
    model: str = Field(..., min_length=1)
    wells: Optional[List[WellSample]] = None  # defaults to 96 empty wells


class WellUpdateRequest(BaseModel):
    well: WellId = Field(..., min_length=2, max_length=3, description="e.g. A1, H12")
    sample_id: Optional[str] = None
    volume_ul: Optional[float] = Field(default=None, ge=0.0)
    notes: Optional[str] = None
    clear_sample_id: bool = False
    clear_notes: bool = False
