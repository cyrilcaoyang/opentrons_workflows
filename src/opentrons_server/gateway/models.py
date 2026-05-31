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
