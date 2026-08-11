"""Models for the OT-2 gateway.

The AC equipment-status wire types are **not** defined here: they come from
the shared ``sdl-lab-contract`` package (versioned in lockstep with
``ac-organic-lab/docs/STATUS_SPEC.md``) and are re-exported so every
``from .models import ...`` in this repo keeps working. What stays local is
this gateway's own domain vocabulary — protocol-command request bodies,
plate/tip tracking, and the normalized deck model.

Conformance: this gateway conforms to **lab status spec v1.2**. ``activity``
/ ``activity_since`` are observed from the control plane (a protocol command
in flight, or a robot-server run the gateway deferred to), and
``metrics["cycles_total"]`` counts completed protocol commands so a 60 s
poller can still account for work it slept through (§2.3.1). See the README
for what "primary operation" means for this device.

``PROTOCOL_VERSION`` below is the version THIS device speaks; it deliberately
overrides the package default ("1.0" — the honest reading of a device that
does not state one).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union, get_args

from pydantic import BaseModel, Field, model_validator

from sdl_lab_contract import (
    Activity,
    ClaimedBy,
    ClaimRejection,
    ClaimRequest,
    ClaimResponse,
    ComponentStatus,
    EquipmentKind,
    EquipmentState,
    EquipmentStatus,
    ErrorInfo,
    ErrorSeverity,
    HealthResponse,
    MetricValue,
    ProbeResponse,
)

PROTOCOL_VERSION = "1.2"

# This gateway's kind, passed on every /status envelope (the shared model
# requires it rather than defaulting, so a device can never mis-declare by
# omission).
EQUIPMENT_KIND: EquipmentKind = "liquid_handler"


# ---------------------------------------------------------------------------
# `last_error.code` taxonomy (STATUS_SPEC best practice #6)
#
# Closed, small, and defined once here so a client can branch on the code and
# offer a targeted recovery hint. It is deliberately NOT per-command: this
# gateway used to emit `f"{name}_failed"` for every one of its ~15 protocol
# commands, an open-ended space nobody could enumerate, let alone branch on —
# and `aspirate_failed` vs `dispense_failed` implies no difference in recovery.
# *Which* command failed stays where it belongs: in `last_error.message`, and
# in the `control_action` audit row the events exporter writes.
#
# Recovery, per code:
#   startup_failed           - the gateway could not reach or initialise the
#                              robot. Fix connectivity, then POST /control/startup.
#   snapshot_failed          - a deck/labware read failed. Non-blocking (severity
#                              `warning`); /status serves the last good snapshot,
#                              whose age is in `details.snapshot_age_s`.
#   command_failed           - the robot rejected or failed a protocol command.
#                              The command did not take effect; safe to retry.
#   command_transport_failed - the link dropped during an *idempotent* command.
#                              Effect is unknown but harmless to repeat.
#   command_unknown_outcome  - the link dropped during a NON-idempotent command
#                              (aspirate/dispense/tips). Whether it happened is
#                              genuinely unknowable; needs operator
#                              reconciliation, never an automatic retry.
# ---------------------------------------------------------------------------

ErrorCode = Literal[
    "startup_failed",
    "snapshot_failed",
    "command_failed",
    "command_transport_failed",
    "command_unknown_outcome",
]

ERROR_CODES: frozenset[str] = frozenset(get_args(ErrorCode))


class GatewayClaimRequest(ClaimRequest):
    """``POST /control/claim`` body — the spec's, plus one local field.

    ``takeover`` asks to supersede a claim **already held by the same owner**:
    the operator's other tab, or the one they just reloaded (a reload drops the
    token, stranding a live claim nobody can heartbeat or release until its TTL
    runs out). Never grants a claim held by a different owner — an agent
    mid-plan or the dashboard is refused as usual, with 409.

    Additive and defaulted, so a spec-shaped body from ``lab-skills`` or the
    dashboard validates unchanged and never takes over by accident. Not part of
    STATUS_SPEC §5; see the README's claim section.
    """

    takeover: bool = False


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
    # Tip-tracking fields. Omitting `position` on a tracked rack auto-picks the
    # next available tip; omitting `labware_nickname` too auto-selects a
    # tracked, tip-size-compatible rack (slot order). An untracked rack still
    # needs an explicit `position`. `sample_id` allows same-sample tip reuse;
    # `force` overrides the contamination guard (never an empty well).
    sample_id: Optional[str] = None
    force: bool = False


class TipsResetRequest(BaseModel):
    """(Re)register a tip rack with every tip fresh — a physical rack swap.

    Addressed by deck ``slot``, which is a tip rack's identity: a rack carries
    no sample and no history worth naming, and what an operator refills is "the
    rack in slot 4". ``nickname`` is accepted as a legacy alias and resolved
    through the session recipe when one exists.
    """

    slot: Optional[str] = Field(default=None, min_length=1)
    nickname: Optional[str] = Field(default=None, min_length=1)
    wells: Optional[List[str]] = None  # defaults to the 96-tip column-major grid

    @property
    def target(self) -> str:
        ref = self.slot or self.nickname
        if not ref:
            raise ValueError("tips/reset needs a slot")
        return ref

    @model_validator(mode="after")
    def _needs_a_target(self) -> "TipsResetRequest":
        if not (self.slot or self.nickname):
            raise ValueError("provide slot (preferred) or nickname")
        return self


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

    ``nickname`` is the name the setup recipe gave this slot's labware — the key
    every nickname-addressed surface uses (``details.tip_racks``, the
    ``/control/*`` ``labware_nickname`` argument). It is a property of the
    *slot*, so :func:`~opentrons_server.gateway.deck.build_deck` stamps it
    regardless of which source won the slot; ``display_name`` cannot stand in
    for it, because a run/REPL source overwrites that with the robot's own
    display name.
    """

    kind: LabwareKind
    load_name: str
    display_name: Optional[str] = None
    is_tiprack: bool = False
    rows: Optional[int] = None
    columns: Optional[int] = None
    plate_id: Optional[str] = None
    wells: Optional[List[WellSample]] = None
    nickname: Optional[str] = None


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
