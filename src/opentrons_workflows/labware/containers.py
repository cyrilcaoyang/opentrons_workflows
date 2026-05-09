"""Stable labware/container models.

These models describe plates, tip racks, and transient pipette contents outside
of the volatile Opentrons Python session. They are intentionally small and can
later be persisted by a workflow or inventory service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional


@dataclass(frozen=True)
class Location:
    """Physical location of a container in the lab."""

    device_id: str
    slot: Optional[str] = None


@dataclass(frozen=True)
class WellRef:
    """Stable reference to a well in a tracked container."""

    container_id: str
    well: str


@dataclass
class LiquidEntry:
    """One contribution to a well's current liquid mixture."""

    volume_ul: float
    sample_id: Optional[str] = None
    reagent: Optional[str] = None
    added_at: Optional[datetime] = None


@dataclass
class Well:
    """Logical well state for plates and tip racks."""

    name: str
    max_volume_ul: Optional[float] = None
    contents: List[LiquidEntry] = field(default_factory=list)
    has_tip: Optional[bool] = None
    last_event_at: Optional[datetime] = None

    @property
    def current_volume_ul(self) -> float:
        return sum(entry.volume_ul for entry in self.contents)


@dataclass
class Container:
    """Stable identity for labware that can move between devices."""

    id: str
    load_name: str
    location: Location
    wells: Dict[str, Well]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_moved_at: Optional[datetime] = None

    def move_to(self, location: Location, *, at: Optional[datetime] = None) -> None:
        self.location = location
        self.last_moved_at = at or datetime.now(timezone.utc)


@dataclass
class PipetteState:
    """Transient state for one mounted pipette."""

    mount: Literal["left", "right"]
    name: str
    max_volume_ul: float
    has_tip: bool = False
    current_volume_ul: float = 0.0
    tip_source: Optional[WellRef] = None
    aspirated_from: Optional[WellRef] = None
