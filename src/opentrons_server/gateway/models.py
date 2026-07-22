"""Models for the OT-2 gateway and AC equipment status contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


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


class CoordinateLocation(BaseModel):
    """Absolute deck coordinates in mm (the robot's deck reference frame)."""

    x: float
    y: float
    z: float


class MoveToRequest(BaseModel):
    """Move a pipette to a well or to absolute deck coordinates (no liquid).

    Exactly one of ``location`` (well-addressed, same shape as aspirate/dispense)
    or ``coordinates`` (absolute deck frame, mm) must be provided.
    """

    pipette: str
    location: Optional[WellLocation] = None
    coordinates: Optional[CoordinateLocation] = None
    # Straight-line speed for this move in mm/s; omit for the robot default.
    speed: Optional[float] = Field(default=None, gt=0.0)
    # Move in a straight line instead of the arced safe path. The caller owns
    # collision avoidance when set.
    force_direct: bool = False
    # Minimum Z height (mm) for the arced path.
    minimum_z_height: Optional[float] = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "MoveToRequest":
        if (self.location is None) == (self.coordinates is None):
            raise ValueError("provide exactly one of 'location' or 'coordinates'")
        return self


class LiquidMoveRequest(BaseModel):
    pipette: str
    volume_ul: float
    location: WellLocation
    # Flow rate in µL/s. Optional: omit to use the transport's default (the
    # pipette's protocol-API default on SSH; the OT2_HTTP_*_FLOW_UL_S env default
    # on the run-engine HTTP transport, which has no implicit pipette default).
    flow_rate: Optional[float] = Field(default=None, gt=0.0)


class TipRequest(BaseModel):
    pipette: str
    labware_nickname: Optional[str] = None
    position: Optional[str] = None
    # Tip-tracking fields; meaningful only when labware_nickname names a
    # registered (tracked) tip rack. Omitting `position` on such a rack
    # auto-picks the next available tip. `sample_id` allows same-sample tip
    # reuse; `force` overrides the contamination guard (never an empty well).
    sample_id: Optional[str] = None
    force: bool = False


class TipsResetRequest(BaseModel):
    """(Re)register a tip rack with every tip fresh — a physical rack swap."""

    nickname: str = Field(..., min_length=1)
    wells: Optional[List[str]] = None  # defaults to the 96-tip column-major grid


class TipRackState(BaseModel):
    """Tracked tip statuses for one rack: well -> "new" | "empty" | sample id."""

    nickname: str
    tips: Dict[str, str]
    registered_at: datetime


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


# ---------------------------------------------------------------------------
# Deck / labware state (Phase 0: model only — normalizers + merge live in
# gateway/deck.py; wiring into /status is Phase 1+). See docs/DECK_STATE.md.
#
# The normalized deck is the single, provenance-tagged view of "what is on each
# of the 12 OT-2 slots", merged from up to three sources (run > repl > declared).
# `kind` is derived, never hand-set; the tile renders any grid off rows/columns.
# ---------------------------------------------------------------------------


LabwareKind = Literal[
    "96-well",
    "384-well",
    "48-well",
    "24-well",
    "12-well",
    "6-well",
    "well_plate",  # a wellPlate whose grid isn't one of the named sizes
    "tiprack",
    "reservoir",
    "tuberack",
    "trash",
    "adapter",
    "unknown",
]

# Per-slot lifecycle. See docs/DECK_STATE.md (slot-state decision table) for the decision table.
SlotState = Literal["empty", "declared", "occupied", "in_use", "mismatch"]

# Which source won a slot / the deck as a whole.
DeckSource = Literal["run", "repl", "declared", "empty"]


class SlotLabware(BaseModel):
    """Normalized labware on one deck slot.

    ``plate_id`` / ``wells`` are populated only for the orchestrator-tracked
    plate (unified from :class:`PlateStateStore`), never stored twice.
    """

    kind: LabwareKind
    load_name: str
    display_name: Optional[str] = None
    is_tiprack: bool = False
    rows: Optional[int] = None
    columns: Optional[int] = None
    plate_id: Optional[str] = None
    wells: Optional[List[WellSample]] = None


class SlotModule(BaseModel):
    """A hardware module occupying a slot (temperature, magnetic, heater-shaker)."""

    module_name: str
    status: Optional[str] = None
    serial_number: Optional[str] = None


class DeckSlot(BaseModel):
    labware: Optional[SlotLabware] = None
    module: Optional[SlotModule] = None
    slot_state: SlotState = "empty"
    source: DeckSource = "empty"
    # Populated only on a mismatch: the declared intent that lost to the
    # observed labware, so the operator can see the conflict.
    declared: Optional[SlotLabware] = None


class DeckState(BaseModel):
    source: DeckSource
    slots: Dict[str, DeckSlot]  # always all of "1".."12"
    timestamp: datetime


class DeckDeclareRequest(BaseModel):
    """Operator/recipe-declared layout (Phase 2 endpoint payload).

    Each slot value is a labware ``load_name`` (preferred, full fidelity), a
    bare ``kind`` string (legacy dashboard compat), a **module** (a module-kind
    string like ``"temperature_module"`` or a dict with ``module_name``), or
    ``null`` to clear that slot. An empty ``slots`` map clears the whole
    declaration. Declared modules are sticky fixtures; movable modules flow
    through the live run instead.
    """

    slots: Dict[str, Optional[Union[str, Dict[str, Any]]]] = Field(default_factory=dict)


class WellUpdateRequest(BaseModel):
    well: WellId = Field(..., min_length=2, max_length=3, description="e.g. A1, H12")
    sample_id: Optional[str] = None
    volume_ul: Optional[float] = Field(default=None, ge=0.0)
    notes: Optional[str] = None
    clear_sample_id: bool = False
    clear_notes: bool = False
