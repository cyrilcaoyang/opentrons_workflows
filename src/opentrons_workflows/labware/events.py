"""Append-only labware event models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .containers import Location, WellRef


@dataclass(frozen=True)
class LabwareEvent:
    """Base event for reconstructing labware and sample state."""

    by: str
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class ContainerMoved(LabwareEvent):
    container_id: str = ""
    from_location: Optional[Location] = None
    to_location: Optional[Location] = None


@dataclass(frozen=True)
class TipPickedUp(LabwareEvent):
    tip: Optional[WellRef] = None
    pipette: str = ""


@dataclass(frozen=True)
class TipDropped(LabwareEvent):
    pipette: str = ""
    destination: Optional[str] = None


@dataclass(frozen=True)
class Aspirated(LabwareEvent):
    source: Optional[WellRef] = None
    volume_ul: float = 0.0
    pipette: str = ""


@dataclass(frozen=True)
class Dispensed(LabwareEvent):
    destination: Optional[WellRef] = None
    volume_ul: float = 0.0
    pipette: str = ""
    source_lineage: List[WellRef] = field(default_factory=list)


@dataclass(frozen=True)
class Reconciled(LabwareEvent):
    reason: str = ""
    observed_state: Dict = field(default_factory=dict)
